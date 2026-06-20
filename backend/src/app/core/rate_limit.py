"""Postgres-backed fixed-window rate limiting.

A sliding window in Redis is replaced by a fixed-window counter table. For each
``(bucket_key, window_start)`` we atomically upsert+increment and compare to the
limit. Keying is per-IP at the middleware; auth endpoints additionally limit
per-email inside the service layer via ``enforce_limit``.

Client IP is read from ``X-Forwarded-For`` (set/overwritten by *our* proxy) so
per-IP limits aren't trivially spoofed; configure the proxy to overwrite XFF.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import get_settings
from app.core.db import session_scope

_settings = get_settings()

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_rate(spec: str) -> tuple[int, int]:
    """'5/minute' -> (5, 60). Returns (limit, window_seconds)."""
    count, _, unit = spec.partition("/")
    unit = unit.strip().rstrip("s") or "minute"
    return int(count), _UNITS.get(unit, 60)


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    return request.client.host if request.client else "unknown"


async def enforce_limit(
    db: AsyncSession, bucket_key: str, spec: str
) -> tuple[bool, int]:
    """Increment the counter for ``bucket_key``. Returns (allowed, retry_after_s)."""
    limit, window = parse_rate(spec)
    now = dt.datetime.now(dt.timezone.utc)
    window_start = dt.datetime.fromtimestamp(
        (int(now.timestamp()) // window) * window, tz=dt.timezone.utc
    )
    row = await db.execute(
        text(
            "INSERT INTO rate_limit_counters (bucket_key, window_start, count) "
            "VALUES (:k, :w, 1) "
            "ON CONFLICT (bucket_key, window_start) "
            "DO UPDATE SET count = rate_limit_counters.count + 1 "
            "RETURNING count"
        ),
        {"k": bucket_key, "w": window_start},
    )
    count = int(row.scalar() or 0)
    if count > limit:
        retry_after = int((window_start.timestamp() + window) - now.timestamp())
        return False, max(retry_after, 1)
    return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP limits on auth, chat, and upload routes."""

    def _spec_for(self, method: str, path: str) -> tuple[str, str] | None:
        if path.startswith("/api/auth/"):
            return "auth", _settings.rate_limit_auth
        if path.endswith("/chat") and method == "POST":
            return "chat", _settings.rate_limit_chat
        if path.endswith("/documents") and method == "POST":
            return "upload", _settings.rate_limit_upload
        return None

    async def dispatch(self, request: Request, call_next):
        match = self._spec_for(request.method, request.url.path)
        if match is None:
            return await call_next(request)

        bucket, spec = match
        key = f"{bucket}:ip:{client_ip(request)}"
        async with session_scope() as db:
            allowed, retry_after = await enforce_limit(db, key, spec)
            await db.commit()

        if not allowed:
            return JSONResponse(
                {"detail": "Rate limit exceeded. Try again later.", "code": "rate_limited"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
