import logging

from fastapi import WebSocket, WebSocketDisconnect

from src.config import settings
from src.core.security import classify_tool_risk
from src.db import session as db_session
from src.db.session import ensure_session
from src.db.database import async_session_factory
from src.plugins import plugin_manager
from src.llm.router import AIRouter

logger = logging.getLogger("loomhub.websocket")

# Shared AIRouter instance using the shared plugin_manager
airouter_with_plugins = AIRouter(plugin_manager=plugin_manager)


async def _execute_tool(tool_name: str, arguments: dict | None = None) -> dict:
    """Выполнить инструмент через плагин, которому он принадлежит."""
    return await plugin_manager.execute_tool_by_name(tool_name, arguments)


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

                # Выполняем инструмент через плагин
                try:
                    raw_result = await _execute_tool(tool, tool_args)

                    # Извлекаем текст из результата (MCP формат: {"content": [{"type": "text", "text": "..."}]})
                    if isinstance(raw_result, dict) and "content" in raw_result:
                        content_text = ""
                        for item in raw_result["content"]:
                            if item.get("type") == "text":
                                content_text += item.get("text", "")
                    else:
                        content_text = str(raw_result)

                    artifacts = None
                    if (
                        tool_name == "wg_create_client"
                        and isinstance(raw_result, dict)
                        and isinstance(raw_result.get("data"), dict)
                    ):
                        artifact_data = raw_result["data"]
                        if artifact_data.get("config_token") and artifact_data.get("qr_token"):
                            artifacts = [
                                {
                                    "label": "Скачать конфигурацию",
                                    "url": f"/api/loomwg/artifacts/{artifact_data['config_token']}/config",
                                },
                                {
                                    "label": "Скачать QR-код",
                                    "url": f"/api/loomwg/artifacts/{artifact_data['qr_token']}/qr",
                                },
                            ]

                    artifacts = None
                    if (
                        tool_name == "wg_create_client"
                        and isinstance(raw_result, dict)
                        and raw_result.get("data", {}).get("artifact_token")
                    ):
                        token = raw_result["data"]["artifact_token"]
                        artifacts = [
                            {
                                "label": "Скачать конфигурацию",
                                "url": f"/api/loomwg/artifacts/{token}/config",
                            },
                            {
                                "label": "Скачать QR-код",
                                "url": f"/api/loomwg/artifacts/{token}/qr",
                            },
                        ]

                    await websocket.send_json({
                        "type": "tool_response",
                        "tool": tool,
                        "content": content_text,
                        "result": content_text,
                        "status": "success",
                        "summary": "Выполнено",
                        "artifacts": artifacts,
                    })
                except Exception as exc:
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Ошибка выполнения '{tool}': {exc}",
                    })
                continue

            elif action == "deny":
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
            except Exception as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "error": f"{type(exc).__name__}: {exc}" or type(exc).__name__,
                    }
                )
                continue

            # Обрабатываем тип ответа
            answer_type = result.get("type")

            if answer_type == "text":
                await websocket.send_json(
                    {"type": "response", "content": result.get("content", "")}
                )

            elif answer_type == "tool_call":
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
                    await websocket.send_json(
                        {
                            "type": "approval_request",
                            "tool": tool_name,
                            "args": tool_args,
                            "risk": "dangerous",
                            "action": tool_name,
                            "content": f"Подтвердите выполнение инструмента: {tool_name}",
                        }
                    )
                else:
                    # Safe/Moderate — выполняем автоматически через плагин
                    try:
                        raw_result = await _execute_tool(tool_name, tool_args)

                        # Извлекаем текст из результата
                        if isinstance(raw_result, dict) and "content" in raw_result:
                            content_text = ""
                            for item in raw_result["content"]:
                                if item.get("type") == "text":
                                    content_text += item.get("text", "")
                        else:
                            content_text = str(raw_result)

                        # Сохраняем результат как tool_message в БД
                        async with async_session_factory() as db:
                            from src.db.session import add_message
                            await add_message(
                                db, session_id,
                                "tool",
                                content_text,
                                tool_calls=[{
                                    "name": tool_name,
                                    "arguments": tool_args,
                                }],
                            )
                            await db.commit()

                        # Показываем пользователю результат
                        await websocket.send_json({
                            "type": "tool_response",
                            "tool": tool_name,
                            "content": content_text,
                            "result": content_text,
                            "risk": risk,
                            "status": "success",
                            "summary": "Выполнено",
                            "artifacts": artifacts,
                        })

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
        try:
            await websocket.send_json(
                {"type": "error", "error": str(exc)}
            )
        except Exception:
            pass
        logger.error(f"❌ Фатальная ошибка WebSocket для session {session_id}: {exc}")
        import traceback; logger.error(traceback.format_exc())
