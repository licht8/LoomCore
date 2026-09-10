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

    def _write_request(self, plugin_name: str, request: dict) -> int:
        """Записать JSON-RPC запрос в stdin плагина. Возвращает request_id."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            raise ValueError(f"Плагин '{plugin_name}' не зарегистрирован")

        if plugin["state"] == PluginState.BUSY:
            raise RuntimeError(f"Плагин '{plugin_name}' занят (timeout 30s)")

        request["id"] = request.get("id") or str(uuid.uuid4())
        line = json.dumps(request, ensure_ascii=False)
        plugin["proc"].stdin.write(line + "\n")
        plugin["proc"].stdin.flush()
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
        """Получить список инструментов плагина (tools/list → JSON Schema)."""
        plugin = self._plugins.get(plugin_name)
        if not plugin or plugin["state"] == PluginState.STOPPED:
            raise RuntimeError(f"Плагин '{plugin_name}' недоступен")

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
        """Получить инструменты всех зарегистрированных плагинов."""
        all_tools: list[dict] = []
        for plugin_name in self.plugin_names():
            for tool in await self.get_tools(plugin_name):
                tool_name = tool.get("name")
                if not tool_name or tool.get("internal"):
                    continue
                self._tool_plugins[tool_name] = plugin_name
                all_tools.append(tool)
        return all_tools

    def plugin_for_tool(self, tool_name: str) -> str:
        plugin_name = self._tool_plugins.get(tool_name)
        if not plugin_name:
            raise ValueError(f"Инструмент '{tool_name}' не зарегистрирован")
        return plugin_name

    async def execute_tool(
        self,
        plugin_name: str,
        tool_name: str,
        arguments: dict | None = None,
    ) -> dict:
        """Вызвать инструмент плагина (tools/call → result).

        Таймаут: 30 секунд. Если плагин не ответил — убиваем процесс.
        """
        plugin = self._plugins.get(plugin_name)
        if not plugin or plugin["state"] == PluginState.STOPPED:
            raise RuntimeError(f"Плагин '{plugin_name}' недоступен")

        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }

        try:
            self._write_request(plugin_name, request)
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
        logger.info(f"Плагин '{plugin_name}' остановлен")

    def is_running(self, plugin_name: str) -> bool:
        """Проверить, работает ли процесс плагина."""
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return False
        return plugin["proc"].poll() is None
