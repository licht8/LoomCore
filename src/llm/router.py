import json
import logging
import time

from src.config import settings
from src.core.agent import (
    AGENT_PLAN_SCHEMA,
    AGENT_PLAN_TOOL,
    PLANNER_REASK_LIMIT,
    build_plan,
    validate_plan,
)
from src.core.core_tools import CORE_TOOLS_SCHEMA
from src.core.timing import log_stage, span
from src.db import session as db_session
from src.db.database import async_session_factory
from src.db.session import ensure_session
from src.llm.client import HetznerClient
from src.llm.prompt import build_planner_messages, build_summary_messages
from src.plugins.manager import PluginManager

logger = logging.getLogger("loomcore.router")


class AIRouter:
    """LLM request router: history -> planner -> plan or text.

    The model never receives callable tools. It either proposes a plan through
    the single ``agent_plan`` tool or answers in plain text.

    Shape of the dict returned by route():
        {
            "type": "text" | "plan",
            "content": str | None,      # Text response (type == "text")
            "plan": dict | None,        # Plan object (type == "plan")
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
        """Plan or answer a user request.

        1. Load history and persist the user message (short DB session).
        2. Gather the tool catalog and build planner messages.
        3. Ask the LLM for a plan (its only callable tool is ``agent_plan``).
        4. Validate the plan; re-ask once when it is malformed.
        5. Persist and return the plan, or the plain-text answer.
        """
        route_started = time.monotonic()
        history_dicts = await self._begin_turn(session_id, user_message)

        collect_started = time.monotonic()
        tools_schema, message_extra = await self._collect_tools()
        log_stage(
            "collect_tools", time.monotonic() - collect_started, tools=len(tools_schema)
        )
        known_tools = {tool["name"] for tool in tools_schema}
        catalog = self._catalog_text(tools_schema)
        messages = build_planner_messages(user_message, history_dicts, catalog)

        logger.info(
            "Planner call: "
            f"user_msg='{user_message[:100]}...', "
            f"history_len={len(history_dicts)}, tools_count={len(tools_schema)}"
        )

        errors: list[str] = []
        planner_started = time.monotonic()
        planner_attempts = 0
        try:
            for attempt in range(PLANNER_REASK_LIMIT + 1):
                planner_attempts += 1
                llm_response = await self.client.chat_completion(
                    messages, tools=[AGENT_PLAN_SCHEMA]
                )
                choices = llm_response.get("choices", [])
                if not choices:
                    return {"type": "text", "content": "No response from the LLM"} | message_extra

                content, tool_name, tool_args, raw_tool_calls = self._parse_completion(
                    llm_response
                )

                if not raw_tool_calls:
                    await self._persist_assistant(session_id, content, None)
                    return {"type": "text", "content": content} | message_extra

                if tool_name == AGENT_PLAN_TOOL:
                    plan = build_plan(tool_args or {})
                    errors = validate_plan(plan, known_tools)
                    if not errors:
                        await self._persist_plan(session_id, plan)
                        return {"type": "plan", "plan": plan} | message_extra
                else:
                    errors = [f"unexpected tool: {tool_name}"]

                if attempt < PLANNER_REASK_LIMIT:
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "That plan was rejected: "
                                + "; ".join(errors)
                                + ". Call agent_plan again with a corrected plan that "
                                "uses only catalog tool names."
                            ),
                        }
                    )

            failure = "I could not form a valid plan: " + "; ".join(errors)
            await self._persist_assistant(session_id, failure, None)
            return {"type": "text", "content": failure} | message_extra
        finally:
            log_stage(
                "planner_loop",
                time.monotonic() - planner_started,
                attempts=planner_attempts,
                tools=len(tools_schema),
            )
            log_stage(
                "route_total",
                time.monotonic() - route_started,
                history_len=len(history_dicts),
            )

    async def summarize_plan(
        self,
        user_message: str,
        plan: dict,
        observations: list[dict],
    ) -> str:
        """Produce the SOUL-voiced summary for a completed plan."""
        messages = build_summary_messages(user_message, plan, observations)
        async with span("summary_call", steps=len(plan.get("steps", []))):
            llm_response = await self.client.chat_completion(messages)
            content, _tool_name, _tool_args, _raw = self._parse_completion(llm_response)
        return content

    async def known_tool_names(self) -> set[str]:
        """Return the names of every tool currently offered to the planner."""
        tools_schema, _extra = await self._collect_tools()
        return {tool["name"] for tool in tools_schema}

    async def _begin_turn(self, session_id: str, user_message: str) -> list[dict]:
        """Load recent history and persist the user message; return history dicts."""
        started = time.monotonic()
        async with async_session_factory() as db:
            history_messages = await db_session.get_session_history(
                db, session_id, limit=50
            )

            # Ensure the session exists (create the user and session if needed)
            await ensure_session(db, session_id)

            # Convert ORM models to dicts, then sanitize to API-safe roles
            history_dicts = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                }
                for msg in history_messages
            ]

            # Save the user message to history
            await db_session.add_user_message(db, session_id, user_message)

        log_stage(
            "history_load",
            time.monotonic() - started,
            history_len=len(history_dicts),
            history_chars=sum(
                len(str(message.get("content") or "")) for message in history_dicts
            ),
        )
        return self._api_history(history_dicts)

    @staticmethod
    def _api_history(messages: list[dict]) -> list[dict]:
        """Sanitize stored history into roles the chat API accepts.

        Internal roles must never reach the chat API: ``tool`` messages are
        folded into an assistant note (the API has no matching tool_call id) and
        ``plan`` messages are dropped entirely. Empty and unknown roles are
        dropped too, so a malformed history can never produce a 400.
        """
        api_messages: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content") or ""
            if role in ("user", "assistant"):
                if content:
                    api_messages.append({"role": role, "content": content})
            elif role == "tool":
                note = AIRouter._tool_note(message, content)
                if note:
                    api_messages.append({"role": "assistant", "content": note})
        return api_messages

    @staticmethod
    def _tool_note(message: dict, content: str) -> str:
        """Fold a stored tool card into a plain assistant note (best-effort)."""
        calls = message.get("tool_calls") or []
        name = ""
        if calls and isinstance(calls[0], dict):
            name = str(calls[0].get("name") or "")
        if not name and not content:
            return ""
        label = f"[tool {name}]" if name else "[tool]"
        return f"{label} {content}".rstrip()

    async def _persist_assistant(
        self, session_id: str, content: str, raw_tool_calls: list | None
    ) -> None:
        """Persist the assistant response in its own short DB session."""
        async with async_session_factory() as db:
            await db_session.add_assistant_message(
                db, session_id, content, raw_tool_calls
            )

    async def _persist_plan(self, session_id: str, plan: dict) -> None:
        """Persist the agent plan in its own short DB session."""
        async with async_session_factory() as db:
            await db_session.add_plan_message(db, session_id, plan)

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
    def _catalog_text(tools_schema: list[dict]) -> str:
        """Render the tool catalog as plain text for the planner prompt."""
        if not tools_schema:
            return "(no tools available)"
        lines = []
        for tool in tools_schema:
            schema = tool.get("inputSchema") or {}
            properties = schema.get("properties") if isinstance(schema, dict) else None
            arg_names = ", ".join(properties.keys()) if isinstance(properties, dict) else ""
            lines.append(
                f"- {tool.get('name')}({arg_names}): {tool.get('description', '')}"
            )
        return "\n".join(lines)

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
