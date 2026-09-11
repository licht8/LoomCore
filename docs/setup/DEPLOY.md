# Деплой LoomHub на VDS

## Требования

- Linux-сервер с Docker Engine и Docker Compose Plugin
- DNS-запись домена на IP сервера
- Открытые порты `80` и `443`
- Не менее 1 GB RAM; для сборки frontend желательно добавить 1-2 GB swap

## Первый запуск

```bash
git clone git@github.com:USER/REPOSITORY.git
cd LoomCore
cp .env.vds.example .env
chmod 600 .env
nano .env
```

### Шаг 1: Установка пакетов на хосте

```bash
sudo bash deploy/install.sh
```

Этот скрипт:
- Устанавливает `wireguard-tools`, `iproute2`, `qrencode`, `iptables`, `nftables` на хосте
- Создает `.env` если его нет
- Собирает и запускает Docker-контейнеры через `docker compose up -d --build`
- Показывает статус контейнеров

После выполнения контейнеры запускаются, но WireGuard-сервер ещё не настроен.

### Альтернативный ручной запуск

```bash
sudo mkdir -p /opt/loomhub
sudo git clone git@github.com:USER/REPOSITORY.git /opt/loomhub
cd /opt/loomhub
# Установите пакеты вручную:
# apt-get update && apt-get install -y wireguard-tools iproute2 qrencode iptables nftables
cp .env.vds.example .env
chmod 600 .env
docker compose up -d --build
```

### Шаг 1.5: Применение миграций БД

Схема БД управляется только через Alembic. `docker-compose.yml` **не запускает
миграции автоматически**, поэтому после первого старта БД выполните:

```bash
docker compose exec hub alembic upgrade head
```

Повторяйте этот шаг после каждого обновления, добавляющего миграции.

### Шаг 2: Настройка WireGuard-сервера

После запуска хаб подключает плагин loomwg. Для настройки сервера:

1. Откройте Web UI на `http://SERVER_IP/` или используйте API `/api/chat`
2. Вызовите инструмент `wg_setup_server` — это создаст конфигурацию `/etc/wireguard/wg0.conf`, включит IPv4/IPv6 forwarding и настроит firewall/NAT **внутри контейнера**
3. Инструмент требует подтверждения администратора (approval)

## Управление сервером через плагины

Backend автоматически запускает каждый плагин, у которого есть файл
`plugins/<name>/main.py`. Инструменты всех таких плагинов передаются в LLM и
маршрутизируются обратно владельцу инструмента.

Self-hosted compose использует принцип минимальных привилегий:

- Контейнер `hub` работает **без `privileged: true`**
- Контейнер имеет только необходимые capabilities: `NET_ADMIN`, `NET_RAW`, `SYS_MODULE`
- Монтируются только нужные тома:
  - `/etc/wireguard:/etc/wireguard` (rw) — конфиги WireGuard
  - `/lib/modules:/lib/modules:ro` — модули ядра
- **Не монтируется:** `/:/host`, `/var/run/docker.sock`, `/sys`, `pid: host`
- **Не используется:** `nsenter`, `chroot`

Это означает, что даже при компрометации плагина или LLM-инъекции, доступ к хосту ограничен управлением WireGuard.

Read-only-инструменты и создание клиентов `loomwg` выполняются внутри
контейнера с ограниченными правами. `wg_create_client` сохраняет client config и
защищённый QR-файл в `/etc/wireguard/clients` с правами `600`; private key не
возвращается в чат и не сохраняется в истории сообщений. Для QR-генерации в
образ включён `qrencode`.
Для создания клиента достаточно передать имя: адрес выбирается автоматически
из свободных адресов сети интерфейса, а публичный IP сервер определяется
автоматически. Если нужен домен или внешний IP нельзя определить из контейнера,
задайте в `.env` `LOOMWG_ENDPOINT` (например, `vpn.example.com`) или передайте
endpoint в отдельном вызове инструмента.

Настройка серверной конфигурации в full-tunnel режиме (инструмент `wg_setup_server`)
определяет ОС, внешний интерфейс по default route и доступный firewall
(`firewalld`, `ufw`, `nftables` или `iptables`) **внутри контейнера**. После
подтверждения администратора она включает IPv4/IPv6 forwarding через
`/etc/sysctl.d/99-loomwg-forwarding.conf`, открывает UDP-порт WireGuard и
настраивает NAT с помощью `iptables`/`nftables` внутри контейнера.
Если firewall не поддерживается, операция завершается ошибкой с требованием
настроить его вручную.

Доступ к Hub защищён административной авторизацией. Поддерживаются два режима
(`src/core/security.py:require_admin_token`):

1. **Bearer-токен:** задайте в `.env` `ADMIN_TOKEN` длинным случайным значением.
   Токен передаётся в query-параметре `access_token` или заголовке
   `Authorization: Bearer <token>`. В UI на странице входа переключитесь на
   вкладку «Токен» и введите его — он сохранится в `localStorage` браузера.
2. **JWT-cookie (рекомендуется):** задайте одновременно `ADMIN_PASSWORD_HASH`,
   `ADMIN_TOTP_SECRET` и `SESSION_SECRET`. В UI используется вкладка
   «Логин и TOTP». Хеш пароля генерируется командой:
   ```bash
   python -m src.auth 'ваш-пароль'
   ```

Не публикуйте токены и не оставляйте доступ не настроенным. WebSocket, чат,
список инструментов и скачивание клиентских файлов (`/api/artifacts/...`) требуют
авторизации.

## Обновление

```bash
cd /opt/loomhub  # или ваша рабочая директория
git pull
docker compose up -d --build
docker image prune -f
```

## HTTPS

Текущий Nginx-конфиг поднимает HTTP и корректно проксирует WebSocket. Для
production нужно добавить TLS-сертификат (например, Certbot) и перевести
серверный блок на `listen 443 ssl`, а HTTP перенаправить на HTTPS. После этого
frontend автоматически использует `wss://`.

## Резервное копирование

Данные PostgreSQL находятся в Docker volume `pgdata`:

```bash
docker compose exec -T db pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
```

Файл `.env` и `backup.sql` не должны попадать в Git.

## Безопасность контейнера

| Действие | Было | Стало |
|---|---|---|
| `wg show/add-peer` | `privileged: true` | `NET_ADMIN` capability |
| `wg-quick up/down` | `privileged` | `NET_ADMIN` + `SYS_MODULE` |
| `/etc/wireguard` | через `/:/host` | прямой mount volume |
| Установка пакетов | `chroot /host` | только `deploy/install.sh` на хосте |
| Firewall/NAT | `chroot /host` | внутри контейнера (iptables/nft) |
| Полный root к хосту | ✅ | ❌ УБРАНО |
| Docker socket | ✅ | ❌ УБРАНО |
| PID namespace | ✅ | ❌ УБРАНО |
