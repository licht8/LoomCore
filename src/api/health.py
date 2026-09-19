"""Core health check route.

``/health`` is deliberately **not** rate-limited: orchestrator liveness probes,
uptime monitors and load balancers poll it frequently, and a ``429`` response
would make a healthy service look down. The reply is a static, cheap constant,
so there is nothing worth throttling.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}
