"""Auth endpoints: email/password + OAuth (google/microsoft/github).

Identity is delivered via httpOnly cookies. The router is a thin layer: it sets
cookies and shapes responses; all logic lives in the auth/oauth services.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.auth import (
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
)
from app.api.schemas.common import UserOut
from app.config import get_settings
from app.core.db import get_db
from app.core.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    clear_auth_cookies,
    set_access_cookie,
    set_refresh_cookie,
)
from app.data.models import AuthProvider
from app.data.repositories import users as user_repo
from app.services import auth_service, oauth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
_settings = get_settings()

_PROVIDERS = {p.value for p in AuthProvider if p != AuthProvider.password}


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    await auth_service.register(
        db, email=body.email, password=body.password, display_name=body.display_name
    )
    return {"detail": "If the address is new, a verification email has been sent."}


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    access, refresh = await auth_service.login(db, email=body.email, password=body.password)
    set_access_cookie(response, access)
    set_refresh_cookie(response, refresh)
    user = await user_repo.get_by_email(db, body.email.lower())
    return user


@router.post("/refresh", response_model=MessageResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    access, new_refresh = await auth_service.refresh(db, raw)
    set_access_cookie(response, access)
    set_refresh_cookie(response, new_refresh)
    return {"detail": "Token refreshed."}


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await auth_service.logout(
        db,
        access_token=request.cookies.get(ACCESS_COOKIE),
        raw_refresh=request.cookies.get(REFRESH_COOKIE),
    )
    clear_auth_cookies(response)
    return {"detail": "Logged out."}


@router.get("/verify", response_model=MessageResponse)
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    await auth_service.verify_email(db, token)
    return {"detail": "Email verified. You can now sign in."}


@router.post("/password-reset/request", response_model=MessageResponse)
async def password_reset_request(body: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    await auth_service.request_password_reset(db, body.email)
    return {"detail": "If an account exists, a reset link has been sent."}


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def password_reset_confirm(body: PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    await auth_service.confirm_password_reset(db, body.token, body.new_password)
    return {"detail": "Password updated. Please sign in."}


# ── OAuth (official provider libraries) ─────────────────────────────────────────
_OAUTH_STATE_COOKIE = "oauth_state"


def _check_provider(provider: str) -> AuthProvider:
    if provider not in _PROVIDERS or not oauth_service.is_enabled(provider):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown or disabled provider.")
    return AuthProvider(provider)


@router.get("/{provider}/start")
async def oauth_start(provider: str):
    _check_provider(provider)
    state = oauth_service.make_state(provider)
    url = oauth_service.authorization_url(provider, state)
    redirect = RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    redirect.set_cookie(
        _OAUTH_STATE_COOKIE,
        state,
        max_age=oauth_service.STATE_MAX_AGE,
        httponly=True,
        secure=_settings.cookie_secure,
        samesite="lax",
        path="/api/auth",
    )
    return redirect


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    enum_provider = _check_provider(provider)
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing authorization code.")

    oauth_service.verify_state(provider, request.cookies.get(_OAUTH_STATE_COOKIE), state)
    identity = await oauth_service.exchange(provider, code)
    user = await oauth_service.login_or_create(db, enum_provider, identity)
    await user_repo.touch_last_login(db, user)
    access, refresh = await auth_service.issue_tokens(db, user)

    redirect = RedirectResponse(_settings.cors_origins[0] if _settings.cors_origins else "/")
    set_access_cookie(redirect, access)
    set_refresh_cookie(redirect, refresh)
    redirect.delete_cookie(_OAUTH_STATE_COOKIE, path="/api/auth")
    return redirect
