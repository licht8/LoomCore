"""Serve plugin-generated artifacts (client configs and QR codes)."""

import base64
import logging

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from src.api.deps import extract_admin_token
from src.core.security import require_admin_token
from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.api.artifacts")

router = APIRouter(prefix="/api", tags=["artifacts"])


@router.get("/artifacts/{name}/{kind}")
async def download_artifact(
    name: str,
    kind: str,
    request: Request,
    access_token: str | None = Query(default=None),
):
    """Serve a client's config/QR by name (admin-only, rebuilt on demand)."""
    require_admin_token(extract_admin_token(request, access_token=access_token))
    if kind not in {"config", "qr"}:
        return JSONResponse(status_code=404, content={"error": "Artifact not found"})
    try:
        result = await plugin_manager.execute_internal_tool(
            "wg_get_client_artifact",
            {"name": name, "kind": kind},
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
    except (KeyError, ValueError, RuntimeError, OSError) as exc:
        logger.warning("Artifact fetch failed for %r (%s): %s", name, kind, exc)
        return JSONResponse(
            status_code=404,
            content={"error": "Artifact not found or unavailable", "reason": str(exc)},
        )
