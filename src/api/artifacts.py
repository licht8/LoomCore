"""Serve plugin-generated artifacts (client configs and QR codes)."""

import base64
import logging
import re

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from src.api.deps import require_admin_token_from_request
from src.core.rate_limit import limiter
from src.plugins import plugin_manager

logger = logging.getLogger("loomcore.api.artifacts")

router = APIRouter(prefix="/api", tags=["artifacts"])

_ALLOWED_MEDIA_TYPES = {"text/plain", "image/png"}


def _sanitize_filename(value: object) -> str:
    """Reduce a plugin-supplied filename to a safe ASCII header token.

    Every character outside ``[A-Za-z0-9._-]`` becomes ``_``; the result is
    stripped of leading/trailing dots and underscores and capped at 255
    characters. An empty result becomes ``"artifact"``. This stops a quote,
    CR/LF or path separator from breaking out of the quoted
    ``Content-Disposition`` filename or injecting a header.
    """
    source = "" if value is None else str(value)
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", source).strip("._")
    return cleaned[:255] or "artifact"


def _safe_media_type(value: object) -> str:
    """Constrain a plugin-supplied media type to a known allow-list.

    Anything outside ``{"text/plain", "image/png"}`` (including a missing
    value or an attempt to inject a header via the media type) becomes
    ``"application/octet-stream"``.
    """
    return (
        value
        if isinstance(value, str) and value in _ALLOWED_MEDIA_TYPES
        else "application/octet-stream"
    )


@router.get("/artifacts/{name}/{kind}")
@limiter.limit("60 per 60 seconds")
async def download_artifact(
    name: str,
    kind: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Serve a client's config/QR by name (admin-only, rebuilt on demand)."""
    require_admin_token_from_request(request, authorization)
    if kind not in {"config", "qr"}:
        return JSONResponse(status_code=404, content={"detail": "Artifact not found"})
    try:
        result = await plugin_manager.execute_internal_tool(
            "wg_get_client_artifact",
            {"name": name, "kind": kind},
        )
        if not isinstance(result, dict):
            raise TypeError("plugin returned a non-object result")
        data = result.get("data", {})
        if not isinstance(data, dict):
            raise TypeError("plugin returned a non-object artifact payload")
        payload = base64.b64decode(data["content_base64"], validate=True)
        filename = _sanitize_filename(data["filename"])
        return Response(
            content=payload,
            media_type=_safe_media_type(data.get("media_type")),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except (KeyError, ValueError, OSError, RuntimeError, TypeError) as exc:
        logger.warning("Artifact fetch failed for %r (%s): %s", name, kind, exc)
        return JSONResponse(
            status_code=404,
            content={"detail": "Artifact not found or unavailable"},
        )
