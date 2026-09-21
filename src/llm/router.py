import json
import logging
import re
import time
from collections.abc import Iterable

from src.config import settings
from src.core.agent import (
    AGENT_PLAN_TOOL,
    PLANNER_REASK_LIMIT,
    PLANNER_TOOLS,
    REPLY_TOOL,
    build_plan,
    plan_is_direct,
    validate_plan,
)
from src.core.core_tools import CORE_TOOLS_SCHEMA, resolve_tool_risk
from src.core.timing import log_stage, span
from src.db import session as db_session
from src.db.database import async_session_factory
from src.db.session import ensure_session
from src.llm.client import HetznerClient
from src.llm.prompt import build_planner_messages, build_summary_messages
from src.plugins.manager import PluginManager

logger = logging.getLogger("loomcore.router")

# A tool call the model wrote as plain text instead of a structured call.
_TEXTUAL_CALL_RE = re.compile(r"\[tool\s+(?P<name>[A-Za-z0-9_]+)\s*\]")
_TOOL_CALL_TAG_RE = re.compile(r"<tool_call>\s*(?P<body>.*?)\s*</tool_call>", re.DOTALL)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)
# A tool result the model imitated from a leaked history note instead of acting.
_TEXTUAL_RESULT_RE = re.compile(r"Tool result\s*\((?P<name>[A-Za-z0-9_]+)\)")
# The planner sometimes prefixes the chat text with the name of the tool it
# chose ("reply: ...", "agent_plan: ..."). That marker is internal and must
# never reach the user or the stored history.
_TOOL_PREFIX_RE = re.compile(
    r"^\s*(?:reply|agent_plan)\s*(?::\s*|[-–—]\s+)", re.IGNORECASE
)
# The planner sometimes writes the tool name immediately followed by its JSON
# arguments ("agent_plan { ... }", "reply { "text": ... }"). That is a textual
# tool call, not chat text, and must be recovered instead of shown to the user.
_NAME_BRACED_JSON_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*"
    r"(?P<body>\{)"
)
# Any top-level JSON object that carries a "steps" array is a leaked plan.
_JSON_STEPS_RE = re.compile(r'"(?P<key>steps)"\s*:\s*\[')

# A provider that ignores tool_choice="required" sometimes returns the tool name
# as plain text. Such a token is not an answer and must never be shown or stored.
_BARE_TOOL_NAMES = frozenset({AGENT_PLAN_TOOL, REPLY_TOOL})
_MALFORMED_NUDGE = (
    "You wrote a tool name as plain text instead of calling the tool. Call "
    "`reply` with the user-facing message, or call `agent_plan` with the plan "
    "arguments; never write a tool name by itself."
)
_CONFUSED_REPLY = "Sorry, I didn't quite catch that — could you rephrase your request?"


def _strip_tool_prefix(text: str) -> str:
    """Remove a leading planner tool-name marker from assistant text."""
    cleaned = text
    while True:
        match = _TOOL_PREFIX_RE.match(cleaned)
        if match is None:
            return cleaned.strip()
        cleaned = cleaned[match.end() :]


def _is_bare_tool_name(text: str, known_tools: Iterable[str] = ()) -> bool:
    """True when ``text`` is only a tool name rather than a real message.

    Normalizes surrounding whitespace and quotes/backticks, then checks the
    planner tools plus the currently offered catalog names.
    """
    cleaned = text.strip().strip("`\"'").strip().lower()
    if not cleaned:
        return False
    return cleaned in _BARE_TOOL_NAMES or cleaned in known_tools


def _balanced_json(text: str, start: int) -> str | None:
    """Return the first brace-balanced ``{...}`` object at/after ``start``.

    A greedy regex would swallow trailing prose and break ``json.loads``; this
    tracks strings/escapes so the object ends at its real closing brace.
    """
    depth = 0
    in_string = False
    escaped = False
    begin = -1
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                begin = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and begin != -1:
                return text[begin : index + 1]
    return None


def _looks_like_tool_note(content: str) -> bool:
    """Detect text that is really a leaked tool note, not an answer.

    Older history stored tool output as an assistant-role note; replaying it made
    the model narrate results instead of calling ``agent_plan``. Such messages are
    dropped from the prompt and never re-created.
    """
    text = content.lstrip()
    return (
        text.startswith("[tool")
        or text.startswith("Tool result (")
        or text.startswith("Tool result:")
    )


def _leaked_plan_object(content: str) -> dict | None:
    """Return the first JSON object at/after a ``steps`` key, or None.

    Guards the text path: a plan the planner wrote as plain text must never be
    shown as a chat answer, even when the surrounding markup is unrecognized.
    """
    for match in _JSON_STEPS_RE.finditer(content):
        # Back up to the object that opened before this "steps" key.
        start = content.rfind("{", 0, match.start())
        if start == -1:
            continue
        raw = _balanced_json(content, start)
        if raw is None:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict) and payload.get("steps"):
            return payload
    return None


class AIRouter:
    """LLM request router: history -> planner -> plan or text.

    The model never receives the real tools. It must call exactly one planner
    tool each turn: ``agent_plan`` to act, or ``reply`` for plain conversation.

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

    async def aclose(self) -> None:
        """Close the router's shared HTTP client."""
        await self.client.aclose()

    async def route(
        self,
        user_message: str,
        session_id: str,
    ) -> dict:
        """Plan or answer a user request.

        1. Load history and persist the user message (short DB session).
        2. Gather the tool catalog and build planner messages.
        3. Ask the LLM to choose a planner tool (``agent_plan`` or ``reply``);
           the call is forced so an unstructured turn cannot slip through.
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
        tool_schemas = {
            tool["name"]: tool.get("inputSchema") or {} for tool in tools_schema
        }
        catalog = self._catalog_text(tools_schema)
        messages = build_planner_messages(user_message, history_dicts, catalog)

        logger.info(
            "Planner call: "
            f"user_msg='{user_message[:100]}...', "
            f"history_len={len(history_dicts)}, tools_count={len(tools_schema)}"
        )

        errors: list[str] = []
        bare_answer = False
        planner_started = time.monotonic()
        planner_attempts = 0
        try:
            for attempt in range(PLANNER_REASK_LIMIT + 1):
                planner_attempts += 1
                llm_response = await self.client.chat_completion(
                    messages,
                    tools=PLANNER_TOOLS,
                    tool_choice="required",
                    temperature=0,
                )
                choices = llm_response.get("choices", [])
                if not choices:
                    return {"type": "text", "content": "No response from the LLM"} | message_extra

                content, tool_name, tool_args, raw_tool_calls = self._parse_completion(
                    llm_response
                )

                bare_answer = False

                if tool_name == REPLY_TOOL:
                    reply_text = ""
                    if isinstance(tool_args, dict):
                        reply_text = str(tool_args.get("text") or "").strip()
                    reply_text = _strip_tool_prefix(reply_text or content)
                    if reply_text and not _is_bare_tool_name(reply_text, known_tools):
                        await self._persist_assistant(
                            session_id, reply_text, raw_tool_calls
                        )
                        return {"type": "text", "content": reply_text} | message_extra
                    # An empty or bare-name reply is a malformed completion.
                    bare_answer = True
                elif not raw_tool_calls:
                    recovered_args = self._recover_textual_plan_args(
                        content, user_message, known_tools
                    )
                    if recovered_args is not None and recovered_args.get("__reply__"):
                        # A textual "reply { ... }": recover it as chat text.
                        reply_text = _strip_tool_prefix(
                            str(recovered_args.get("text") or "")
                        )
                        if reply_text and not _is_bare_tool_name(reply_text, known_tools):
                            await self._persist_assistant(session_id, reply_text, None)
                            return {"type": "text", "content": reply_text} | message_extra
                        bare_answer = True
                    elif recovered_args is None:
                        answer = _strip_tool_prefix(content)
                        if (
                            answer
                            and not _is_bare_tool_name(answer, known_tools)
                            and _leaked_plan_object(answer) is None
                        ):
                            await self._persist_assistant(session_id, answer, None)
                            return {"type": "text", "content": answer} | message_extra
                        bare_answer = True
                    else:
                        logger.warning(
                            "Recovered a tool call the planner wrote as text "
                            f"(session={session_id}, "
                            f"steps={len(recovered_args.get('steps', []))})"
                        )
                        tool_name = AGENT_PLAN_TOOL
                        tool_args = recovered_args

                if bare_answer:
                    logger.warning(
                        "Planner returned a bare tool name as text "
                        f"(session={session_id}, content={content!r})"
                    )
                    errors = ["the planner wrote a bare tool name instead of calling it"]
                elif tool_name == AGENT_PLAN_TOOL:
                    plan = build_plan(tool_args or {}, risk_of=resolve_tool_risk)
                    errors = validate_plan(plan, known_tools, tool_schemas)
                    if not errors:
                        # A direct one-step action leaves no plan artifact; only
                        # multi-step plans are persisted as "plan" rows.
                        if not plan_is_direct(plan):
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
                                _MALFORMED_NUDGE
                                if bare_answer
                                else (
                                    "That plan was rejected: "
                                    + "; ".join(errors)
                                    + ". Call agent_plan again with a corrected plan "
                                    "that uses only catalog tool names."
                                )
                            ),
                        }
                    )

            if bare_answer:
                await self._persist_assistant(session_id, _CONFUSED_REPLY, None)
                return {"type": "text", "content": _CONFUSED_REPLY} | message_extra

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

    async def tool_schemas(self) -> dict[str, dict]:
        """Return the input schema of every tool currently offered to the planner."""
        tools_schema, _extra = await self._collect_tools()
        return {tool["name"]: tool.get("inputSchema") or {} for tool in tools_schema}

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

        Only user and assistant text reach the chat API. Tool cards and plans are
        dropped: the API has no matching tool_call id for them, and replaying a
        tool result as assistant narration taught the model to narrate results
        instead of calling ``agent_plan``. Assistant messages that are themselves
        a leaked tool note or a bare tool name (from older history) are dropped for
        the same reason. Empty and unknown roles are dropped too, so a malformed
        history can never produce a 400.
        """
        api_messages: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content") or ""
            if role not in ("user", "assistant") or not content:
                continue
            if role == "assistant" and (
                _looks_like_tool_note(content)
                or _is_bare_tool_name(content)
                or _leaked_plan_object(content) is not None
            ):
                continue
            api_messages.append({"role": role, "content": content})
        return api_messages

    @staticmethod
    def _recover_textual_plan_args(
        content: str, user_message: str, known_tools: set[str]
    ) -> dict | None:
        """Recover a plan the model wrote as text instead of a real tool call.

        Some completions return the intended call as prose (``[tool name] {...}``,
        ``name { ... }`` without any wrapper, ``<tool_call>{...}</tool_call>``, or
        a narrated ``Tool result (name)``). Treat those as ``agent_plan``
        arguments — a real tool name becomes a one-step direct action — so the
        request still runs instead of surfacing as a chat answer. A textual
        ``reply`` returns a ``{"__reply__": True, "text": ...}`` sentinel so the
        caller can answer in chat. Returns None when nothing usable parses.
        """
        if not content:
            return None

        candidates: list[tuple[str, object]] = []
        for match in _TEXTUAL_CALL_RE.finditer(content):
            candidates.append((match.group("name"), _balanced_json(content, match.end()) or ""))
        for match in _NAME_BRACED_JSON_RE.finditer(content):
            candidates.append(
                (match.group("name"), _balanced_json(content, match.start("body")) or "")
            )
        for match in _TEXTUAL_RESULT_RE.finditer(content):
            candidates.append((match.group("name"), ""))
        for match in _FENCED_JSON_RE.finditer(content):
            raw = _balanced_json(match.group("body"), 0)
            if raw is None:
                continue
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("steps"):
                name = str(payload.get("name") or payload.get("tool") or AGENT_PLAN_TOOL)
                candidates.append((name, payload.get("arguments", payload.get("args", payload))))
        for match in _TOOL_CALL_TAG_RE.finditer(content):
            try:
                payload = json.loads(match.group("body"))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                name = str(payload.get("name") or payload.get("tool") or "")
                args = payload.get("arguments", payload.get("args", {}))
                if name:
                    candidates.append((name, args))

        for name, raw_args in candidates:
            args = raw_args
            if isinstance(args, str):
                args = args.strip()
                try:
                    args = json.loads(args) if args else {}
                except (json.JSONDecodeError, TypeError):
                    args = {}
            if not isinstance(args, dict):
                args = {}

            if name == AGENT_PLAN_TOOL:
                if args.get("steps"):
                    return args
                continue
            if name == REPLY_TOOL:
                # Unwrapped "reply { ... }": surface it as chat text, not a plan.
                if isinstance(args.get("text"), str) and args["text"].strip():
                    return {"__reply__": True, "text": args["text"]}
                continue
            if name in known_tools:
                # A real tool named directly in prose: run it as one direct step.
                return {
                    "mode": "direct",
                    "goal": user_message,
                    "steps": [
                        {
                            "tool": name,
                            "args": args,
                            "why": f"Recovered a {name} call written as text.",
                        }
                    ],
                }
        return None

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
        choices = llm_response.get("choices") or []
        if not choices:
            return "", None, None, None
        message = choices[0].get("message", {})
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
