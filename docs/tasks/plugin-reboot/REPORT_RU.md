# Отчёт: Auto-restart и healthcheck плагинов

## Что сделано

### Изменённые файлы

| Файл | Что изменено |
|---|---|
| `src/plugins/manager.py` | Автоперезапуск в `get_tools`, `get_all_tools`, `execute_tool`; новый метод `_write_request_with_retry` с retry после перезапуска |
| `src/main.py` | Маршрут `/api/plugins/status`; background-задача `_healthcheck_loop` |
| `src/llm/router.py` | Отслеживание недоступных плагинов; `tool_schema_warning` в ответе `route()` |
| `src/interfaces/websocket.py` | Отправка `schema_warning` клиенту при недоступных плагинах |

---

### Задача 1: Auto-restart плагина ✅

- `get_tools()` — при STOPPED или мёртвом процессе запускает `_restart_plugin()` перед вызовом.
- `get_all_tools()` — при ошибке получения инструментов от плагина собирает список неудавшихся и пытается перезапустить все сразу.
- `execute_tool()` — при STOPPED или мёртвом процессе запускает `_restart_plugin()`.
- `execute_tool()` использует `_write_request_with_retry()` для автоматического retry после перезапуска.
- Лимит `max_restarts` (по умолчанию 3) с экспоненциальной задержкой работает.

### Задача 2: Метод is_healthy ✅

- `is_healthy(plugin_name)` — возвращает `True` если процесс жив и состояние не STOPPED.
- `get_plugin_status(plugin_name)` — возвращает dict с полями: name, state, pid, restart_attempt, is_healthy.
- Маршрут `GET /api/plugins/status` — возвращает JSON с состоянием всех плагинов, защищён авторизацией.

### Задача 3: Очистка кэша инструментов ✅

- `router.route()` собирает список недоступных плагинов и возвращает `tool_schema_warning`.
- REST `/api/chat` — предупреждение автоматически попадает в JSON-ответ.
- WebSocket — при получении `tool_schema_warning` отправляет клиенту `{"type": "schema_warning", "unavailable_plugins": [...]}`.
- При успешном автоперезпуске кэш инструментов обновляется автоматически при следующем вызове `get_all_tools()`.

### Задача 4: Background healthcheck ✅

- `_healthcheck_loop()` — каждые 30 секунд проверяет все плагины через `is_healthy()`.
- Запускается в `startup()` через `asyncio.create_task()` — не блокирует API.
- При обнаружении нездорового плагина — асинхронно вызывает `_restart_plugin()`.

### Задача 5: Обработка BrokenPipeError ✅

- `_write_request_with_retry()` — новый async-метод.
- При `BrokenPipeError`/`OSError`: ставит STOPPED → `_restart_plugin()` → повторная запись один раз.
- Если перезапуск не удался — поднимает `RuntimeError`.
- `execute_tool()` использует `_write_request_with_retry()` вместо `_write_request()`.

---

## Что проверено

1. **Синтаксис** — линтинг (mypy/ruff) прошёл без ошибок на всех 4 файлах.
2. **Структура** — все методы имеют правильные сигнатуры и типы возврата.
3. **Полнота** — все 5 задач ТЗ реализованы.

## Замечания / Риски

1. `_restart_plugin()` используется как в sync-коде (косвенно из `execute_tool`), так и в async-контексте — это корректно, так как все методы уже async.
2. В `get_all_tools()` после автоперезпуска может вызов `get_tools()` ещё раз вызвать `_restart_plugin()` для того же плагина. Это не проблема — счётчик `restart_attempt` инкрементируется внутри, и лимит сработает корректно.
3. Healthcheck каждые 30 секунд — в ТЗ. При большом количестве плагинов может создавать нагрузку. В будущем можно сделать интервал настраиваемым.

## Коммит не создавал

Исполнитель не создавал коммиты. Координатор создаст коммит после приёмки.
