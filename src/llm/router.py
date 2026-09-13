import json
import logging

from src.config import settings
from src.core.core_tools import CORE_TOOLS_SCHEMA
from src.db import session as db_session
from src.db.database import async_session_factory
from src.db.session import ensure_session
from src.llm.client import HetznerClient
from src.llm.prompt import build_messages
from src.plugins.manager import PluginManager

logger = logging.getLogger("loomcore.router")


class AIRouter:
    """LLM request router: history -> prompt -> request -> parsing.

    Shape of the dict returned by route():
        {
            "type": "text" | "tool_call",
            "content": str | None,       # Text response
            "tool_name": str | None,     # Set when a tool is called
            "tool_args": dict | None,    # Tool call arguments
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
        """Handle a user request and return the response.

        1. Load history and persist the user message (short DB session).
        2. Gather tools and build the messages.
        3. Send the request to HetznerClient (no DB session held).
        4. Parse the response: tool_calls or text.
        5. Persist the assistant response (second short DB session).
        """
        history_dicts = await self._begin_turn(session_id, user_message)

        tools_schema, message_extra = await self._collect_tools()
        messages = build_messages(user_message, history_dicts)
        llm_tools = self._build_llm_tools(tools_schema)

        # Send the request to the LLM
        logger.info(
            f"🚀 LLM call: user_msg='{user_message[:100]}...', "
            f"history_len={len(history_dicts)}, tools_count={len(tools_schema)}"
        )
        llm_response = await self.client.chat_completion(messages, tools=llm_tools)

        choices = llm_response.get("choices", [])
        finish_reason = choices[0].get("finish_reason", "N/A") if choices else "N/A"
        logger.info(f"✅ LLM API response: finish_reason={finish_reason}")

        if not choices:
            return {"type": "text", "content": "No response from the LLM"}

        content, tool_name, tool_args, raw_tool_calls = self._parse_completion(llm_response)
        answer_type = "tool_call" if tool_name else "text"

        await self._persist_assistant(session_id, content, raw_tool_calls)

        return {
            "type": answer_type,
            "content": content if answer_type == "text" else None,
            "tool_name": tool_name,
            "tool_args": tool_args,
        } | message_extra

    async def _begin_turn(self, session_id: str, user_message: str) -> list[dict]:
        """Load recent history and persist the user message; return history dicts."""
        async with async_session_factory() as db:
            history_messages = await db_session.get_session_history(
                db, session_id, limit=50
            )

            # Ensure the session exists (create the user and session if needed)
            await ensure_session(db, session_id)

            # Convert ORM models to dicts for the LLM
            history_dicts = [
                {"role": msg.role, "content": msg.content}
                for msg in history_messages
            ]

            # Save the user message to history
            await db_session.add_user_message(db, session_id, user_message)

        return history_dicts

    async def _persist_assistant(
        self, session_id: str, content: str, raw_tool_calls: list | None
    ) -> None:
        """Persist the assistant response in its own short DB session."""
        async with async_session_factory() as db:
            await db_session.add_assistant_message(
                db, session_id, content, raw_tool_calls
            )

    async def _collect_tools(self) -> tuple[list[dict], dict]:
        """Fetch the tool schema; return it with any 'unavailable' warning."""
        tools_schema: list[dict] = list(CORE_TOOLS_SCHEMA)
        unavailable_plugins: list[str] = []
        if self.plugin_manager:
            try:
                tools_schema.extend(await self.plugin_manager.get_all_tools())
            except Exception as exc:
                logger.warning(f"Failed to fetch plugin tools: {exc}")
                unavailable_plugins.append("all plugins")

        message_extra: dict = {}
        if unavailable_plugins:
            logger.warning(f"Unavailable plugins during routing: {unavailable_plugins}")
            message_extra["tool_schema_warning"] = unavailable_plugins
        return tools_schema, message_extra

    @staticmethod
    def _build_llm_tools(tools_schema: list[dict]) -> list[dict]:
        """Map MCP tool schemas to the OpenAI function-tool format."""
        return [
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

    @staticmethod
    def _parse_completion(
        llm_response: dict,
    ) -> tuple[str, str | None, dict | None, list | None]:
        """Extract (content, tool_name, tool_args, raw_tool_calls) from a response."""
        message = llm_response.get("choices", [{}])[0].get("message", {})
        content = message.get("content") or ""
        raw_tool_calls = message.get("tool_calls")

        tool_name: str | None = None
        tool_args: dict | None = None
        if raw_tool_calls:
            func = raw_tool_calls[0].get("function", {})
            tool_name = func.get("name")
            args_str = func.get("arguments", "{}")
            try:
                tool_args = json.loads(args_str)
            except (json.JSONDecodeError, TypeError):
                tool_args = {"_raw": args_str}

        return content, tool_name, tool_args, raw_tool_calls
