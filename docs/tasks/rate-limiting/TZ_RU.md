# ТЗ: Rate limiting

## Цель

Реализовать rate limiting согласно RULES.md (п.13):
- Ограничить частоту запросов к плагинам (чтобы ИИ не зациклился).
- Ограничить запросы к LLM API (Hetzner/OpenRouter).

## Контекст

- **Нет Redis** — один контейнер, один процесс FastAPI. Используем `slowapi` с `TTLCache` в памяти.
- **Правила:** Соблюдать RULES.md (код на русском, один файл = одна ответственность, ядро тонкое).
- **Рабочее пространство:** `docs/tasks/rate-limiting/TZ_RU.md` и `REPORT_RU.md`.

---

## Задача 1: Добавить slowapi в зависимости

**Что сделать:**

Добавить `"slowapi"` в `[project.optional-dependencies] dev` в `pyproject.toml`:
```toml
[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "alembic",
    "slowapi",  # ← добавить
]
```

**Изменяемый файл:** `pyproject.toml`

---

## Задача 2: Создать модуль rate_limit

**Что сделать:**

Создать `src/core/rate_limit.py` — отдельный модуль для конфигурации лимитов.

```python
# src/core/rate_limit.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
```

Модуль создаёт единственный экземпляр `limiter` с ключом по IP-адресу. Никакой логики кроме создания лимитера — `RULES.md` п.10 «один файл = одна ответственность».

**Создаваемый файл:** `src/core/rate_limit.py`

---

## Задача 3: Интегрировать limiter в FastAPI

**Что сделать:**

1. Добавить `limiter` в `main.py`:
   ```python
   from src.core.rate_limit import limiter
   
   app.state.limiter = limiter
   ```
2. Добавить middleware для rate limiting:
   ```python
   app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
   ```
3. Добавить декоратор `@limiter.limit()` на критичные эндпоинты.

**Изменяемый файл:** `src/main.py`

---

## Задача 4: Настроить лимиты

| Эндпоинт | Лимит | По чему считается |
|---|---|---|
| `POST /api/chat` | 30/min | `session_id` |
| `GET /api/tools` | 10/min | IP |
| `GET /api/plugins/status` | 10/min | IP |
| `GET /api/auth/*` | 5/min | IP |
| `GET /health` | 60/min | IP |
| WebSocket (`/ws/*`) | не ограничивать | — |

### Ключевые функции

- `/api/chat` — лимит по `session_id`, чтобы один юзер не мог спамить, а другие — могли
- `/api/auth/login` — строгий лимит (5/min по IP), чтобы предотвратить brute force
- `/api/tools` и `/api/plugins/status` — ограничивают доступ к спискам инструментов
- `/health` — не критично, но полезно от DDoS-сканеров

---

## Задача 5: Обработчик 429

**Что сделать:**

Создать функцию `handle_rate_limit_exceeded(request, exc)` — возвращает JSON:
```python
return JSONResponse(
    status_code=429,
    content={"detail": "Слишком много запросов. Попробуйте позже."},
)
```

Добавить этот обработчик как exception handler в FastAPI.

**Изменяемый файл:** `src/main.py`

---

## Задача 6: Добавить лимит на вызовы инструментов (Plugin Manager)

**Что сделать:**

Добавить простую встроенную защиту в `PluginManager.execute_tool()` — счётчик вызовов за окно 60 секунд. Если превышен лимит (например, 10 вызовов одного инструмента за минуту), поднять исключение.

```python
# В PluginManager.__init__
from collections import defaultdict
from time import time

self._tool_calls: dict = defaultdict(list)
self._max_tool_calls_per_minute = 10  # configurable?

# В execute_tool перед вызовом
now = time()
window = 60
self._tool_calls[tool_name] = [
    t for t in self._tool_calls[tool_name] if t > now - window
]
if len(self._tool_calls[tool_name]) >= self._max_tool_calls_per_minute:
    raise RuntimeError(
        f"Слишком частые вызовы '{tool_name}' — лимит 10/мин"
    )
self._tool_calls[tool_name].append(now)
```

Это защитит от зацикливания AI, когда он вызывает один инструмент бесконечно.

**Изменяемый файл:** `src/plugins/manager.py`

---

## Изменяемые файлы

| Файл | Что |
|---|---|
| `pyproject.toml` | Добавить `slowapi` в dev |
| `src/core/rate_limit.py` | Создать модуль с limiter |
| `src/core/__init__.py` | Добавить `rate_limit` |
| `src/main.py` | Интегрировать limiter, лимиты, обработчик 429 |
| `src/plugins/manager.py` | Встроенная защита от зацикливания |

## Неизменяемые файлы

- ❌ Не трогать `plugins/` кроме `manager.py`
- ❌ Не трогать `frontend/`
- ❌ Не трогать `docker-compose.yml`, `Dockerfile`
- ❌ Не трогать `src/db/`, `src/auth.py`, `src/llm/`

## Критерии приёмки

1. При 31 запросе к `/api/chat` за 1 минуту (с разными session_id) — ответ 429.
2. При 6 запросах к `/api/auth/login` за 1 минуту с одного IP — ответ 429.
3. При 11 вызовах одного инструмента (например, `wg_status`) за 1 минуту в `execute_tool` — `RuntimeError`.
4. WebSocket (`/ws/`) не ограничен.
5. Ответ при превышении — JSON `{"detail": "Слишком много запросов. Попробуйте позже."}`.
6. Код на русском, комментарии на русском, идентификаторы на английском.

---

*Автор ТЗ: Координатор*
*Дата: 2026-02-14*
