import uuid
import base64
import secrets

import uvicorn
from fastapi import FastAPI, Depends, WebSocket, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.database import get_db, init_db
from src.interfaces.websocket import websocket_endpoint
from src.llm.router import AIRouter
from src.plugins import plugin_manager

app = FastAPI(
    title="LoomHub Core",
    version="0.0.1",
    docs_url="/docs" if settings.debug else None,
)

# --- Подключение CORS для React (Vite/Next.js) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8083",
        "http://127.0.0.1:8083",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

airouter = AIRouter(plugin_manager=plugin_manager)


class ChatRequest(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str


def require_admin_token(token: str | None) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Административный доступ не настроен")
    if not token or not secrets.compare_digest(token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Требуется административный токен")


@app.on_event("startup")
async def startup():
    print("🚀 LoomHub Core starting up...")
    await init_db()

    discovered = plugin_manager.discover_plugins("/app/plugins")
    print(f"🔌 Plugins loaded: {', '.join(discovered) or 'none'}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/tools")
async def list_plugins_tools(
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
):
    """Вернуть схемы инструментов всех плагинов (для Swagger/UI)."""
    token = access_token or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    require_admin_token(token)
    result = {}
    try:
        result["tools"] = await plugin_manager.get_all_tools()
    except Exception as exc:
        result["loomwg"] = {"error": str(exc)}
    return result


@app.post("/api/chat")
async def chat_endpoint(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(default=None),
    access_token: str | None = Query(default=None),
):
    """Отправить сообщение AI и получить ответ (REST)."""
    token = access_token or (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    require_admin_token(token)
    try:
        result = await airouter.route(request.message, request.session_id)
        return result
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )


@app.get("/api/loomwg/artifacts/{token}/{kind}")
async def download_loomwg_artifact(
    token: str,
    kind: str,
    access_token: str | None = Query(default=None),
):
    """Consume a short-lived, one-time client artifact capability."""
    require_admin_token(access_token)
    if kind not in {"config", "qr"}:
        return JSONResponse(status_code=404, content={"error": "Артефакт не найден"})
    try:
        result = await plugin_manager.execute_tool(
            "loomwg",
            "wg_get_client_artifact",
            {"token": token, "kind": kind},
        )
        data = result.get("data", {})
        payload = base64.b64decode(data["content_base64"], validate=True)
        return Response(
            content=payload,
            media_type=data["media_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{data["filename"]}"',
                "Cache-Control": "no-store",
            },
        )
    except (KeyError, ValueError, RuntimeError, OSError):
        return JSONResponse(status_code=404, content={"error": "Артефакт не найден или истёк"})


@app.websocket("/ws/{session_id}")
async def ws_proxy(websocket: WebSocket, session_id: str, access_token: str | None = Query(default=None)):
    """Прокси-роутер: передаёт управление в src.interfaces.websocket."""
    if not settings.admin_token or not access_token or not secrets.compare_digest(
        access_token, settings.admin_token
    ):
        await websocket.close(code=1008, reason="Требуется административный токен")
        return
    await websocket_endpoint(websocket, session_id)


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
