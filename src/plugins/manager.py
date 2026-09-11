"""Plugin Manager — запуск плагинов как subprocess + MCP Transport (stdin/stdout).

Каждый плагин запускается как отдельный процесс. Связь: JSON-RPC 2.0
по stdin/stdout. Таймаут вызова: 30 секунд. Падение плагина не крашит Hub.
"""

import asyncio
import json
import logging
import subprocess
import uuid
from enum import Enum
from pathlib import Path

logger = logging.getLogger("loomhub.plugins")


class PluginState(str, Enum):
    """Состояние процесса плагина."""

    IDLE = "idle"
    BUSY = "busy"
    STOPPED = "stopped"


class PluginManager:
    """Управление плагинами: запуск, MCP-транпорт, вызов инструментов."""

    def __init__(self):
        # name -> {"proc": subprocess, "state": PluginState, "config": {...}}
        self._plugins: dict[str, dict] = {}
        self._tool_plugins: dict[str, str] = {}

    def register(
        self,
        name: str,
        command: list[str],
        working_dir: str | None = None,
    ):
        """Зарегистрировать и запустить плагин как subprocess.

        Args:
            name: Имя плагина (уникальный идентификатор).
            command: Команда запуска [python, путь_к_main.py].
            working_dir: Рабочая директория плагина.
        """
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
        )
        self._plugins[name] = {
            "proc": proc,
            "state": PluginState.IDLE,
            "config": {
                "command": command,
                "working_dir": working_dir,
            },
            "restart_attempt": 0,
            "max_restarts": 3,
            "restart_delay_base": 1.0,
            "restart_delay_max": 30.0,
        }
        logger.info(f"Плагин '{name}' запущен (PID: {proc.pid})")

    def discover_plugins(self, plugins_dir: str = "plugins") -> list[str]:
        """Запустить все плагины, содержащие main.py в дочерних директориях."""
        discovered: list[str] = []
        for plugin_dir in sorted(Path(plugins_dir).iterdir()):
            if not plugin_dir.is_dir() or not (plugin_dir / "main.py").is_file():
                continue
            name = plugin_dir.name
            if name in self._plugins:
                continue
            self.register(name, ["python", str(plugin_dir / "main.py")])
            discovered.append(name)
        return discovered

    def plugin_names(self) -> list[str]:
        return list(self._plugins)

    # ------------------------------------------------------------------
    # Задача 1: Auto-restart плагина
    # ------------------------------------------------------------------

    async def _restart_plugin(self, plugin_name: str) -> bool:
        """Перезапустить плагин с экспоненциальной задержкой (backoff).

        Returns:
            True — если перезапуск успешен и плагин в состоянии IDLE.
            False — если исчерпаны попытки или ошибка.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            logger.error(f"Плагин '{plugin_name}' не зарегистрирован — невозможно перезапустить")
            return False

        # Проверка лимита перезапусков
        if plugin["restart_attempt"] >= plugin["max_restarts"]:
            logger.error(
                f"Плагин '{plugin_name}' исчерпал попытки перезапуска "
                f"({plugin['restart_attempt']}/{plugin['max_restarts']})"
            )
            plugin["state"] = PluginState.STOPPED
            return False

        plugin["restart_attempt"] += 1
        attempt = plugin["restart_attempt"]
        base_delay = plugin["restart_delay_base"]
        max_delay = plugin["restart_delay_max"]
        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)

        logger.info(
            f"Попытка перезапуска плагина '{plugin_name}' ({attempt}/{plugin['max_restarts']}), "
            f"задержка {delay:.1f}с"
        )

        # Ждём перед перезапуском (неблокирующе)
        await asyncio.sleep(delay)

        try:
            # Убиваем старый процесс если ещё жив
            old_proc = plugin["proc"]
            if old_proc and old_proc.poll() is None:
                old_proc.terminate()
                try:
                    old_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    old_proc.kill()
                    old_proc.wait()

            # Запускаем subprocess заново через register (обновляем dict)
            self._do_register_inplace(
                plugin_name,
                plugin["config"]["command"],
                plugin["config"]["working_dir"],
            )

            # Ждём initialize
            await self._initialize_plugin(plugin_name)

            # Получаем инструменты через tools/list
            tools = await self.get_tools(plugin_name)

            # Обновляем кэш инструментов
            self._rebuild_tool_cache(plugin_name, tools)

            plugin["state"] = PluginState.IDLE
            logger.info(f"Плагин '{plugin_name}' успешно перезапущен")
            return True

        except Exception as exc:
            logger.error(f"Не удалось перезапустить плагин '{plugin_name}': {exc}")
            plugin["state"] = PluginState.STOPPED
            return False

    def _do_register_inplace(self, name: str, command: list[str], working_dir: str | None):
        """Запустить subprocess заново, сохранив ключи словаря (не перезаписывая ссылку)."""
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=working_dir,
        )
        plugin = self._plugins[name]
        plugin["proc"] = proc
        plugin["state"] = PluginState.IDLE
        logger.info(f"Плагин '{name}' subprocess перезапущен (PID: {proc.pid})")

    async def _initialize_plugin(self, plugin_name: str) -> None:
        """Отправить {\"method\": \"initialize\"} и дождаться ответа (таймаут 10s)."""
        request = {"jsonrpc": "2.0", "method": "initialize"}
        request_id = str(uuid.uuid4())
        line = json.dumps(request, ensure_ascii=False) + "\n"

        plugin = self._plugins[plugin_name]
        plugin["proc"].stdin.write(line)
        plugin["proc"].stdin.flush()

        # Ждём ответ в течение 10 секунд
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._read_response, plugin_name),
                timeout=10,
            )
            logger.info(f"Плагин '{plugin_name}' инициализирован")
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут инициализации плагина '{plugin_name}' (10s)")
            raise

    def _rebuild_tool_cache(self, plugin_name: str, tools: list[dict]) -> None:
        """Перестроить кэш _tool_plugins для данного плагина (очистить старые, добавить новые)."""
        # Удаляем старые записи этого плагина
        tools_to_remove = [
            tname for tname, pname in self._tool_plugins.items() if pname == plugin_name
        ]
        for tname in tools_to_remove:
            del self._tool_plugins[tname]

        # Добавляем новые
        for tool in tools:
            tool_name = tool.get("name")
            if tool_name and not tool.get("internal"):
                self._tool_plugins[tool_name] = plugin_name

    # ------------------------------------------------------------------
    # Задача 2: Методы is_healthy / get_plugin_status
    # ------------------------------------------------------------------

    def is_healthy(self, plugin_name: str) -> bool:
        """Проверить, здоров ли плагин (живой процесс и не в состоянии STOPPED).

        Args:
            plugin_name: Имя плагина.

        Returns:
            True — если плагин жив и не остановлен.
            False — если плагин не найден, мёртв или остановлен.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return False
        if plugin["state"] == PluginState.STOPPED:
            return False
        if plugin["proc"].poll() is not None:
            plugin["state"] = PluginState.STOPPED
            return False
        return True

    def get_plugin_status(self, plugin_name: str) -> dict:
        """Вернуть статус плагина в формате для API.

        Returns:
            {"name": ..., "state": ..., "pid": ..., "restart_attempt": ..., "is_healthy": ...}
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return {
                "name": plugin_name,
                "state": "stopped",
                "pid": None,
                "restart_attempt": 0,
                "is_healthy": False,
            }

        pid = plugin["proc"].pid
        healthy = self.is_healthy(plugin_name)
        return {
            "name": plugin_name,
            "state": plugin["state"].value,
            "pid": pid,
            "restart_attempt": plugin["restart_attempt"],
            "is_healthy": healthy,
        }

    # ------------------------------------------------------------------
    # Задача 5: Обработка BrokenPipeError / OSError в _write_request
    # ------------------------------------------------------------------

    def _write_request(self, plugin_name: str, request: dict) -> int:
        """Записать JSON-RPC запрос в stdin плагина. Возвращает request_id.

        Обработка BrokenPipeError / OSError при записи — если процесс мёртв,
        ставим STOPPED и поднимаем RuntimeError для вызова из execute_tool.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Плагин '{plugin_name}' не зарегистрирован")

        if plugin["state"] == PluginState.BUSY:
            raise RuntimeError(f"Плагин '{plugin_name}' занят (timeout 30s)")

        # Проверяем, не мёртв ли процесс
        if plugin["proc"].poll() is not None:
            if plugin["state"] != PluginState.STOPPED:
                logger.warning(f"Процесс плагина '{plugin_name}' мёртв — ставим STOPPED")
                plugin["state"] = PluginState.STOPPED
            raise RuntimeError(f"Плагин '{plugin_name}' недоступен (процесс мёртв)")

        request["id"] = request.get("id") or str(uuid.uuid4())
        line = json.dumps(request, ensure_ascii=False)
        try:
            plugin["proc"].stdin.write(line + "\n")
            plugin["proc"].stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.error(f"BrokenPipeError для плагина '{plugin_name}': {exc}")
            plugin["state"] = PluginState.STOPPED
            raise RuntimeError(f"Плагин '{plugin_name}' недоступен (поток ввода разорван)") from exc

        return request["id"]

    async def _write_request_with_retry(self, plugin_name: str, request: dict) -> str:
        """Записать запрос в stdin плагина, с автоперезапуском при BrokenPipeError.

        Если процесс мёртв при записи:
        1. Ставит STOPPED, запускает _restart_plugin().
        2. После успешного перезапуска — повторяет запись один раз.
        3. Если перезапуск не удался — поднимает RuntimeError.

        Returns:
            request_id
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Плагин '{plugin_name}' не зарегистрирован")

        if plugin["state"] == PluginState.BUSY:
            raise RuntimeError(f"Плагин '{plugin_name}' занят (timeout 30s)")

        # Проверяем, не мёртв ли процесс
        if plugin["proc"].poll() is not None:
            if plugin["state"] != PluginState.STOPPED:
                logger.warning(f"Процесс плагина '{plugin_name}' мёртв — ставим STOPPED")
                plugin["state"] = PluginState.STOPPED
            raise RuntimeError(f"Плагин '{plugin_name}' недоступен (процесс мёртв)")

        request["id"] = request.get("id") or str(uuid.uuid4())
        line = json.dumps(request, ensure_ascii=False)

        try:
            plugin["proc"].stdin.write(line + "\n")
            plugin["proc"].stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.error(f"BrokenPipeError для плагина '{plugin_name}' при записи: {exc}")
            plugin["state"] = PluginState.STOPPED

            # Пытаемся перезапустить
            logger.info(f"Попытка автоперезпуска плагина '{plugin_name}' из _write_request_with_retry")
            success = await self._restart_plugin(plugin_name)
            if not success:
                raise RuntimeError(f"Плагин '{plugin_name}' недоступен (перезапуск не удался)")

            # Повторяем запись один раз
            plugin = self._plugins[plugin_name]
            logger.info(f"Повторная запись после перезапуска плагина '{plugin_name}'")
            try:
                plugin["proc"].stdin.write(line + "\n")
                plugin["proc"].stdin.flush()
            except (BrokenPipeError, OSError) as retry_exc:
                logger.error(f"BrokenPipeError при повторной записи для плагина '{plugin_name}': {retry_exc}")
                raise RuntimeError(f"Плагин '{plugin_name}' недоступен (поток ввода разорван)") from retry_exc

        return request["id"]

    def _read_response(self, plugin_name: str) -> dict:
        """Прочитать следующий JSON-RPC ответ, пропуская уведомления MCP."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Плагин '{plugin_name}' не зарегистрирован")

        while True:
            line = plugin["proc"].stdout.readline()
            if not line:
                logger.error(f"Плагин '{plugin_name}' завершил работу")
                plugin["state"] = PluginState.STOPPED
                raise RuntimeError(f"Плагин '{plugin_name}' завершил работу")

            response = json.loads(line.strip())
            if "id" in response:
                return response

    async def get_tools(self, plugin_name: str) -> list[dict]:
        """Получить список инструментов плагина (tools/list → JSON Schema).

        При обнаружении мёртвого процесса или STOPPED — пытается автоперезапуск.
        """
        plugin = self._plugins.get(plugin_name)

        # Проверяем, мёртв ли процесс
        if plugin and plugin["proc"].poll() is not None:
            if plugin["state"] != PluginState.STOPPED:
                logger.warning(f"Процесс плагина '{plugin_name}' мёртв — ставим STOPPED")
                plugin["state"] = PluginState.STOPPED

        if not plugin or plugin["state"] == PluginState.STOPPED:
            logger.info(f"Попытка автоперезпуска плагина '{plugin_name}' перед get_tools")
            success = await self._restart_plugin(plugin_name)
            if not success:
                raise RuntimeError(f"Плагин '{plugin_name}' недоступен")
            plugin = self._plugins[plugin_name]

        # Проверяем ещё раз — вдруг перезапуск провалился
        if plugin["state"] == PluginState.STOPPED or plugin["proc"].poll() is not None:
            raise RuntimeError(f"Плагин '{plugin_name}' недоступен (процесс мёртв)")

        try:
            request = {"jsonrpc": "2.0", "method": "tools/list"}
            self._write_request(plugin_name, request)
            plugin["state"] = PluginState.BUSY
            response = await asyncio.wait_for(
                asyncio.to_thread(self._read_response, plugin_name),
                timeout=30,
            )
            result = response.get("result", {})
            return result.get("tools", [])
        finally:
            if plugin["state"] == PluginState.BUSY:
                plugin["state"] = PluginState.IDLE

    async def get_all_tools(self) -> list[dict]:
        """Получить инструменты всех зарегистрированных плагинов.

        При ошибке получения инструментов плагина — пытается автоперезапуск.
        Возвращает список инструментов, которые удалось получить.
        """
        all_tools: list[dict] = []
        failed_plugins: list[str] = []
        for plugin_name in self.plugin_names():
            try:
                for tool in await self.get_tools(plugin_name):
                    tool_name = tool.get("name")
                    if not tool_name or tool.get("internal"):
                        continue
                    self._tool_plugins[tool_name] = plugin_name
                    all_tools.append(tool)
            except Exception as exc:
                logger.warning(f"Не удалось получить инструменты плагина '{plugin_name}': {exc}")
                failed_plugins.append(plugin_name)

        # Пытаемся перезапустить плагины, которые не ответили
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

        return all_tools

    def plugin_for_tool(self, tool_name: str) -> str:
        plugin_name = self._tool_plugins.get(tool_name)
        if not plugin_name:
            raise ValueError(f"Инструмент '{tool_name}' не зарегистрирован")
        return plugin_name

    # ------------------------------------------------------------------
    # Задача 1 (продолжение): execute_tool с проверкой мёртвого процесса
    # ------------------------------------------------------------------

    async def execute_tool(
        self,
        plugin_name: str,
        tool_name: str,
        arguments: dict | None = None,
    ) -> dict:
        """Вызвать инструмент плагина (tools/call → result).

        Таймаут: 30 секунд. Если плагин не ответил — убиваем процесс.
        При STOPPED или мёртвом процессе — пытаемся автоперезапуск.
        """
        plugin = self._plugins.get(plugin_name)

        # Проверяем, не мёртв ли процесс (если не STOPPED)
        if plugin and plugin["proc"].poll() is not None and plugin["state"] != PluginState.STOPPED:
            logger.warning(f"Процесс плагина '{plugin_name}' мёртв при вызове '{tool_name}'")
            plugin["state"] = PluginState.STOPPED

        if not plugin or plugin["state"] == PluginState.STOPPED:
            # Пытаемся автоперезапуск
            logger.info(f"Попытка автоперезпуска плагина '{plugin_name}' перед вызовом '{tool_name}'")
            success = await self._restart_plugin(plugin_name)
            if not success:
                raise RuntimeError(f"Плагин '{plugin_name}' недоступен")

            # После успешного перезапуска — повторяем вызов
            plugin = self._plugins[plugin_name]

        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        try:
            await self._write_request_with_retry(plugin_name, request)
            plugin["state"] = PluginState.BUSY
            response = await asyncio.wait_for(
                asyncio.to_thread(self._read_response, plugin_name),
                timeout=30,
            )
            if response.get("error"):
                raise RuntimeError(response["error"].get("message", "Ошибка MCP-плагина"))
            result = response.get("result")
            if result is None:
                raise RuntimeError(f"Плагин '{plugin_name}' не вернул результат")
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут 30s для плагина '{plugin_name}' — убиваем процесс")
            plugin["proc"].kill()
            plugin["state"] = PluginState.STOPPED
            raise TimeoutError(f"Таймаут вызова '{tool_name}' в '{plugin_name}' (30s)")

        finally:
            if plugin["state"] == PluginState.BUSY:
                plugin["state"] = PluginState.IDLE

    async def execute_tool_by_name(
        self, tool_name: str, arguments: dict | None = None
    ) -> dict:
        return await self.execute_tool(
            self.plugin_for_tool(tool_name), tool_name, arguments
        )

    def stop(self, plugin_name: str):
        """Остановить процесс плагина."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return
        if plugin["proc"].poll() is None:
            plugin["proc"].terminate()
            try:
                plugin["proc"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                plugin["proc"].kill()
        plugin["state"] = PluginState.STOPPED
        plugin["restart_attempt"] = 0  # сбрасываем счётчик при ручной остановке
        logger.info(f"Плагин '{plugin_name}' остановлен")

    def is_running(self, plugin_name: str) -> bool:
        """Проверить, работает ли процесс плагина."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return False
        return plugin["proc"].poll() is None
