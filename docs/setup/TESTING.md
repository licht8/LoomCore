# Тестирование LoomHub

Приложение рассчитано на Linux-серверы, поэтому все проверки выполняются в
Linux-окружении: **WSL2 Ubuntu**, **Linux-контейнеры** (Docker Desktop) или
**VDS**. Windows используется только как терминал/редактор — отдельной
поддержки Windows нет.

## Уровни тестов

| Уровень | Маркер | Что проверяет | Где запускать |
|---|---|---|---|
| Unit | — (по умолчанию) | ядро, LLM-роутер, manager, логика loomwg, JSON-RPC протокол | везде (WSL2/CI) |
| Integration | `integration` | доступность Postgres | WSL2/Docker/CI |
| E2E | `e2e` | реальный WireGuard (`wg-quick`) | WSL2/VDS |

## Быстрый цикл (WSL2 Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y wireguard-tools iproute2 qrencode iptables nftables

uv sync --extra dev

# unit-тесты (без БД и WireGuard)
pytest -q -m "not integration and not e2e"

# линтер
ruff check .
```

В WSL2 обычно нет команды `python` (только `python3`); менеджер плагинов
запускает subprocess через `sys.executable`, поэтому это не мешает.

### Postgres (integration)

Проще всего поднять только БД в Docker, а ядро запускать в WSL2:

```bash
docker compose up -d db
export DATABASE_URL="postgresql+asyncpg://loomuser:secret_password@localhost:5432/loomhub"
alembic upgrade head
LOOMHUB_INTEGRATION=1 pytest -q -m integration
```

### Локальный запуск ядра в WSL2

```bash
export PLUGINS_DIR="$PWD/plugins"
export DATABASE_URL="postgresql+asyncpg://loomuser:secret_password@localhost:5432/loomhub"
uvicorn src.main:app --reload --port 8000
```

`PLUGINS_DIR` заменил жёсткий путь `/app/plugins`, поэтому плагины поднимаются
вне контейнера. Для авторизации задайте либо `ADMIN_TOKEN`, либо
`ADMIN_PASSWORD_HASH` + `ADMIN_TOTP_SECRET` + `SESSION_SECRET`
(хеш пароля: `python -m src.auth 'пароль'`).

## E2E WireGuard

Тест создаёт временный каталог с конфигом, поднимает и опускает интерфейс
`wgloomtest` через настоящий `wg-quick`. Нужны Linux и права root:

```bash
sudo LOOMWG_E2E=1 pytest -q -m e2e
```

`LOOMWG_CONFIG_DIR` переопределяет каталог конфигов WireGuard (по умолчанию
`/etc/wireguard`), поэтому рабочий `/etc` не затрагивается.

## Docker Desktop (Linux-контейнеры)

Прод-compose монтирует `/etc/wireguard` и `/lib/modules`, которых нет на
Windows-хосте. Для локальной проверки API/UI используйте dev-override:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
docker compose exec hub alembic upgrade head
```

В этом режиме у `hub` нет capabilities и host-монтирований, поэтому
инструменты `loomwg`, требующие реального WireGuard, не работают — они
проверяются в WSL2 или на VDS.

## Frontend

```bash
cd frontend
npm ci
npx tsc --noEmit   # next.config.mjs игнорирует ошибки TS при build
npm run build
```

## CI

`.github/workflows/ci.yml`:
- `backend` — `ruff check .` + `pytest` (unit);
- `frontend` — `tsc --noEmit` + `next build`;
- `integration` — Postgres-сервис + `pytest -m integration`.

E2E с реальным WireGuard в GitHub-hosted runner не запускается — только
вручную по чек-листу ниже.

## Чек-лист E2E на VDS

1. `sudo bash deploy/install.sh` (пакеты WireGuard на хосте + `docker compose up`).
2. `docker compose exec hub alembic upgrade head`.
3. Настроить авторизацию в `.env` (JWT или `ADMIN_TOKEN`) и перезапустить.
4. Инструмент `wg_setup_server` (с подтверждением admin) — создаёт `wg0.conf`,
   forwarding, firewall/NAT.
5. `wg_create_client` — создать клиента.
6. Скачать конфиг и QR: ссылки `/api/artifacts/<token>/{config,qr}`.
7. Подключить реального клиента (телефон/ПК) по конфигу.
8. `wg_list_peers` — убедиться, что peer `online` (есть handshake).
9. `wg_remove_peer` — проверить удаление пира и клиентских файлов.

## Переменные окружения для тестов

| Переменная | Назначение |
|---|---|
| `PLUGINS_DIR` | каталог плагинов (default `/app/plugins`) |
| `LOOMWG_CONFIG_DIR` | каталог конфигов WireGuard (default `/etc/wireguard`) |
| `LOOMHUB_INTEGRATION=1` | включает integration-тесты (Postgres) |
| `LOOMWG_E2E=1` | включает e2e-тест реального WireGuard |
| `DATABASE_URL` | строка подключения к Postgres |
