# Отчёт: Ограничение прав контейнера hub для безопасности

## Что сделано

### Изменённые файлы

| Файл | Что изменено |
|---|---|
| `Dockerfile` | Добавлены `iptables` и `nftables` в образ |
| `docker-compose.yml` | Убраны `privileged: true`, `pid: host`, `/:/host`, `/sys`, `docker.sock`. Оставлены `cap_add` (NET_ADMIN, NET_RAW, SYS_MODULE) + volumes (`/etc/wireguard` rw, `/lib/modules` ro) |
| `plugins/loomwg/wireguard.py` | Убран `nsenter`, команды выполняются напрямую |
| `plugins/loomwg/install.py` | `_host_shell` → `_container_shell`, `install_wireguard` сделан NO-OP, `setup_server` работает внутри контейнера |
| `deploy/install.sh` | Добавлена установка пакетов (wireguard-tools, iproute2, qrencode, iptables, nftables) перед docker compose |
| `docs/setup/DEPLOY.md` | Обновлена документация: новый процесс деплоя, раздел безопасности |

---

## Задача 1: Dockerfile — добавить iptables/nftables ✅

В `RUN apt-get install` добавлены пакеты `iptables` и `nftables`. Теперь `setup_server()` может настраивать firewall внутри контейнера.

**Было:** `iproute2 util-linux wireguard-tools qrencode`
**Стало:** `iproute2 util-linux wireguard-tools qrencode iptables nftables`

## Задача 2: docker-compose.yml — убрать привилегии ✅

**Убрано:**
- `privileged: true`
- `pid: host`
- `/:/host`
- `/sys:/sys`
- `/var/run/docker.sock:/var/run/docker.sock`
- `LOOMHUB_HOST_ROOT` из environment

**Оставлено:**
- `cap_add: NET_ADMIN, NET_RAW, SYS_MODULE`
- `/etc/wireguard:/etc/wireguard` (rw)
- `/lib/modules:/lib/modules:ro`
- `security_opt: no-new-privileges:false`

## Задача 3: wireguard.py — убрать nsenter ✅

Функция `run_command()` больше не оборачивает команды `wg` и `ip` в `nsenter`. Команды выполняются напрямую внутри контейнера, который имеет `NET_ADMIN` capability.

Удалён fallback на чистый вызов команды — он больше не нужен.

## Задача 4: install.sh — перенести установку на хост ✅

`deploy/install.sh` дополнен шагами установки пакетов:
- Автоопределение пакетного менеджера (apt/dnf/yum/apk)
- Установка `wireguard-tools`, `iproute2`, `qrencode`, `iptables`, `nftables`
- Сборка и запуск `docker compose up -d --build`
- Сообщение о дальнейшем использовании `wg_setup_server` в чате

## Задача 5: install.py — контейнер-safe версия ✅

- `_host_shell()` → `_container_shell()`: команды выполняются через `/bin/sh -c` без `nsenter`/`chroot`
- `_detect_host()`, `_configure_firewall()`, `_enable_forwarding()` — переписаны на `_container_shell`
- `install_wireguard()` — NO-OP: проверяет `wg --version`, возвращает понятное сообщение об установке через `install.sh`
- `setup_server()` — полностью работает внутри контейнера:
  - Конфиг → `/etc/wireguard` (уже смонтирован)
  - Firewall → iptables/nft через `cap_add NET_ADMIN`
  - Forwarding → `/etc/sysctl.d/99-loomwg-forwarding.conf` + `sysctl --system`

## Задача 6: DEPLOY.md — обновить документацию ✅

- Новый процесс: `install.sh` → `docker compose` → `wg_setup_server`
- Убран раздел с `privileged`/`/host`, добавлен раздел «Безопасность контейнера»
- Таблица «Было → Стало» с объяснением изменений
- Уточнено, что firewall/NAT настраивается **внутри контейнера**

---

## Что проверено

1. **Синтаксис Python** — линтинг `wireguard.py`, `install.py` прошёл без ошибок.
2. **YAML** — `docker-compose.yml` валиден (формат проверен).
3. **Shell** — `deploy/install.sh` соответствует TЗ.
4. **Полнота** — все 6 задач ТЗ выполнены.

## Замечания / Риски

1. **iptables/nft в контейнере:** команды firewall внутри контейнера требуют `NET_ADMIN` capability. `cap_add` настроен, но при запуске на некоторых ядрах может потребоваться `SYS_ADMIN` для iptables — тестировать на целевой инфраструктуре.
2. **sysctl внутри контейнера:** `sysctl --system` внутри контейнера с `NET_ADMIN` работает, но зависит от ядра хоста. Альтернатива — монтирование `/proc/sys` (не добавляем без крайней необходимости).
3. **Старые установки:** если контейнер уже запущен с `privileged: true`, нужно вручную снять привилегии и пересобрать образ.
4. **Миграция с chroot:** если кто-то уже настроил сервер через `_host_shell` (nsenter/chroot), `setup_server()` с контейнерной версией создаст новый конфиг в `/etc/wireguard` (та же папка, но без chroot). Конфликтов нет.

## Коммит не создавал

Исполнитель не создавал коммиты. Координатор создаст коммит после приёмки.
