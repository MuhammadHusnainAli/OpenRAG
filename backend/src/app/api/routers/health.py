"""Liveness + readiness. Checks Postgres and Qdrant; reports degraded if either
is down (used by orchestrators / load balancers)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.db import session_scope
from app.rag.qdrant import qdrant

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    checks: dict[str, str] = {}

    try:
        async with session_scope() as db:
            await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await qdrant.get_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["qdrant"] = f"error: {type(exc).__name__}"

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )
