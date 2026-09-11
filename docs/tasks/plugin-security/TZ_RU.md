# ТЗ: Ограничение прав контейнера hub для безопасности

## Цель

Устранить критический риск безопасности: контейнер `hub` работает с `privileged: true` и монтирует весь хост (`/:/host`), что даёт плагину loomwg полный root-доступ к серверу. Заменить на принцип минимальных привилегий — контейнер получает ровно те права, которые нужны для работы WireGuard.

## Контекст

- **Плагин loomwg** — subprocess внутри контейнера hub, общается через MCP over stdio.
- **Инструменты плагина** (tools.py): 13 инструментов для управления WireGuard (статус, пировы, установка, создание клиентов, запуск/останов).
- **Проблема:** `privileged: true` + `/:/host` = полный root к хосту. Любая уязвимость в плагине или LLM-инъекция = контроль над сервером.
- **RULES.md п.10:** «Ядро — это только транспорт и мозг. Вся системная логика живёт в Плагинах.» Но плагин не должен иметь прав root к хосту без крайней нужды (RULES.md п.7).

---

## Анализ: что реально нужно плагину

Проанализированы все вызовы subprocess в плагине loomwg:

### wireguard.py

| Команда | Зачем | Права нужны |
|---|---|---|
| `wg show interfaces` | Показать активные интерфейсы | `NET_ADMIN` |
| `wg show <iface> dump` | Показать пиры и трафик | `NET_ADMIN` |
| `wg show <iface>` (human) | Fallback парсинг пиров | `NET_ADMIN` |
| `ip link show <iface>` | Проверить наличие link | `NET_ADMIN` |
| `wg pubkey` | Вычислить публичный ключ из приватного | `NET_ADMIN` |
| `wg set ... peer ... allowed-ips` | Добавить/изменить пира | `NET_ADMIN` |
| `wg set ... peer ... remove` | Удалить пира | `NET_ADMIN` |
| `wg genpsk` | Сгенерировать preshared key | **не нужны** привилегии (только crypto) |
| `wg --version` | Проверить установку | **не нужны** привилегии |

### lifecycle.py

| Команда | Зачем | Права нужны |
|---|---|---|
| `wg-quick up <iface>` | Запустить интерфейс WireGuard | `NET_ADMIN` + загрузка модуля ядра |
| `wg-quick down <iface>` | Остановить интерфейс | `NET_ADMIN` |

### install.py

| Команда | Зачем | Права нужны |
|---|---|---|
| `apt-get install` / `dnf install` / `yum install` / `apk add` | Установка wireguard-tools | **root (host)** |
| `firewall-cmd / iptables / nft / ufw` | Настройка firewall и NAT | **root (host)** |
| `sysctl --system` | Включение IPv4/IPv6 forwarding | **root (host)** |
| `chroot /host /bin/sh` | Выполнение команд на хосте | **`/:/host` mount** |

### config.py

| Операция | Зачем | Права нужны |
|---|---|---|
| `Path.read_text()` | Чтение конфига WireGuard | `r` на `/etc/wireguard` |
| `os.fdopen(fd, "w")` | Запись конфига WireGuard | `w` на `/etc/wireguard` |
| `os.chmod(path, 0o600)` | Запрет доступа к конфигам | тот же пользователь |

### client.py

| Команда | Зачем | Права нужны |
|---|---|---|
| `wg set ... peer ... allowed-ips ... preshared-key` | Добавить пира клиента | `NET_ADMIN` |
| `qrencode -o <path> -t png` | Генерация QR-кода | **не нужны** привилегии |
| `Path.read_bytes()` | Чтение файла конфига | `r` на `/etc/wireguard/clients` |
| `Path.write_text()` + `os.chmod(0o600)` | Сохранение конфига клиента | `w` на `/etc/wireguard/clients` |

### ИТОГО по файлам

| Директория/Файл | Что нужно | Права |
|---|---|---|
| `/etc/wireguard/*.conf` | Серверные конфиги | **rw** (config.py, client.py) |
| `/etc/wireguard/clients/` | Клиентские конфиги | **rw** (client.py) |
| `/lib/modules` | Модули ядра (wg-quick) | **ro** (уже ro) |
| Команды `wg`, `ip`, `wg-quick` | Управление WireGuard | `NET_ADMIN` + `SYS_MODULE` |
| Команды `qrencode` | QR-коды | **без привилегий** (уже в Dockerfile) |
| `apt-get` / `dnf` / `yum` / `apk` | Установка пакетов | **root на хосте** (НЕ в контейнере!) |
| `iptables` / `nft` / `firewall-cmd` / `ufw` | Firewall/NAT | **root на хосте** (НЕ в контейнере!) |
| `sysctl` | Включение forwarding | **root на хосте** (НЕ в контейнере!) |

---

## Ключевое решение: разделить install и run

Проблема в том, что `install.py` (install_wireguard, setup_server) вызывает пакетные менеджеры, iptables, nftables, sysctl — это **требует root на хосте**. Но эти операции нужны **только один раз** при первой настройке.

**Решение:** разнести на два шага:
1. **`install.sh` (хост)** — выполняет install + setup_server один раз при деплое.
2. **Плагин (контейнер)** — работает только с уже настроенным WireGuard: статус, пировы, клиенты, lifecycle.

---

## Что менять в docker-compose.yml

### Убрать
| Параметр | Почему убрать |
|---|---|
| `privileged: true` | Даёт ALL capabilities — избыточно. Нужны только NET_ADMIN, NET_RAW, SYS_MODULE. |
| `/:/host` mount | Даёт полный root к файловой системе хоста — избыточно. Нужен только `/etc/wireguard`. |
| `docker.sock` mount | Дает полный контроль над Docker-демоном — не используется плагином. |
| `/sys:/sys` mount | Чтение `/sys` не нужно для WireGuard. |
| `pid: host` | Используется для `nsenter -t 1` в wireguard.py. После разделения install/run — `nsenter` больше не нужен (см. ниже). |

### Оставить
| Параметр | Значение | Зачем |
|---|---|---|
| `cap_add` | NET_ADMIN, NET_RAW, SYS_MODULE | Управление сетевыми интерфейсами, модулями ядра |
| `/etc/wireguard:/etc/wireguard` | rw | Чтение/запись серверных и клиентских конфигов |
| `/lib/modules:/lib/modules:ro` | ro | Поиск модулей ядра wireguard |

### Изменённый docker-compose.yml (секция hub)

```yaml
  hub:
    build: .
    container_name: loom_hub
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    # ❌ Убрать:
    # privileged: true
    # pid: host
    # cap_add:
    #   - NET_ADMIN
    #   - NET_RAW
    #   - SYS_MODULE
    # volumes:
    #   - /:/host
    #   - /sys:/sys
    #   - /var/run/docker.sock:/var/run/docker.sock
    # ✅ Заменить на:
    cap_add:
      - NET_ADMIN
      - NET_RAW
      - SYS_MODULE
    security_opt:
      - no-new-privileges:false
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-loomuser}:${POSTGRES_PASSWORD:-secret_password}@db:5432/${POSTGRES_DB:-loomhub}
      HETZNER_API_KEY: ${HETZNER_API_KEY:-}
      DEBUG: ${DEBUG:-false}
      LOOMHUB_MODE: self-hosted
      LOOMWG_ENDPOINT: ${LOOMWG_ENDPOINT:-}
      ADMIN_TOKEN: ${ADMIN_TOKEN:-}
      ADMIN_USERNAME: ${ADMIN_USERNAME:-admin}
      ADMIN_PASSWORD_HASH: ${ADMIN_PASSWORD_HASH:-}
      ADMIN_TOTP_SECRET: ${ADMIN_TOTP_SECRET:-}
      SESSION_SECRET: ${SESSION_SECRET:-}
    command: uvicorn src.main:app --host 0.0.0.0 --port 8000
    expose:
      - "8000"
    # ✅ Только нужные тома:
    volumes:
      - /etc/wireguard:/etc/wireguard
      - /lib/modules:/lib/modules:ro
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
      interval: 15s
      timeout: 5s
      retries: 5
    logging:
      driver: json-file
      options:
        max-size: 10m
        max-file: "3"
```

---

## Задача 2: Убрать nsenter из wireguard.py

**Где:** `plugins/loomwg/wireguard.py`, функция `run_command()`.

**Проблема:** `nsenter -t 1 -n --` используется для выполнения команд от имени PID 1 (init процесса хоста). Это нужно потому, что контейнер без `pid: host` не имеет доступа к системным вызовам сетевых интерфейсов.

**Но!** После снятия `privileged` и `pid: host` команда `wg`, `ip`, `wg-quick` будут выполняться **внутри контейнера** с `cap_add`. Нет необходимости в `nsenter` — команды выполняются напрямую.

**Что сделать:**

Убрать обёртку `nsenter`:
```python
# Было (wireguard.py, строка 26-28):
host_command = command
if command and command[0] in {"wg", "ip"}:
    host_command = ["nsenter", "-t", "1", "-n", "--", *command]

# Стало:
host_command = command  # выполняем напрямую — контейнер имеет NET_ADMIN
```

Удалить строки 26-28 полностью. Удалить логику fallback (строки 42-50) — `nsenter` больше не используется.

**Изменяемый файл:** `plugins/loomwg/wireguard.py`

---

## Задача 3: Перенести install/setup_server в install.sh

**Где:** `plugins/loomwg/install.py` + `deploy/install.sh`.

**Проблема:** `install_wireguard()` и `setup_server()` внутри плагина вызывают пакетные менеджеры (`apt-get`, `dnf`, `yum`, `apk`), `iptables`, `nftables`, `firewall-cmd`, `ufw` — всё это требует root на хосте.

**Решение:** перенести логику установки в `deploy/install.sh`, который выполняется **на хосте** один раз. Плагин loomwg в контейнере работает только с уже настроенным WireGuard.

### Изменённый deploy/install.sh

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install.sh"
  exit 1
fi

command -v docker >/dev/null 2>&1 || {
  echo "Docker is required. Install Docker Engine and the Compose plugin first."
  exit 1
}

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is required."
  exit 1
fi

install -d -m 0750 /opt/loomhub
cd /opt/loomhub

if [[ ! -f .env ]]; then
  cp .env.vds.example .env
  chmod 600 .env
  echo "Created /opt/loomhub/.env. Fill in HETZNER_API_KEY and POSTGRES_PASSWORD, then rerun."
  exit 0
fi

# === Шаги установки хоста (выполняются на хосте, не в контейнере) ===

echo "=== Установка WireGuard-пакетов ==="
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update && apt-get install -y wireguard-tools iproute2 qrencode iptables nftables
  echo "Установлено через apt"
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y wireguard-tools iproute iptables-nft nftables
  echo "Установлено через dnf"
elif command -v yum >/dev/null 2>&1; then
  yum install -y wireguard-tools iproute iptables nftables
  echo "Установлено через yum"
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache wireguard-tools iproute2 iptables nftables
  echo "Установлено через apk"
else
  echo "Не найден поддерживаемый пакетный менеджер. Установите wireguard-tools вручную."
  exit 1
fi

# === Сборка и запуск Docker-контейнеров ===
echo "=== Сборка и запуск LoomHub ==="
docker compose up -d --build
docker compose ps
echo "LoomHub is available on port 80."
echo ""
echo "Для настройки WireGuard-сервера используйте инструмент wg_setup_server"
echo "в чате LoomHub (или через API /api/chat)."
```

### Изменённый install.py (уменьшенный)

```python
"""Install helpers for WireGuard prerequisites.

WARNING: install_wireguard() and setup_server() are now NO-OPs
when running inside the container. They should be run on the host
via deploy/install.sh before starting the container.

To setup the server via the plugin after installation:
    use wg_setup_server tool (requires approval) which handles
    firewall/NAT inside the container using NET_ADMIN capability.
"""

def install_wireguard(_: dict) -> dict:
    return {
        "installed": True,
        "changed": False,
        "package_manager": None,
        "message": "WireGuard установлен на хосте (через install.sh). Контейнер использует уже установленные пакеты.",
    }
```

**Фактически:** `install_wireguard()` в контейнере не нужна — она дублирует `install.sh`. Можно либо сделать NO-OP, либо удалить из `HANDLERS` в `tools.py`.

**Изменяемые файлы:** `deploy/install.sh`, `plugins/loomwg/install.py`.

---

## Задача 4: Убрать setup_server из плагина или заменить на container-safe версию

**Где:** `plugins/loomwg/install.py`, функция `setup_server()`.

**Проблема:** `setup_server()` вызывает `_host_shell()` который использует `nsenter` и `chroot /host` — эти инструменты больше не нужны после снятия `privileged` и `/:/host`.

**Решение:** заменить на версию, которая работает внутри контейнера:
- Конфиг WireGuard записывается в `/etc/wireguard` (уже смонтирован).
- Firewall/NAT настраивается через `iptables` / `nft` внутри контейнера — с `cap_add NET_ADMIN` это работает (хотя нужно убедиться, что `iptables` и `nft` есть в Dockerfile).
- `sysctl` настраивается через файл `/etc/sysctl.d/99-loomwg-forwarding.conf` (создаётся в контейнере) + `sysctl --system`.

**Что сделать:** переписать `setup_server()` так, чтобы она выполняла всё **внутри контейнера** без `nsenter`/`chroot`:

```python
def _container_shell(command: str, timeout: int = 300) -> str:
    """Выполнить команду внутри контейнера (без nsenter)."""
    result = subprocess.run(
        ["/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        details = result.stderr.strip() or result.stdout.strip() or "нет деталей"
        raise RuntimeError(f"Команда завершилась с кодом {result.returncode}: {details}")
    return result.stdout.strip()

def _configure_firewall(...) -> str:
    """Настроить firewall внутри контейнера."""
    # firewalld, ufw, nftables, iptables — выполняем без nsenter
    ...

def _enable_forwarding() -> None:
    """Включить forwarding через sysctl.d внутри контейнера."""
    _container_shell(
        "printf 'net.ipv4.ip_forward=1\\nnet.ipv6.conf.all.forwarding=1\\n' > "
        "/etc/sysctl.d/99-loomwg-forwarding.conf && sysctl --system"
    )
```

Или проще: оставить `setup_server()` как есть, но заменить `_host_shell` на `_container_shell`.

**Изменяемый файл:** `plugins/loomwg/install.py`.

---

## Задача 5: Добавить iptables/nftables в Dockerfile

**Где:** `Dockerfile`.

**Проблема:** `setup_server()` использует `iptables` и `nftables` для настройки firewall/NAT внутри контейнера. Эти инструменты должны быть установлены в Docker-образе.

**Что сделать:**

```dockerfile
# Было:
RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 util-linux wireguard-tools qrencode \
    && rm -rf /var/lib/apt/lists/*

# Стало:
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        util-linux \
        wireguard-tools \
        qrencode \
        iptables \
        nftables \
    && rm -rf /var/lib/apt/lists/*
```

**Изменяемый файл:** `Dockerfile`.

---

## Задача 6: Обновить DEPLOY.md

**Где:** `docs/setup/DEPLOY.md`.

**Что сделать:** обновить документацию — описать новый процесс:
1. Запуск `deploy/install.sh` на хосте (установка пакетов).
2. Запуск `docker compose up -d --build` (запуск контейнеров с минимальными правами).
3. Настройка сервера через чат (`wg_setup_server`) — firewall/NAT настраиваются внутри контейнера.

---

## Сводная таблица: что разрешено, что нет

| Действие | Было | Стало | Как |
|---|---|---|---|
| `wg show/status/add-peer/remove-peer` | ✅ privileged | ✅ NET_ADMIN | cap_add |
| `wg-quick up/down` | ✅ privileged | ✅ NET_ADMIN + SYS_MODULE | cap_add |
| Чтение/запись `/etc/wireguard` | ✅ `/:/host` | ✅ прямой mount | volume mount |
| Генерация QR-кодов | ✅ | ✅ | qrencode в образе |
| Установка пакетов (apt/dnf/yum) | ✅ chroot /host | ⚠️ только install.sh на хосте | вне контейнера |
| Настройка firewall (iptables/nft) | ✅ chroot /host | ✅ внутри контейнера | cap_add + установленные пакеты |
| Настройка sysctl (forwarding) | ✅ chroot /host | ✅ файл в sysctl.d + sysctl --system | внутри контейнера |
| Полный root к хосту | ✅ privileged | ❌ | УБРАНО |
| Полный доступ к файловой системе | ✅ `/:/host` | ❌ | УБРАНО |
| Полный доступ к Docker | ✅ docker.sock | ❌ | УБРАНО |
| PID namespace хоста | ✅ pid: host | ❌ | УБРАНО |
| nsenter к PID 1 | ✅ | ❌ | УБРАНО |

---

## Порядок выполнения

1. **Dockerfile** — добавить `iptables`, `nftables`
2. **docker-compose.yml** — убрать `privileged`, `/:/host`, `docker.sock`, `/sys`, `pid: host`. Оставить `cap_add` + нужные volumes
3. **wireguard.py** — убрать `nsenter`, выполнить команды напрямую
4. **install.sh** — добавить шаги установки пакетов
5. **install.py** — заменить `_host_shell` на `_container_shell`, убрать `chroot`/`nsenter`
6. **DEPLOY.md** — обновить документацию

## Неизменяемые файлы

- ❌ Не трогать `src/` (ядро), `frontend/`, `plugins/loomwg/tools.py` (список инструментов), `plugins/loomwg/client.py`, `plugins/loomwg/config.py`, `plugins/loomwg/wireguard.py` (кроме nsenter), `plugins/loomwg/main.py`, `plugins/loomwg/protocol.py`, `plugins/loomwg/server.py`, `plugins/loomwg/lifecycle.py`, `plugins/loomwg/keys.py`, `plugins/loomwg/__init__.py` (кроме указанных изменений)

## Критерии приёмки

1. Контейнер hub работает без `privileged: true`, без `/:/host`, без `docker.sock`, без `pid: host`.
2. Контейнер имеет только `cap_add: NET_ADMIN, NET_RAW, SYS_MODULE`.
3. Контейнер монтирует только `/etc/wireguard` (rw) и `/lib/modules` (ro).
4. Команды `wg`, `ip`, `wg-quick` выполняются внутри контейнера напрямую (без `nsenter`).
5. `install.sh` на хосте устанавливает wireguard-tools + iproute2 + qrencode + iptables + nftables.
6. `setup_server()` настраивает firewall/NAT и forwarding внутри контейнера (без `chroot`/`nsenter`).
7. После изменений WireGuard-плагины продолжают работать: статус, пировы, клиенты, lifecycle.
8. Код на русском, комментарии на русском, идентификаторы на английском.

---

*Автор ТЗ: Координатор*
*Дата: 2026-02-14*
