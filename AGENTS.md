# AGENTS.md — LoomHub / LoomCore

Перед любыми правками читать `RULES.md` — это главный свод правил проекта.
Весь чат, комментарии, документация и коммит-месседжи — **на русском** (RULES 0).

## Что это

- **Ядро (`src/`)** — тонкий FastAPI-слой: API, WebSocket, LLM-роутер, авторизация, manager плагинов. Точка входа — `src.main:app` (`uvicorn src.main:app`), но `main.py` только вызывает `create_app()` из `src/app.py`; роуты — в `src/api/*` (RULES 10). Корневого `main.py` нет.
- **Плагины (`plugins/<name>/main.py`)** — отдельные subprocess'ы, общаются с ядром по JSON-RPC 2.0 через stdio (MCP). Сейчас есть только `plugins/loomwg` (WireGuard, часть — mock).
- **`frontend/`** — Next.js 16 (standalone), клиент хаба. НЕ трогать, не прочитав `frontend/AGENTS.md` (авто-генерируется Next, не удалять его блок).
- Новая функциональность = новый/правленый **плагин**, а не раздувание `src/` (RULES 1).

## Команды

Backend (корень, используется `uv` + `uv.lock`):
```bash
uv sync                              # или: pip install -e ".[dev]"
pytest                               # asyncio_mode=auto; единичный: pytest tests/test_rate_limit.py::test_rate_limit_429_response_body
pytest -m "not integration and not e2e"   # быстрый unit-прогон (без БД/WireGuard)
ruff check .
uvicorn src.main:app --reload --port 8000
```
Frontend (`frontend/`, используется `npm` — так собирает Dockerfile):
```bash
npm install
npm run dev          # порт 3000
npm run build
```
Полный стек / деплой:
```bash
docker compose up -d --build         # VDS: sudo bash deploy/install.sh (nginx:80, hub/frontend только внутри)
```

## Неочевидные факты

- **Миграции только через Alembic.** Startup (`src/app.py`) и `docker-compose.yml` НЕ запускают миграции. После старта БД вручную: `alembic upgrade head` (`alembic/env.py` берёт URL из `.env`/`DATABASE_URL`).
- `src/app.py` при старте вызывает `plugin_manager.discover_plugins(settings.plugins_dir)` (`PLUGINS_DIR`, default `/app/plugins`) — вне Docker задайте `PLUGINS_DIR=./plugins`, иначе плагины не поднимутся.
- Плагин запускается как `sys.executable <plugins/<name>/main.py>`; `plugins/loomwg/main.py` использует bare-импорты (`from server import ...`), т.е. опирается на sys.path скрипта.
- Каталог конфигов WireGuard у плагина — `LOOMWG_CONFIG_DIR` (default `/etc/wireguard`); в тестах/e2e используется временный каталог.
- Авторизация имеет **два режима** (`src/core/security.py:require_admin_token`): либо `ADMIN_TOKEN` (bearer/query), либо JWT-cookie при заданных `ADMIN_PASSWORD_HASH` + `ADMIN_TOTP_SECRET` + `SESSION_SECRET`. Хеш пароля: `python -m src.auth <пароль>`.
- WS-роут `/ws/{session_id}` не обёрнут slowapi (лимиты — на REST: chat 30/мин, auth 5/мин и т.д.).
- `frontend/next.config.mjs` ставит `typescript.ignoreBuildErrors: true` — ошибки TS не ломают `next build`; проверяйте типы отдельно (`npx tsc --noEmit`).
- Тесты БД не требуют; интеграционный smoke БД — `LOOMHUB_INTEGRATION=1 pytest tests/test_smoke_integration.py`. CI — `.github/workflows/ci.yml` (ruff + pytest + frontend).

## Инварианты проекта (из RULES.md — не нарушать)

- Файл **не удалять** без явного разрешения владельца; при разрешении — переносить в `tmp/_trash/`.
- Работать только внутри проекта; черновики/логи — только в `tmp/` (в `.gitignore`).
- Не трогать чужие Docker-контейнеры; свои ресурсы — с префиксом `loom_`; порт 8000 наружу не публиковать (только через nginx).
- Коммит делает координатор после приёмки; исполнитель не коммитит. Отчёты и задания — в fenced code block (RULES 0a).

## Ссылки

- `RULES.md` — полные правила (читать целиком).
- `docs/setup/DEPLOY.md` — деплой на VDS.
- `docs/setup/TESTING.md` — тесты (WSL2/Docker/VDS), e2e-чек-лист.
- `docs/tasks/<name>/TZ_RU.md` + `REPORT_RU.md` — ТЗ и отчёты по задачам.
