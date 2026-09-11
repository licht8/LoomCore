"""Отдача одноразовых артефактов плагинов."""

import base64
import logging

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from src.api.deps import extract_admin_token
from src.core.security import require_admin_token
from src.plugins import plugin_manager

logger = logging.getLogger("loomhub.api.artifacts")

router = APIRouter(prefix="/api", tags=["artifacts"])


@router.get("/artifacts/{token}/{kind}")
async def download_artifact(
    token: str,
    kind: str,
    request: Request,
    access_token: str | None = Query(default=None),
):
    """Отдать артефакт плагина (config/QR) по одноразовому capability-токену."""
    require_admin_token(extract_admin_token(request, None, access_token))
    if kind not in {"config", "qr"}:
        return JSONResponse(status_code=404, content={"error": "Артефакт не найден"})
    try:
        result = await plugin_manager.execute_internal_tool(
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
