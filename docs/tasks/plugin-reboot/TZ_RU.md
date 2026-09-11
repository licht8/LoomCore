# ТЗ: Auto-restart и healthcheck плагинов

## Цель

Довести до ума обработку падений плагинов в Plugin Manager. Согласно RULES.md (п.2): «Если плагин упал — Manager должен автоматически перезапустить его (reboot logic)». Сейчас при падании процесса плагина он помечается как `STOPPED`, но **никогда не перезапускается**. Инструменты мёртвого плагина остаются в кэше LLM, и AI пытается вызывать недоступные функции.

## Контекст

- **Ядро готово:** Plugin Manager запускает плагины как subprocess, связь через MCP over stdio, таймаут 30s, падение не крашит FastAPI.
- **Правила:** Соблюдать RULES.md (код на русском, изоляция, комментарии, один файл = одна ответственность).
- **Рабочее пространство:** `docs/tasks/plugin-reboot/TZ_RU.md` и `docs/tasks/plugin-reboot/REPORT_RU.md`.

## Проблема

### Текущее поведение

```
Плагин падает (segfault / crash / infinite loop)
  → _read_response() ловит пустой readline()
  → state = STOPPED
  → RuntimeError поднимается в вызывающий код
  → try/except ловит → ошибка пользователю
  → ❌ Процесс мёртв, не перезапускается
  → ❌ Инструменты остаются в кэше AIRouter
  → ❌ LLM продолжает предлагать вызовы недоступных инструментов
  → ❌ Пользователь ждёт, пока админ перезапустит хаб
```

### Ожидаемое поведение

```
Плагин падает (segfault / crash / infinite loop)
  → _read_response() ловит пустой readline()
  → state = STOPPED, логирование
  → _restart_plugin() — запускает subprocess заново
  → initialize + tools/list для восстановления кэша
  → Если перезапуск удался → state = IDLE, continue
  → Если перезапуск не удался (N раз) → error пользователю, state = STOPPED
  → ✅ Хаб работает, остальные плагины работают
  → ✅ LLM получает актуальный список инструментов
```

---

## Задача 1: Auto-restart плагина

**Что сделать:**

1. В `src/plugins/manager.py` добавить метод `_restart_plugin(plugin_name)` — private, вызывается из `execute_tool()` при `STOPPED` или при обнаружении мёртвого процесса.

2. Алгоритм перезапуска:
   - Проверить, не превышен ли `max_restarts_count` (см. п.3 конфигурации).
   - Запустить subprocess заново через `register(name, command, working_dir)`.
   - Дождаться `initialize` (send `{"method": "initialize"}`, ждать ответ в течение 10s).
   - Получить инструменты через `tools/list`.
   - Если всё успешно — установить `state = PluginState.IDLE`, обновить `_tool_plugins`.
   - Если не удалось (timeout / исключение) — зафиксировать ошибку, увеличить `restart_failure_count`, перейти к шагу 3.

3. **Retry с экспоненциальной задержкой (backoff):**
   - Если `max_restarts_count` исчерпан — не пытаться больше, вернуть ошибку.
   - Если осталось хотя бы одно tries:
     - Вычислить delay: `min(base_delay * (2 ** attempt), max_delay)` (например: 1s, 2s, 4s, 8s, max 30s).
     - Ждать delay секунд (через `asyncio.sleep`, неблокирующе).
     - Повторить перезапуск.
   - Если все tries исчерпаны — установить `state = PluginState.STOPPED`, логировать финальную ошибку.

4. Мёртвый процесс: перед каждым `execute_tool` и `get_tools` проверять `plugin["proc"].poll()` — если процесс умер (`poll()` вернёт не None) и `state != STOPPED` — поставить `STOPPED` и запустить `_restart_plugin()`.

**Формат нового состояния плагина:**
```python
self._plugins[name] = {
    "proc": proc,
    "state": PluginState.IDLE,
    "config": {
        "command": command,
        "working_dir": working_dir,
    },
    "restart_attempt": 0,        # текущая попытка перезапуска (0 — не перезапускался)
    "max_restarts": 3,           # максимальное число перезапусков (по умолчанию 3)
    "restart_delay_base": 1.0,   # базовая задержка в секундах
    "restart_delay_max": 30.0,   # максимальная задержка в секундах
}
```

**Изменяемый файл:** `src/plugins/manager.py` — методы `execute_tool`, `get_tools`, `get_all_tools`, `discover_plugins`.

---

## Задача 2: Метод is_healthy

**Что сделать:**

1. Добавить публичный метод `is_healthy(plugin_name: str) -> bool`:
   - Проверить, что плагин существует в `self._plugins`.
   - Если `state == PluginState.STOPPED` → вернуть `False`.
   - Вызвать `plugin["proc"].poll()` — если процесс умер → вернуть `False`.
   - Иначе → вернуть `True`.

2. Добавить публичный метод `get_plugin_status(plugin_name: str) -> dict`:
   ```python
   {
       "name": "loomwg",
       "state": "idle",        # idle / busy / stopped
       "pid": 12345,
       "restart_attempt": 0,
       "is_healthy": True,
   }
   ```

3. Добавить маршрут `/api/plugins/status` в ядро (в `main.py`) для получения статуса всех плагинов:
   ```python
   @app.get("/api/plugins/status")
   async def get_plugins_status(request: Request):
       token = ...  # require_admin_token
       statuses = {}
       for name in plugin_manager.plugin_names():
           statuses[name] = plugin_manager.get_plugin_status(name)
       return {"plugins": statuses}
   ```

**Изменяемые файлы:** `src/plugins/manager.py` (добавить `is_healthy`, `get_plugin_status`), `src/main.py` (добавить маршрут).

---

## Задача 3: Очистка кэша инструментов при падении

**Что сделать:**

1. В `src/llm/router.py` в методе `route()` — при получении ошибок от `get_all_tools()`, **не просто логировать**, а запомнить: «некоторые инструменты недоступны» и вернуть в ответе `tool_schema_warning` с именами недоступных плагинов.

2. Если `execute_tool` вернул `RuntimeError("Плагин недоступен")` — LLM-роутер должен **отключить** инструменты этого плагина из текущего контекста, чтобы AI не предлагал их в будущем.

3. При successful auto-restart плагина — кэш инструментов автоматически обновится при следующем вызове `get_all_tools()`.

**Изменяемый файл:** `src/llm/router.py` — метод `route()`.

---

## Задача 4: Plugin healthcheck (background task)

**Что сделать:**

1. Добавить в `src/main.py` background-задачу (черед `asyncio.create_task` в `startup()`), которая периодически (каждые 30 секунд) проверяет все плагины через `plugin_manager.is_healthy(plugin_name)`.

2. Если плагин не здоров (не найден в `_plugins`, процесс умер) — запустить `_restart_plugin()` асинхронно, не блокируя обработчики запросов.

3. **Важно:** healthcheck должен работать параллельно — если плагин перезапускается во время обработки запроса, это не должно блокировать `/api/chat` или WebSocket.

```python
# Пример в startup():
async def _healthcheck_loop():
    while True:
        await asyncio.sleep(30)
        for name in plugin_manager.plugin_names():
            if not plugin_manager.is_healthy(name):
                logger.warning(f"Плагин '{name}' не здоров — перезапуск")
                try:
                    await plugin_manager._restart_plugin(name)
                    logger.info(f"Плагин '{name}' успешно перезапущен")
                except Exception as exc:
                    logger.error(f"Не удалось перезапустить '{name}': {exc}")

@app.on_event("startup")
async def startup():
    ...
    asyncio.create_task(_healthcheck_loop())
```

**Изменяемый файл:** `src/main.py` — добавить `_healthcheck_loop` и зарегистрировать в `startup()`.

---

## Задача 5: Обработка ошибок в `_write_request`

**Что сделать:**

1. В `_write_request` добавить обработку `BrokenPipeError` / `OSError` при записи в stdin — если процесс умер, stdin будет закрыт. В этом случае:
   - Поставить `state = PluginState.STOPPED`.
   - Запустить `_restart_plugin()` (см. п.2 retry-логики).
   - После успешного перезапуска — повторить запрос (retry one time).

2. Если перезапуск не удался — поднять `RuntimeError` как сейчас.

**Изменяемый файл:** `src/plugins/manager.py` — метод `_write_request`.

---

## Изменяемые файлы (список)

| Файл | Что меняется |
|---|---|
| `src/plugins/manager.py` | `_restart_plugin()`, `is_healthy()`, `get_plugin_status()`, логика retry/backoff в `execute_tool`, проверка мёртвого процесса в `_write_request` и `execute_tool` |
| `src/main.py` | маршрут `/api/plugins/status`, background `healthcheck_loop` |
| `src/llm/router.py` | более детальная обработка ошибок плагинов, очистка кэша |

## Неизменяемые файлы

- ❌ Не трогать `plugins/` (плагины — отдельный мир, RULES.md п.10)
- ❌ Не трогать `frontend/` (только бэкенд)
- ❌ Не трогать `docker-compose.yml`, `Dockerfile`
- ❌ Не трогать `src/auth.py`, `src/db/`, `src/core/`, `src/interfaces/`

## Критерии приёмки

1. Плагин падает → Hub не крашится → ошибка пользователю → автоперезапуск через 1-4s → плагин возвращается в `idle` → инструменты снова доступны.
2. `is_healthy("loomwg")` возвращает `True` когда плагин жив, `False` когда мёртв.
3. `/api/plugins/status` возвращает JSON с состоянием всех плагинов.
4. LLM не предлагает инструменты мёртвого плагина (кэш обновляется при перезапуске).
5. Если плагин не может перезапуститься 3 раза подряд → останавливается, ошибка в лог, инструменты недоступны до ручного вмешательства.
6. Background healthcheck каждые 30 секунд не блокирует запросы к API.
7. Код на русском, комментарии на русском, идентификаторы на английском.

---

*Автор ТЗ: Координатор*
*Дата: 2026-02-14*
