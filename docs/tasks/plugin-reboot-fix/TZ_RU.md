# ТЗ: Исправление багов автоперезпуска плагинов

## Цель

Устранить критический баг и замечания из приёмки отчёта `docs/tasks/plugin-reboot/TZ_RU.md`. Без исправлений работа автоперезпуска плагинов ненадёжна: пропущенный `await` вызывает race condition, `print()` ломает консистентность логирования, дублирование `_tool_plugins` создаёт лишнюю работу.

## Контекст

- **Автоперезпуск плагинов реализован:** `docs/tasks/plugin-reboot/REPORT_RU.md` описывает 5 задач, все выполнены.
- **Приёмка выявила 3 проблемы:** 1 критическая (race condition), 1 высокая (консистентность логов), 1 низкая (дублирование кода).
- **Правила:** Соблюдать RULES.md (код на русском, изоляция, комментарии, один файл = одна ответственность).
- **Рабочее пространство:** `docs/tasks/plugin-reboot-fix/` — ТЗ и отчёт.

---

## Задача 1: Добавить `await` перед `_write_request_with_retry()` — критический баг

**Где:** `src/plugins/manager.py`, строка 491, метод `execute_tool()`.

**Проблема:**
```python
# Строка 491 — БЕЗ await
self._write_request_with_retry(plugin_name, request)
plugin["state"] = PluginState.BUSY
response = await asyncio.wait_for(
    asyncio.to_thread(self._read_response, plugin_name),
    timeout=30,
)
```

Метод `_write_request_with_retry` — это `async def`, но вызывается без `await`. Это означает:
1. Код не ждёт завершения записи в stdin плагина.
2. Следующая строка `plugin["state"] = PluginState.BUSY` и `read_response()` запускаются **мгновенно**, до того как запрос реально попадёт в процесс плагина.
3. `read_response()` читает из `stdout` — но там может быть ответ от предыдущего сообщения (или ничего). Это **race condition**.
4. Результат: AI получает неправильный ответ, инструмент выполняется с задержкой, или падает с таймаутом.

**Что сделать:**

Добавить `await` перед вызовом:
```python
# Было:
self._write_request_with_retry(plugin_name, request)

# Стало:
await self._write_request_with_retry(plugin_name, request)
```

Только одна строка меняется.

**Изменяемый файл:** `src/plugins/manager.py`

---

## Задача 2: Заменить `print()` на `logger.warning()` в router

**Где:** `src/llm/router.py`, строка 77.

**Проблема:**
```python
# Строка 77 — использует print(), а не logging
except Exception as exc:
    print(f"⚠️ Не удалось получить инструменты плагина: {exc}")
```

В проекте все модули используют `logging.getLogger("loomhub.*")` для вывода. `print()` — это синхронная запись в stdout, которая:
- Не идёт в форматированный лог (без timestamp, без уровня, без имени модуля)
- Может потеряться при docker-логировании (json-file драйвер парсит только structured output)
- Несоответствует RULES.md (единый стиль логирования в ядре)

**Что сделать:**

Заменить `print()` на `logger.warning()`:
```python
# Было:
print(f"⚠️ Не удалось получить инструменты плагина: {exc}")

# Стало:
logger.warning(f"Не удалось получить инструменты плагина: {exc}")
```

**Изменяемый файл:** `src/llm/router.py`

---

## Задача 3: Убрать дублирование `_tool_plugins` в `get_all_tools()`

**Где:** `src/plugins/manager.py`, метод `get_all_tools()`.

**Проблема:**

После успешного автоперезпуска плагина `_restart_plugin()` уже вызывает `_rebuild_tool_cache()`, который заполняет `_tool_plugins`. Но `get_all_tools()` после рестарта вручную добавляет инструменты заново:

```python
# get_all_tools(), строки 428-437
success = await self._restart_plugin(plugin_name)
if success:
    # Перечитаем инструменты после перезапуска
    for tool in await self.get_tools(plugin_name):
        tool_name = tool.get("name")
        if not tool_name or tool.get("internal"):
            continue
        self._tool_plugins[tool_name] = plugin_name  # ← дублирование
        all_tools.append(tool)
```

`_restart_plugin()` уже вызвал `_rebuild_tool_cache()` (строка 149), который очистил старые записи и добавил новые. Затем `get_tools()` снова получил инструменты. И `get_all_tools()` вручную добавляет их в `_tool_plugins` **ещё раз**. Это не ломает функциональность, но:
- Делает код избыточным
- `_rebuild_tool_cache()` и ручной цикл делают одно и то же
- При большом количестве инструментов — лишняя итерация словаря

**Что сделать:**

Убрать ручное добавление в `_tool_plugins`. Оставить только сбор `all_tools` — кэш `_tool_plugins` уже заполнен `_rebuild_tool_cache()` после рестарта:

```python
# Было (строки 426-439):
for plugin_name in failed_plugins:
    logger.info(f"Попытка автоперезпуска плагина '{plugin_name}' из get_all_tools")
    try:
        success = await self._restart_plugin(plugin_name)
        if success:
            # Перечитаем инструменты после перезапуска
            for tool in await self.get_tools(plugin_name):
                tool_name = tool.get("name")
                if not tool_name or tool.get("internal"):
                    continue
                self._tool_plugins[tool_name] = plugin_name
                all_tools.append(tool)
    except Exception as inner_exc:
        logger.error(f"Не удалось перезапустить плагин '{plugin_name}' из get_all_tools: {inner_exc}")

# Стало:
for plugin_name in failed_plugins:
    logger.info(f"Попытка автоперезпуска плагина '{plugin_name}' из get_all_tools")
    try:
        success = await self._restart_plugin(plugin_name)
        if success:
            # Кэш _tool_plugins уже обновлён через _rebuild_tool_cache()
            # Перечитаем инструменты только для возврата в all_tools
            for tool in await self.get_tools(plugin_name):
                tool_name = tool.get("name")
                if not tool_name or tool.get("internal"):
                    continue
                all_tools.append(tool)
    except Exception as inner_exc:
        logger.error(f"Не удалось перезапустить плагин '{plugin_name}' из get_all_tools: {inner_exc}")
```

**Изменяемый файл:** `src/plugins/manager.py`

---

## Изменяемые файлы (список)

| Файл | Что меняется |
|---|---|
| `src/plugins/manager.py` | 1. Добавить `await` перед `_write_request_with_retry` (строка 491). 2. Убрать дублирование `_tool_plugins` в `get_all_tools()` (строки 416-419). |
| `src/llm/router.py` | Заменить `print()` → `logger.warning()` (строка 77). |

## Неизменяемые файлы

- ❌ Не трогать `plugins/` (плагины — отдельный мир, RULES.md п.10)
- ❌ Не трогать `frontend/`
- ❌ Не трогать `docker-compose.yml`, `Dockerfile`
- ❌ Не трогать `src/auth.py`, `src/db/`, `src/core/`, `src/interfaces/`

## Критерии приёмки

1. `_write_request_with_retry` вызывается с `await` — код не уходит на `BUSY`/`read_response` раньше, чем запрос записан в stdin.
2. В `router.py` нет `print()` — всё логирование через `logging.getLogger("loomhub.*")`.
3. В `get_all_tools()` после успешного рестарта `_tool_plugins` не заполняется вручную — только `all_tools.append(tool)`.
4. Связь между файлами сохранена: `manager.py` не импортируется в `router.py`, и наоборот.
5. Код на русском, комментарии на русском, идентификаторы на английском.

---

*Автор ТЗ: Координатор*
*Дата: 2026-02-14*
