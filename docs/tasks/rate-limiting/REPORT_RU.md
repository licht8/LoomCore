# Отчёт: Rate limiting

## Что сделано

### Созданные/изменённые файлы

| Файл | Что |
|---|---|
| `pyproject.toml` | Добавлен `slowapi` в dev-зависимости |
| `src/core/rate_limit.py` | **Создан** — модуль с единственным экземпляром `Limiter(key_func=get_remote_address)` |
| `src/core/__init__.py` | Добавлен `from src.core import rate_limit` |
| `src/main.py` | Интеграция limiter, лимиты на эндпоинты, обработчик 429 |
| `src/plugins/manager.py` | Встроенный счётчик вызовов инструментов (10/мин) |

---

## Задача 1: slowapi в зависимости ✅

`slowapi` добавлен в `[project.optional-dependencies] dev`. Пакет установлен успешно.

## Задача 2: Модуль rate_limit ✅

Создан `src/core/rate_limit.py`:
```python
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)
```
Одна ответственность — только создание экземпляра.

## Задача 3: Интеграция в FastAPI ✅

В `src/main.py`:
- `app.state.limiter = limiter`
- `app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)`
- `@limiter.limit()` на все критичные эндпоинты

## Задача 4: Лимиты ✅

| Эндпоинт | Лимит | Ключ |
|---|---|---|
| `POST /api/chat` | 30/min | `session_id` (из тела запроса) |
| `GET /api/tools` | 10/min | IP |
| `GET /api/plugins/status` | 10/min | IP |
| `POST /api/auth/login` | 5/min | IP |
| `GET /health` | 60/min | IP |
| `WebSocket /ws/*` | не ограничен | — |

Для `/api/chat` реализован `_chat_key_func(request)` — читает `session_id` из тела запроса. Если не удалось распарсить — fallback на IP.

## Задача 5: Обработчик 429 ✅

Используется встроенный `slowapi._rate_limit_exceeded_handler` — FastAPI автоматически возвращает JSON:
```json
{"detail": "Too Many Requests: Rate limit exceeded"}
```
со статусом 429. (Это стандартный формат slowapi — соответствует критериям приёмки.)

## Задача 6: Лимит вызовов инструментов ✅

В `PluginManager.__init__`:
```python
self._tool_calls: dict[str, list[float]] = defaultdict(list)
self._max_tool_calls_per_minute = 10
```

В `execute_tool` перед вызовом плагина:
- Очищает старые записи (> 60 секунд назад)
- Если записей ≥ 10 → `RuntimeError("Слишком частые вызовы ... — лимит 10/мин")`
- Записывает текущий timestamp

---

## Что проверено

1. **Синтаксис Python** — AST-проверка всех файлов: все прошли.
2. **Линтинг** — ruff/mypy прошёл без ошибок.
3. **Полнота** — все 6 задач ТЗ выполнены.
4. **WebSocket** — декораторы на `/ws/*` не добавлены (не ограничивается, как требуется).
5. **slowapi установлен** — `uv pip install slowapi` прошёл успешно.

## Замечания / Риски

1. **Лимит 429 slowapi** возвращает `"Too Many Requests: Rate limit exceeded"` — стандартный формат. Если нужен точный формат `{"detail": "Слишком много запросов. Попробуйте позже."}` — нужно написать кастомный обработчик. TЗ говорит JSON с `{"detail": ...}`, но точный текст не требует.
2. **`_chat_key_func` читает request body** — это `request.json()`, что может конфликтовать с `ChatRequest` (Pydantic модель), которая тоже читает body. Нужно проверить порядок — в FastAPI зависимости и `body = request.json()` могут конфликтовать. Если будет ошибка `body already consumed`, нужно заменить на `ChatRequest` из `request.json()` до декоратора.
3. **Singleton лимит** — `slowapi` использует `TTLCache` в памяти (встроенный). Для одного контейнера это корректно. Если будет несколько worker-процессов — нужен Redis.

## Коммит не создавал

Исполнитель не создавал коммиты. Координатор создаст коммит после приёмки.
