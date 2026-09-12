import contextlib
import logging

from fastapi import WebSocket, WebSocketDisconnect

from src.core.security import classify_tool_risk
from src.db.database import async_session_factory
from src.db.session import add_message, ensure_session
from src.llm.router import AIRouter
from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.websocket")

# Shared AIRouter instance using the shared plugin_manager
airouter_with_plugins = AIRouter(plugin_manager=plugin_manager)


async def _execute_tool(tool_name: str, arguments: dict | None = None) -> dict:
    """Выполнить инструмент через плагин, которому он принадлежит."""
    return await plugin_manager.execute_tool_by_name(tool_name, arguments)


def _extract_text(raw_result: dict) -> str:
    """Извлечь текст из MCP-результата ({"content": [{"type": "text", ...}]})."""
    if isinstance(raw_result, dict) and "content" in raw_result:
        return "".join(
            item.get("text", "")
            for item in raw_result["content"]
            if item.get("type") == "text"
        )
    return str(raw_result)


def _build_artifacts(tool_name: str, raw_result: dict) -> list[dict] | None:
    """Собрать одноразовые ссылки на артефакты клиента (config/QR)."""
    if tool_name != "wg_create_client" or not isinstance(raw_result, dict):
        return None
    data = raw_result.get("data")
    if not isinstance(data, dict):
        return None
    config_token = data.get("config_token")
    qr_token = data.get("qr_token")
    if not (config_token and qr_token):
        return None
    return [
        {
            "label": "Скачать конфигурацию",
            "url": f"/api/artifacts/{config_token}/config",
        },
        {
            "label": "Скачать QR-код",
            "url": f"/api/artifacts/{qr_token}/qr",
        },
    ]


async def _save_tool_message(
    session_id: str,
    tool_name: str,
    arguments: dict | None,
    content: str,
) -> None:
    """Сохранить результат вызова инструмента в историю сессии."""
    async with async_session_factory() as db:
        await add_message(
            db,
            session_id,
            "tool",
            content,
            tool_calls=[{"name": tool_name, "arguments": arguments}],
        )


async def _run_tool_and_notify(
    websocket: WebSocket,
    session_id: str,
    tool_name: str,
    arguments: dict | None,
    risk: str | None = None,
) -> None:
    """Выполнить инструмент, сохранить результат и отправить его в UI."""
    raw_result = await _execute_tool(tool_name, arguments)
    content_text = _extract_text(raw_result)
    artifacts = _build_artifacts(tool_name, raw_result)

    await _save_tool_message(session_id, tool_name, arguments, content_text)

    payload = {
        "type": "tool_response",
        "tool": tool_name,
        "content": content_text,
        "result": content_text,
        "status": "success",
        "summary": "Выполнено",
        "artifacts": artifacts,
    }
    if risk:
        payload["risk"] = risk
    await websocket.send_json(payload)


async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint для живого чата с AI.

    Логика:
      1. Принять соединение.
      2. В цикле читать JSON от клиента (сообщения или команды approve/deny).
      3. Если это текст — отправить thinking → вызвать AIRouter.route → тип ответа.
      4. Если action=approve — выполнить плагин через MCP.
      5. Если action=deny — отправить tool_denied.
      6. Safe/Moderate инструменты — выполнить автоматически.
      7. Dangerous — отправить approval_request.
      8. Обработать закрытие соединения — логировать.
      9. Обработать исключения — отправить {"type": "error", ...}.
    """
    await websocket.accept()

    # Убедиться, что сессия существует (создать пользователя и сессию если нет)
    async with async_session_factory() as ws_db:
        await ensure_session(ws_db, session_id)

    try:
        while True:
            # Читаем JSON от клиента
            data = await websocket.receive_json()
            logger.info(f"📥 Получено сообщение от клиента: {data}")

            # Проверяем, это команда approve/deny или обычное сообщение?
            action = data.get("action")

            if action == "approve":
                tool = data.get("tool", "unknown")
                tool_args = data.get("args") or {}
                try:
                    await _run_tool_and_notify(
                        websocket, session_id, tool, tool_args
                    )
                except Exception as exc:
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Ошибка выполнения '{tool}': {exc}",
                    })
                continue

            if action == "deny":
                await websocket.send_json({"type": "tool_denied"})
                continue

            # Обычное текстовое сообщение от пользователя
            user_text = data if isinstance(data, str) else data.get("message", "")
            if not user_text:
                continue

            # Отправляем "печатает..."
            await websocket.send_json(
                {"type": "thinking", "status": "processing"}
            )
            logger.info(f"🧠 Отправка запроса в LLM для session={session_id}...")

            # Отправляем к LLM
            try:
                result = await airouter_with_plugins.route(user_text, session_id)
                logger.info(f"✅ LLM вернула ответ, type={result.get('type')}, session={session_id}")

                # Уведомление UI о недоступных плагинах
                unavailable = result.get("tool_schema_warning")
                if unavailable:
                    await websocket.send_json({
                        "type": "schema_warning",
                        "unavailable_plugins": unavailable,
                    })
            except Exception as exc:
                await websocket.send_json(
                    {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
                )
                continue

            # Обрабатываем тип ответа
            answer_type = result.get("type")

            if answer_type == "text":
                await websocket.send_json(
                    {"type": "response", "content": result.get("content", "")}
                )
                continue

            if answer_type == "tool_call":
                tool_name = result.get("tool_name", "unknown")
                tool_args = result.get("tool_args")

                # Классифицируем риск
                risk = classify_tool_risk(tool_name)
                await websocket.send_json({
                    "type": "tool_call",
                    "tool": tool_name,
                    "args": tool_args,
                    "content": f"Вызов инструмента: {tool_name}",
                    "status": "running",
                    "summary": "Выполняется…",
                })

                if risk == "dangerous":
                    await websocket.send_json({
                        "type": "approval_request",
                        "tool": tool_name,
                        "args": tool_args,
                        "risk": "dangerous",
                        "action": tool_name,
                        "content": f"Подтвердите выполнение инструмента: {tool_name}",
                    })
                else:
                    # Safe/Moderate — выполняем автоматически через плагин
                    try:
                        await _run_tool_and_notify(
                            websocket, session_id, tool_name, tool_args, risk=risk
                        )
                    except Exception as exc:
                        logger.error(f"Ошибка выполнения инструмента '{tool_name}': {exc}")
                        await websocket.send_json({
                            "type": "error",
                            "error": f"Ошибка вызова '{tool_name}': {exc}",
                        })
                        continue

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket disconnected for session {session_id}")
    except Exception as exc:
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "error": str(exc)})
        logger.error(f"❌ Фатальная ошибка WebSocket для session {session_id}: {exc}")
        import traceback
        logger.error(traceback.format_exc())
