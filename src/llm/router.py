import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable

from src.core.agent import (
    AGENT_PLAN_TOOL,
    PLANNER_REASK_LIMIT,
    PLANNER_TOOLS,
    REPLY_TOOL,
    build_plan,
    plan_is_direct,
    validate_plan,
)
from src.core.core_tools import CORE_TOOLS_SCHEMA, dispatch_core_tool, resolve_tool_risk
from src.core.timing import log_stage, span
from src.db import session as db_session
from src.db.database import async_session_factory
from src.db.session import ensure_session
from src.llm.client import OpenAICompatClient
from src.llm.config import NOT_CONFIGURED_MESSAGE, get_active
from src.llm.prompt import build_planner_messages, build_summary_messages
from src.plugins.manager import PluginManager

logger = logging.getLogger("loomcore.router")

# Longest tool description rendered in the planner catalog (chars, before the
# ellipsis); a verbose plugin description must not bloat the system prompt.
_MAX_TOOL_DESC_CHARS = 160

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

# Meta fast-path: answer short, explicit tool/plugin-listing requests without an
# LLM call. Deliberately conservative — anything longer or less explicit falls
# through to the planner.
_META_MAX_CHARS = 120
_META_TOOLS_RE = re.compile(
    r"(?:list|show|see|which|what)\b[^?]*\btools?\b"
    r"|\btools?\s+(?:for|of)\b"
    r"|(?:какие|покажи|список)\s+инструмент",
    re.IGNORECASE,
)
_META_PLUGINS_RE = re.compile(
    r"(?:list|show|which|what)\b[^?]*\bplugins?\b"
    r"|(?:какие|покажи|список)\s+плагин",
    re.IGNORECASE,
)
_META_PLUGIN_TOKEN_RE = re.compile(r"\b(LoomOps|LoomWG|LoomCore)\b", re.IGNORECASE)
_META_PLUGIN_AFTER_RE = re.compile(
    r"(?:for|of|для)\s+([A-Za-z_][\w-]*)", re.IGNORECASE
)


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
    return _bare_tool_name(text, known_tools) is not None


def _bare_tool_name(text: str, known_tools: Iterable[str] = ()) -> str | None:
    """Return the normalized tool name when ``text`` is only that name, else None."""
    cleaned = text.strip().strip("`\"'").strip().lower()
    if not cleaned:
        return None
    if cleaned in _BARE_TOOL_NAMES or cleaned in known_tools:
        return cleaned
    return None


def _tool_text(result: object) -> str | None:
    """Return the text of an MCP-shaped tool result, or None when malformed."""
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict):
        return None
    text = first.get("text")
    return text if isinstance(text, str) and text else None


def _plan_reply_step_text(raw_args: object) -> str | None:
    """Return the answer text when a plan is a single ``reply`` step.

    Some planners wrap a plain answer in an ``agent_plan`` whose only step calls
    ``reply``; that is chat text, not an action, and must not be validated as a
    plan (``reply`` is not a catalog tool).
    """
    if not isinstance(raw_args, dict):
        return None
    steps = raw_args.get("steps")
    if not isinstance(steps, list) or len(steps) != 1:
        return None
    step = steps[0]
    if not isinstance(step, dict) or step.get("tool") != REPLY_TOOL:
        return None
    args = step.get("args")
    text = str(args.get("text") or "") if isinstance(args, dict) else ""
    if not text.strip():
        text = str(raw_args.get("goal") or step.get("why") or "")
    text = _strip_tool_prefix(text)
    if text and not _is_bare_tool_name(text):
        return text
    return None


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
        self.client = OpenAICompatClient()
        self.plugin_manager = plugin_manager

    async def aclose(self) -> None:
        """Close the router's shared HTTP client."""
        await self.client.aclose()

    async def _meta_fast_path(self, user_message: str) -> str | None:
        """Answer a short tool/plugin-listing request without an LLM call.

        Returns the rendered text, or None to let the planner handle the turn
        (unknown intent, too long, or any error while listing).
        """
        if len(user_message) > _META_MAX_CHARS:
            return None
        if _META_TOOLS_RE.search(user_message):
            tool_name = "core_list_tools"
        elif _META_PLUGINS_RE.search(user_message):
            tool_name = "core_list_plugins"
        else:
            return None

        token = ""
        match = _META_PLUGIN_TOKEN_RE.search(user_message)
        if match:
            token = match.group(1)
        else:
            after = _META_PLUGIN_AFTER_RE.search(user_message)
            if after:
                token = after.group(1)
        if token.lower() == "loomcore":
            token = "core"
        arguments = {"plugin": token} if (token and tool_name == "core_list_tools") else {}
        try:
            result = await dispatch_core_tool(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001 - fall back to the planner
            logger.warning(f"Meta fast-path lookup failed: {exc}")
            return None
        return _tool_text(result)

    async def route(
        self,
        user_message: str,
        session_id: str,
        *,
        on_delta: Callable[..., Awaitable[None]] | None = None,
    ) -> dict:
        """Plan or answer a user request.

        1. Load history and persist the user message (short DB session).
        2. Gather the tool catalog and build planner messages.
        3. Ask the LLM to choose a planner tool (``agent_plan`` or ``reply``);
           the call is forced so an unstructured turn cannot slip through.
        4. Validate the plan; re-ask once when it is malformed.
        5. Persist and return the plan, or the plain-text answer.

        When ``on_delta`` is set, the planner is streamed and the ``reply``
        tool's ``text`` argument is forwarded incrementally; the streamer is
        reset before every re-ask so two attempts never concatenate.
        """
        config = get_active()
        if not config.configured:
            # Persist the turn even without a provider: the user message and the
            # notice stay in history so they survive a reload and are valid
            # context once an admin configures a provider.
            await self._begin_turn(session_id, user_message)
            await self._persist_assistant(session_id, NOT_CONFIGURED_MESSAGE, None)
            return {"type": "text", "content": NOT_CONFIGURED_MESSAGE}

        route_started = time.monotonic()
        history_dicts = await self._begin_turn(session_id, user_message)

        # High-confidence tool/plugin listings never need an LLM turn.
        fast = await self._meta_fast_path(user_message)
        if fast:
            await self._persist_assistant(session_id, fast, None)
            return {"type": "text", "content": fast}

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
            "Planner call: history_len=%d, tools_count=%d, message_chars=%d",
            len(history_dicts),
            len(tools_schema),
            len(user_message),
        )

        errors: list[str] = []
        bare_answer = False
        planner_started = time.monotonic()
        planner_attempts = 0
        reply_stream = _ReplyTextStream() if on_delta is not None else None
        goal_stream = _GoalTextStream() if on_delta is not None else None
        try:
            for attempt in range(PLANNER_REASK_LIMIT + 1):
                planner_attempts += 1
                reask_nudge: str | None = None
                if reply_stream is not None and attempt > 0:
                    # A re-ask must not append to the previous attempt's text.
                    reply_stream.reset()
                    goal_stream.reset()
                    await on_delta("", reset=True)
                if on_delta is not None:
                    llm_response = await self.client.stream_chat_completion(
                        messages,
                        tools=PLANNER_TOOLS,
                        tool_choice="required",
                        temperature=0,
                        model=config.planner_model or None,
                        max_tokens=config.planner_max_tokens,
                        purpose="planner",
                        session_id=session_id,
                        on_tool_delta=_make_reply_streamer(
                            reply_stream, goal_stream, on_delta
                        ),
                    )
                else:
                    llm_response = await self.client.chat_completion(
                        messages,
                        tools=PLANNER_TOOLS,
                        tool_choice="required",
                        temperature=0,
                        model=config.planner_model or None,
                        max_tokens=config.planner_max_tokens,
                        purpose="planner",
                        session_id=session_id,
                    )
                choices = llm_response.get("choices", [])
                if not choices:
                    return {"type": "text", "content": "No response from the LLM"} | message_extra

                content, tool_name, tool_args, raw_tool_calls = self._parse_completion(
                    llm_response
                )

                bare_answer = False
                bare_text = ""

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
                    bare_text = reply_text
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
                        bare_text = reply_text
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
                        bare_text = answer
                    else:
                        logger.warning(
                            "Recovered a tool call the planner wrote as text "
                            f"(session={session_id}, "
                            f"steps={len(recovered_args.get('steps', []))})"
                        )
                        tool_name = AGENT_PLAN_TOOL
                        tool_args = recovered_args

                if bare_answer:
                    candidate = _bare_tool_name(bare_text, known_tools)
                    if candidate is not None and candidate in known_tools:
                        # The planner named a real catalog tool instead of calling
                        # it: run it as a direct one-step action.
                        raw = {
                            "goal": user_message,
                            "mode": "direct",
                            "steps": [
                                {
                                    "tool": candidate,
                                    "args": {},
                                    "why": "The user asked for this tool.",
                                }
                            ],
                        }
                        plan = build_plan(raw, risk_of=resolve_tool_risk)
                        errors = validate_plan(plan, known_tools, tool_schemas)
                        if not errors:
                            if not plan_is_direct(plan):
                                await self._persist_plan(session_id, plan)
                            return {"type": "plan", "plan": plan} | message_extra
                        logger.warning(
                            "Planner named a tool that needs arguments instead of "
                            f"calling it (session={session_id}, tool={candidate})"
                        )
                        bare_answer = False
                        errors = [f"missing required arguments for '{candidate}'"]
                        reask_nudge = (
                            "Call agent_plan with the required arguments for "
                            f"'{candidate}'."
                        )
                    else:
                        logger.warning(
                            "Planner returned a bare tool name as text "
                            f"(session={session_id}, content={content!r})"
                        )
                        errors = [
                            "the planner wrote a bare tool name instead of calling it"
                        ]
                elif tool_name == AGENT_PLAN_TOOL:
                    reply_step_text = _plan_reply_step_text(tool_args)
                    if reply_step_text is not None:
                        # A `reply` step is a plain answer, not an action.
                        await self._persist_assistant(
                            session_id, reply_step_text, raw_tool_calls
                        )
                        return {
                            "type": "text",
                            "content": reply_step_text,
                        } | message_extra
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
                    if content:
                        messages.append({"role": "assistant", "content": content})
                    if reask_nudge is not None:
                        nudge = reask_nudge
                    elif bare_answer:
                        nudge = _MALFORMED_NUDGE
                    else:
                        nudge = (
                            "That plan was rejected: "
                            + "; ".join(errors)
                            + ". Call agent_plan again with a corrected plan "
                            "that uses only catalog tool names."
                        )
                    messages.append({"role": "user", "content": nudge})

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
        *,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Produce the SOUL-voiced summary for a completed plan.

        When ``on_delta`` is set the summary is streamed and each text fragment
        is forwarded as it arrives.
        """
        config = get_active()
        if not config.configured:
            return NOT_CONFIGURED_MESSAGE
        messages = build_summary_messages(user_message, plan, observations)
        async with span("summary_call", steps=len(plan.get("steps", []))):
            if on_delta is not None:
                llm_response = await self.client.stream_chat_completion(
                    messages,
                    model=config.summary_model or None,
                    max_tokens=config.summary_max_tokens,
                    purpose="summary",
                    on_content=on_delta,
                )
            else:
                llm_response = await self.client.chat_completion(
                    messages,
                    model=config.summary_model or None,
                    max_tokens=config.summary_max_tokens,
                    purpose="summary",
                )
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
            name = match.group("name")
            if name not in known_tools and name not in _BARE_TOOL_NAMES:
                continue
            raw = _balanced_json(content, match.start("body"))
            if raw is None:
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                candidates.append((name, parsed))
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
                tools_schema.extend(await self.plugin_manager.get_tools_for_routing())
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
            description = str(tool.get("description", ""))
            if len(description) > _MAX_TOOL_DESC_CHARS:
                description = description[:_MAX_TOOL_DESC_CHARS] + "…"
            lines.append(f"- {tool.get('name')}({arg_names}): {description}")
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


def _decode_json_string_prefix(raw: str, start: int) -> tuple[str, int, bool]:
    """Decode a JSON string body from ``start`` (after the opening quote).

    Returns ``(decoded, next_pos, done)``. ``next_pos`` is where decoding should
    resume on the next call: the raw offset of a trailing partial escape, or
    ``len(raw)`` while the string is still open. ``done`` is True once the
    closing quote is reached (``next_pos`` is then just past it). A high
    surrogate is held back until its low counterpart arrives, so astral
    characters stream as one code point and never leave a lone surrogate that
    would fail UTF-8 encoding.
    """
    simple = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
    }
    out: list[str] = []
    i, n = start, len(raw)
    while i < n:
        ch = raw[i]
        if ch == '"':
            return "".join(out), i + 1, True
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= n:
            break
        esc = raw[i + 1]
        if esc in simple:
            out.append(simple[esc])
            i += 2
        elif esc == "u":
            if i + 6 > n:
                break
            code = raw[i + 2 : i + 6]
            try:
                value = int(code, 16)
            except ValueError:
                out.append("\\u" + code)
                i += 6
                continue
            if 0xD800 <= value <= 0xDBFF:
                # A high surrogate needs its low counterpart. Inspect what
                # follows first: if it is not a ``\u`` escape at all, the
                # surrogate is lone and must be replaced (not held back). Only
                # wait for the next chunk when the following escape is itself
                # incomplete.
                nxt = raw[i + 6 : i + 8]
                if len(nxt) < 2:
                    break
                if nxt != "\\u":
                    out.append("\ufffd")
                    i += 6
                    continue
                if i + 12 > n:
                    break
                try:
                    low = int(raw[i + 8 : i + 12], 16)
                except ValueError:
                    low = -1
                if 0xDC00 <= low <= 0xDFFF:
                    out.append(
                        chr(0x10000 + ((value - 0xD800) << 10) + (low - 0xDC00))
                    )
                    i += 12
                else:
                    out.append("\ufffd")
                    i += 6
            elif 0xDC00 <= value <= 0xDFFF:
                # A lone low surrogate cannot be encoded; replace it.
                out.append("\ufffd")
                i += 6
            else:
                out.append(chr(value))
                i += 6
        else:
            out.append(esc)
            i += 2
    return "".join(out), i, False


class _ToolFieldStream:
    """Incrementally extract one string field of a streamed tool call."""

    def __init__(self, tool: str, key: str) -> None:
        self._tool = tool
        self._key_re = re.compile(rf'"{re.escape(key)}"\s*:\s*"')
        self._active = False
        self._done = False
        # Raw offset in the accumulated arguments to resume decoding from; None
        # until the field has been seen.
        self._pos: int | None = None
        # Buffer seen on the previous call, used to detect a silent restart
        # (see ``feed``).
        self._buf = ""

    def reset(self) -> None:
        """Discard state so a re-ask starts from a clean slate."""
        self._active = False
        self._done = False
        self._pos = None
        self._buf = ""

    def feed(self, name: str | None, args_so_far: str) -> str:
        """Return the newly decoded field text for the accumulated arguments.

        Decoding itself is linear overall: only the suffix appended since the
        previous call is decoded. Detecting a silent restart additionally compares
        the whole buffer against the previous one, which is O(n) per delta, but
        the streamed text is bounded by the configured max tokens, so this stays
        cheap.
        """
        if name is not None:
            self._active = name == self._tool
        if not args_so_far.startswith(self._buf):
            # The client restarted this call internally (a retry after tool
            # deltas that never forwarded text) without notifying us. The new
            # buffer is not an extension of the old one, so the resume offset is
            # stale and decoding must restart from the beginning.
            self._pos = None
            self._done = False
        self._buf = args_so_far
        if not self._active or self._done:
            return ""
        if self._pos is None:
            match = self._key_re.search(args_so_far)
            if match is None:
                return ""
            self._pos = match.end()
        decoded, self._pos, done = _decode_json_string_prefix(args_so_far, self._pos)
        self._done = done
        return decoded


class _ReplyTextStream(_ToolFieldStream):
    """Incrementally extract the ``text`` argument of a streamed ``reply``."""

    def __init__(self) -> None:
        super().__init__(REPLY_TOOL, "text")


class _GoalTextStream(_ToolFieldStream):
    """Incrementally extract the ``goal`` of a streamed ``agent_plan``."""

    def __init__(self) -> None:
        super().__init__(AGENT_PLAN_TOOL, "goal")


def _make_reply_streamer(
    reply_stream: _ReplyTextStream,
    goal_stream: _GoalTextStream,
    on_delta: Callable[[str], Awaitable[None]],
) -> Callable[[str | None, str], Awaitable[bool]]:
    """Build the ``on_tool_delta`` callback.

    ``reply`` streams its ``text``; ``agent_plan`` streams its ``goal``. Only one
    is active for a given tool call, so the first stream that yields text wins.
    """

    async def _on_tool_delta(name: str | None, args_so_far: str) -> bool:
        fragment = reply_stream.feed(name, args_so_far)
        if not fragment:
            fragment = goal_stream.feed(name, args_so_far)
        if fragment:
            await on_delta(fragment)
            return True
        return False

    return _on_tool_delta
