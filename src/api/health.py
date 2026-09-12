"""Core health check route."""

from fastapi import APIRouter, Request

from src.core.rate_limit import limiter

router = APIRouter(tags=["health"])


@router.get("/health")
@limiter.limit("60 per 60 seconds")
def health(request: Request):
    return {"status": "ok"}
