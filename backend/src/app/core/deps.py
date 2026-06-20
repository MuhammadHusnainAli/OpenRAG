"""FastAPI dependencies and cookie helpers.

``current_user`` is the single source of authenticated identity: it reads the
access token (httpOnly cookie, or ``Authorization: Bearer`` for API clients),
validates the signature/claims, checks the denylist, loads the user, and
enforces active + verified status. ``user_id`` is taken ONLY from here.
"""

from __future__ import annotations

import datetime as dt

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import broker, cache, token_cache
from app.core.db import get_db
from app.core.security import decode_access_token
from app.data.models import User
from app.data.repositories import users as user_repo

_settings = get_settings()

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/api/auth"


#  cookie helpers 
def set_access_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=_settings.access_token_ttl_min * 60,
        httponly=True,
        secure=_settings.cookie_secure,
        samesite="lax",
        domain=_settings.cookie_domain,
        path="/",
    )


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=_settings.refresh_token_ttl_days * 86400,
        httponly=True,
        secure=_settings.cookie_secure,
        samesite="lax",
        domain=_settings.cookie_domain,
        path=REFRESH_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, domain=_settings.cookie_domain, path="/")
    response.delete_cookie(REFRESH_COOKIE, domain=_settings.cookie_domain, path=REFRESH_PATH)


#  identity 
def _extract_token(request: Request) -> str | None:
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = _extract_token(request)
    if not token:
        raise credentials_error
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise credentials_error from exc

    # Fast path: in-memory denylist kept coherent by RabbitMQ fan-out.
    # If the broker is degraded, fall back to the durable Postgres denylist.
    jti = payload["jti"]
    if token_cache.is_revoked(jti):
        raise credentials_error
    if not broker.connected and await cache.is_access_jti_revoked(db, jti):
        raise credentials_error

    user = await user_repo.get_by_id(db, payload["sub"])
    if user is None or not user.is_active:
        raise credentials_error

    if _settings.require_email_verification and not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please check your inbox.",
        )

    request.state.user_id = str(user.id)
    return user


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)
