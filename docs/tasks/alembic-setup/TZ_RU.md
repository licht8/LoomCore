# ТЗ: Инициализация Alembic-миграций

## Цель

Заменить автоинициализацию БД через `Base.metadata.create_all()` на Alembic-миграции. Согласно RULES.md (п.6): «Схема БД управляется через **SQLAlchemy** + **Alembic** (миграции)». Сейчас миграций нет, `alembic` не установлен, БД создаётся при старте хабом через `init_db()`.

## Контекст

- **Модели БД готовы:** 3 таблицы в `src/db/models.py` — `User`, `Session`, `Message`.
- **Текущий подход:** `database.py:init_db()` вызывает `Base.metadata.create_all()` при старте хабом — создаёт таблицы, если не существуют.
- **Проблема:** Нет версионирования схемы БД, нет `upgrade`/`downgrade`, нет истории изменений. При любом изменении модели старая БД не обновится автоматически.
- **Правила:** Соблюдать RULES.md (код на русском, изоляция, комментарии, один файл = одна ответственность).
- **Рабочее пространство:** `docs/tasks/alembic-setup/TZ_RU.md` и `docs/tasks/alembic-setup/REPORT_RU.md`.

## Текущее состояние

### Модели БД (`src/db/models.py`)

```
users (id, identifier, provider, role)
  └─ sessions (id, user_id, status)
       └─ messages (id, session_id, role, content, tool_calls JSONB, tool_result, created_at)
```

### Текущая инициализация (`src/db/database.py`)
```python
async def init_db():
    from src.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database initialized")
```

Вызывается в `main.py:startu p()` (строка 105).

### Зависимости (`pyproject.toml`)
- `alembic` — **не установлен**
- В `[project.optional-dependencies] dev` — `pytest`, `pytest-asyncio`, `ruff`

---

## Задача 1: Установить alembic в dev-зависимости

**Что сделать:**

Добавить `alembic` в `[project.optional-dependencies] dev` в `pyproject.toml`:
```toml
[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "alembic",  # ← добавить
]
```

**Изменяемый файл:** `pyproject.toml`

---

## Задача 2: Инициализировать Alembic

**Что сделать:**

1. Запустить команду инициализации Alembic:
   ```
   alembic init alembic
   ```
   Это создаст структуру:
   ```
   alembic/
   ├── alembic.ini              # конфигурация alembic
   ├── env.py                   # скрипт миграций
   ├── README
   └── versions/                # директория миграций
       └── .gitkeep
   ```

2. **Настроить `alembic.ini`:**
   - `sqlalchemy.url` — убрать дефолтное значение, оставить пустым или закомментированным. URL будет подставляться из `DATABASE_URL` (через `.env`).
   - Не менять другие настройки по умолчанию (`script_location`, `sqlalchemy.pool_pre_ping` и т.д.).

3. **Настроить `alembic/env.py`:**
   - Добавить импорт моделей: `from src.db import models` — чтобы Alembic видел все `DeclarativeBase` классы и мог автосгенерировать миграцию.
   - Установить `config.set_main_option("sqlalchemy.url", ...)` — брать `DATABASE_URL` из `.env` файла.
   - Убедиться, что `target_metadata = models.Base.metadata` — Alembic будет сравнивать модели с текущей схемой БД.

**Пример структуры env.py:**
```python
# alembic/env.py
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from src.db import models  # ← импорт моделей для target_metadata

# ... стандартная конфигурация логирования ...

config = context.config
# ...

# Берём DATABASE_URL из окружения
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = models.Base.metadata  # ← метаданные моделей
```

**Изменяемые файлы:** `pyproject.toml`, `alembic.ini`, `alembic/env.py` (создаются)

---

## Задача 3: Создать первую миграцию (init)

**Что сделать:**

1. Установить `.env` или `.env.example` — чтобы `DATABASE_URL` был доступен для миграций.

2. Запустить автосгенерацию миграции:
   ```
   alembic revision --autogenerate -m "init"
   ```
   Это создаст файл в `alembic/versions/` с именем вроде `001_init.py`, который создаст таблицы `users`, `sessions`, `messages`.

3. **Применить миграцию** (проверить, что работает):
   ```
   alembic upgrade head
   ```
   Миграция должна успешно создать все 3 таблицы.

4. **Проверить downgrade:**
   ```
   alembic downgrade base
   ```
   Миграция должна успешно удалить все таблицы.

**Пример сгенерированной миграции (`001_init.py`):**
```python
"""init

Revision ID: xxx
Revises: 
Create Date: 2026-02-xx
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("identifier", sa.String, unique=True, nullable=False),
        sa.Column("provider", sa.String, default="web"),
        sa.Column("role", sa.String, default="user"),
    )
    # ... sessions ...
    # ... messages ...

def downgrade():
    op.drop_table("users")
    op.drop_table("sessions")
    op.drop_table("messages")
```

**Результат:** Файл `alembic/versions/001_init.py` (имя revision будет сгенерировано автоматически).

---

## Задача 4: Очистить `init_db()` в `database.py`

**Что сделать:**

1. Убрать вызов `Base.metadata.create_all()` из `init_db()`:
   ```python
   # Было:
   async def init_db():
       from src.db.models import Base
       async with engine.begin() as conn:
           await conn.run_sync(Base.metadata.create_all)
       print("✅ Database initialized")

   # Стало:
   async def init_db():
       """Проверить подключение к БД (миграции применяются через alembic)."""
       # init_db() теперь просто проверяет, что БД доступна.
       # Фактическое создание таблиц — через alembic upgrade head.
       pass
   ```

2. Если есть сомнения — заменить на простую проверку соединения:
   ```python
   async def init_db():
       """Проверить подключение к БД (миграции применяются через alembic)."""
       from src.db.models import Base  # noqa: F401
       # Можно оставить пустым — миграции применяются через alembic upgrade head
       # при сборке Docker-образа или деплое.
   ```

**Изменяемый файл:** `src/db/database.py`

---

## Задача 5: Добавить `alembic downgrade base` в `.gitignore`

**Что сделать:**

Убедиться, что `alembic/versions/` файлы **попадают в Git** (это история миграций — это хорошо).
Убедиться, что `__pycache__/`, `.venv/`, `.env` — **исключены из Git** (уже есть в `.gitignore`).

**Проверка `.gitignore`:** убедиться, что в `.gitignore` есть:
- `__pycache__/`
- `.venv/`
- `.env` (без `.example`)

**Изменяемый файл:** `.gitignore` — проверить/дополнить.

---

## Изменяемые файлы (список)

| Файл | Что делается |
|---|---|
| `pyproject.toml` | Добавить `alembic` в dev-зависимости |
| `alembic.ini` | Создать (через `alembic init`), настроить `sqlalchemy.url` |
| `alembic/env.py` | Создать (через `alembic init`), добавить импорт моделей, `target_metadata` |
| `alembic/versions/001_init.py` | Создать (через `alembic revision --autogenerate`) |
| `src/db/database.py` | Очистить `init_db()` от `create_all()` |
| `.gitignore` | Проверить наличие исключений |

## Неизменяемые файлы

- ❌ Не трогать `plugins/` (плагины — отдельный мир, RULES.md п.10)
- ❌ Не трогать `frontend/`
- ❌ Не трогать `docker-compose.yml`, `Dockerfile` — миграции применяются отдельно (до или при деплое)
- ❌ Не трогать `src/auth.py`, `src/llm/`, `src/core/`, `src/interfaces/`
- ❌ Не трогать `src/main.py` — `init_db()` вызывается там, менять не нужно

## Критерии приёмки

1. `alembic revision --autogenerate -m "init"` генерирует миграцию, создающую все 3 таблицы (`users`, `sessions`, `messages`).
2. `alembic upgrade head` успешно создаёт таблицы.
3. `alembic downgrade base` успешно удаляет таблицы.
4. `alembic upgrade head` и `alembic downgrade base` работают без ошибок.
5. `init_db()` в `database.py` больше не вызывает `Base.metadata.create_all()`.
6. Файлы миграций (`alembic/versions/`) попадают в Git, `__pycache__/` — нет.
7. Код на русском, комментарии на русском, идентификаторы на английском.

---

*Автор ТЗ: Координатор*
*Дата: 2026-02-14*
