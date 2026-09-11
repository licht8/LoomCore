import json
import logging

from src.config import settings
from src.db import session as db_session
from src.db.database import async_session_factory
from src.db.session import ensure_session
from src.llm.client import HetznerClient
from src.llm.prompt import build_messages
from src.plugins.manager import PluginManager

logger = logging.getLogger("loomhub.router")


class AIRouter:
    """Маршрутизатор запросов к LLM: история -> промпт -> запрос -> парсинг.

    Формат возвращаемого словаря из route():
        {
            "type": "text" | "tool_call",
            "content": str | None,       # Текстовый ответ
            "tool_name": str | None,     # Если вызов инструмента
            "tool_args": dict | None,    # Аргументы вызова
        }
    """

    def __init__(self, plugin_manager: PluginManager | None = None):
        self.client = HetznerClient(
            api_key=settings.hetzner_api_key,
        )
        self.plugin_manager = plugin_manager

    async def route(
        self,
        user_message: str,
        session_id: str,
    ) -> dict:
        """Обработать запрос пользователя и вернуть ответ.

        1. Загрузить историю через db_session.
        2. Сформировать сообщения через prompt.build_messages.
        3. Отправить запрос в HetznerClient.
        4. Распарсить ответ: tool_calls или текст.
        5. Сохранить ответ ассистента через db_session.add_assistant_message.
        """
        # 1. Загружаем историю
        async with async_session_factory() as db:
            history_messages = await db_session.get_session_history(
                db, session_id, limit=50
            )

            # Убедиться, что сессия существует (создать пользователя и сессию если нет)
            await ensure_session(db, session_id)

            # Преобразуем ORM-модели в dicts для LLM
            history_dicts = [
                {"role": msg.role, "content": msg.content}
                for msg in history_messages
            ]

            # 0.5. Сохраняем пользовательское сообщение в историю
            await db_session.add_user_message(
                db, session_id, user_message
            )

            # 2. Формируем messages (с инструментами, если есть plugin_manager)
            tools_schema = []
            unavailable_plugins: list[str] = []
            if self.plugin_manager:
                try:
                    tools_schema = await self.plugin_manager.get_all_tools()
                except Exception as exc:
                    logger.warning(f"Не удалось получить инструменты плагина: {exc}")
                    unavailable_plugins.append("все плагины")

            # Если некоторые инструменты недоступны, фиксируем это
            message_extra = {}
            if unavailable_plugins:
                logger.warning(f"Недоступные плагины при маршрутизации: {unavailable_plugins}")
                message_extra["tool_schema_warning"] = unavailable_plugins

            messages = build_messages(user_message, history_dicts, tools_schema)
            llm_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "inputSchema",
                            {"type": "object", "properties": {}, "required": []},
                        ),
                    },
                }
                for tool in tools_schema
            ]

            # 3. Отправляем запрос к LLM
            logger.info(f"🚀 Вызов LLM: user_msg='{user_message[:100]}...', history_len={len(history_dicts)}, tools_count={len(tools_schema)}")
            llm_response = await self.client.chat_completion(messages, tools=llm_tools)

            # 4. Парсим ответ LLM
            choices = llm_response.get("choices", [])
            finish_reason = choices[0].get("finish_reason", "N/A") if choices else "N/A"
            logger.info(f"✅ LLM API ответ: finish_reason={finish_reason}")

            if not choices:
                return {"type": "text", "content": "Нет ответа от LLM"}

            message = choices[0].get("message", {})
            content = message.get("content") or ""
            raw_tool_calls = message.get("tool_calls")

            # Явный разбор tool_calls
            tool_name: str | None = None
            tool_args: dict | None = None

            if raw_tool_calls:
                tc = raw_tool_calls[0]
                func = tc.get("function", {})
                tool_name = func.get("name")
                args_str = func.get("arguments", "{}")
                try:
                    tool_args = json.loads(args_str)
                except (json.JSONDecodeError, TypeError):
                    tool_args = {"_raw": args_str}

            # Определяем тип ответа
            answer_type = "tool_call" if tool_name else "text"

            # Сохраняем ответ ассистента через db_session (SRP)
            await db_session.add_assistant_message(
                db, session_id, content, raw_tool_calls
            )

            return {
                "type": answer_type,
                "content": content if answer_type == "text" else None,
                "tool_name": tool_name,
                "tool_args": tool_args,
            } | message_extra
