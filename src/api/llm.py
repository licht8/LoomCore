"""Routes for the runtime LLM provider settings (admin only).

The active provider can be read and changed without a restart. The API key is
write-only: it is never echoed back, only reported as configured plus a short
preview, and it is never written to the audit log or application logs.
"""

import contextlib
import logging
from dataclasses import asdict, replace

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from src.api.deps import extract_admin_token
from src.core.audit import record_mutation_isolated
from src.core.rate_limit import limiter
from src.core.security import require_admin_token
from src.llm.config import (
    NOT_CONFIGURED_MESSAGE,
    LlmConfig,
    get_active,
    mask_api_key,
    set_active,
)
from src.llm.providers import PROVIDER_PRESETS

router = APIRouter(prefix="/api", tags=["llm"])

logger = logging.getLogger("loomcore.api.llm")


class LlmSettingsUpdate(BaseModel):
    """A partial update; every field is optional and ``api_key`` is write-only."""

    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    summary_model: str | None = None
    enable_thinking: bool | None = None
    thinking_template: bool | None = None
    connect_timeout: float | None = None
    read_timeout: float | None = None
    write_timeout: float | None = None
    pool_timeout: float | None = None
    max_retries: int | None = None


def _serialize(config: LlmConfig) -> dict:
    """Render the config for the API, masking the write-only API key."""
    return {
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "summary_model": config.summary_model,
        "enable_thinking": config.enable_thinking,
        "thinking_template": config.thinking_template,
        "connect_timeout": config.connect_timeout,
        "read_timeout": config.read_timeout,
        "write_timeout": config.write_timeout,
        "pool_timeout": config.pool_timeout,
        "max_retries": config.max_retries,
        "configured": config.configured,
        "api_key": {
            "configured": bool(config.api_key),
            "preview": mask_api_key(config.api_key),
        },
    }


def _merge(current: LlmConfig, data: dict) -> LlmConfig:
    """Validate a partial update and merge it onto ``current`` (D3 key rules)."""
    provider = data.get("provider", current.provider)
    if provider and provider not in PROVIDER_PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")

    base_url = data.get("base_url", current.base_url)
    if base_url and not base_url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400, detail="base_url must start with http:// or https://"
        )

    if "model" in data and not str(data.get("model") or "").strip():
        raise HTTPException(status_code=400, detail="model must not be empty")

    for field in ("connect_timeout", "read_timeout", "write_timeout", "pool_timeout"):
        if field in data and (data[field] is None or data[field] <= 0):
            raise HTTPException(status_code=400, detail=f"{field} must be positive")

    if "max_retries" in data and (data["max_retries"] is None or data["max_retries"] < 0):
        raise HTTPException(status_code=400, detail="max_retries must be >= 0")

    if "api_key" in data:
        key = data["api_key"]
        if key is None:
            api_key = current.api_key if provider == current.provider else ""
        elif key == "":
            api_key = ""
        else:
            api_key = key
    else:
        api_key = current.api_key if provider == current.provider else ""

    return replace(
        current,
        provider=provider,
        base_url=base_url,
        model=data.get("model", current.model),
        api_key=api_key,
        summary_model=data.get("summary_model", current.summary_model),
        enable_thinking=data.get("enable_thinking", current.enable_thinking),
        thinking_template=data.get("thinking_template", current.thinking_template),
        connect_timeout=data.get("connect_timeout", current.connect_timeout),
        read_timeout=data.get("read_timeout", current.read_timeout),
        write_timeout=data.get("write_timeout", current.write_timeout),
        pool_timeout=data.get("pool_timeout", current.pool_timeout),
        max_retries=data.get("max_retries", current.max_retries),
    )


async def _audit_llm_update(config: LlmConfig) -> None:
    """Record the change in the audit log (never includes the API key)."""
    with contextlib.suppress(Exception):
        await record_mutation_isolated(
            tool="llm_settings_update",
            arguments={
                "provider": config.provider,
                "model": config.model,
                "base_url": config.base_url,
            },
            risk="dangerous",
            status="success",
        )


@router.get("/llm/providers")
@limiter.limit("60 per 60 seconds")
async def list_llm_providers(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return the available OpenAI-compatible provider presets."""
    require_admin_token(extract_admin_token(request, authorization), request)
    return {"providers": [asdict(preset) for preset in PROVIDER_PRESETS.values()]}


@router.get("/llm/settings")
@limiter.limit("60 per 60 seconds")
async def get_llm_settings(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return the active provider config with the API key masked."""
    require_admin_token(extract_admin_token(request, authorization), request)
    return _serialize(get_active())


@router.put("/llm/settings")
@limiter.limit("10 per 60 seconds")
async def update_llm_settings(
    payload: LlmSettingsUpdate,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Validate, apply at runtime, and persist the provider config."""
    require_admin_token(extract_admin_token(request, authorization), request)
    data = payload.model_dump(exclude_unset=True)
    updated = _merge(get_active(), data)
    saved = set_active(updated)
    await _audit_llm_update(updated)
    if not saved:
        raise HTTPException(status_code=500, detail="Failed to persist LLM settings")
    return _serialize(get_active())


@router.post("/llm/test")
@limiter.limit("10 per 60 seconds")
async def test_llm_connection(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Probe the active provider's ``/models`` endpoint (no budget consumed)."""
    require_admin_token(extract_admin_token(request, authorization), request)
    config = get_active()
    if not config.configured:
        raise HTTPException(status_code=400, detail=NOT_CONFIGURED_MESSAGE)

    headers = (
        {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    )
    url = f"{config.base_url}/models"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if response.status_code >= 400:
        return {
            "ok": False,
            "status": response.status_code,
            "error": f"HTTP {response.status_code}",
        }

    result = {"ok": True, "status": response.status_code}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    models = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(models, list):
        result["models_count"] = len(models)
    return result
