"""Authentication business logic: registration, login (with lockout), token
issuance + rotation with reuse detection, logout, email verification, and
password reset. All token hygiene per SECURITY §2.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import cache
from app.core.disposable_email import EmailRejected, normalize_email, validate_registration_email
from app.core.email import send_password_reset_email, send_verification_email
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_opaque_token,
    hash_password,
    needs_rehash,
    sha256_hex,
    verify_password,
)
from app.data.models import User
from app.data.repositories import login as login_repo
from app.data.repositories import tokens as token_repo
from app.data.repositories import users as user_repo
from app.services.errors import (
    AccountLocked,
    EmailPolicyError,
    InvalidCredentials,
    TokenError,
)

_settings = get_settings()
MIN_PASSWORD_LEN = 12
VERIFICATION_TTL = dt.timedelta(hours=24)
RESET_TTL = dt.timedelta(hours=1)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise EmailPolicyError(
            f"Password must be at least {MIN_PASSWORD_LEN} characters.",
            code="weak_password",
        )


#  token issuance / rotation 
async def issue_tokens(
    db: AsyncSession, user: User, *, family_id: uuid.UUID | None = None
) -> tuple[str, str]:
    """Create a new access JWT and a rotated opaque refresh token. Returns
    (access_token, refresh_token_raw)."""
    access_token, _jti, _exp = create_access_token(user.id)

    raw_refresh = generate_opaque_token(32)
    await token_repo.create_refresh(
        db,
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=sha256_hex(raw_refresh),
        expires_at=_now() + dt.timedelta(days=_settings.refresh_token_ttl_days),
    )
    return access_token, raw_refresh


#  registration 
async def register(
    db: AsyncSession, *, email: str, password: str, display_name: str | None
) -> None:
    """Create an unverified account and send verification. Anti-enumeration:
    succeeds quietly if the email already exists (caller returns a generic msg)."""
    _check_password_policy(password)
    try:
        normalized = validate_registration_email(
            email, check_deliverability=_settings.is_production
        )
    except EmailRejected as exc:
        raise EmailPolicyError(str(exc)) from exc

    existing = await user_repo.get_by_email(db, normalized)
    if existing is not None:
        return  # do not reveal that the address is taken

    user = await user_repo.create(
        db,
        email=normalized,
        password_hash=hash_password(password),
        display_name=display_name,
        email_verified=not _settings.require_email_verification,
    )
    await _send_verification(db, user)


async def _send_verification(db: AsyncSession, user: User) -> None:
    raw = generate_opaque_token(32)
    await token_repo.create_email_verification(
        db, user_id=user.id, token_hash=sha256_hex(raw), expires_at=_now() + VERIFICATION_TTL
    )
    await send_verification_email(user.email, raw)


async def verify_email(db: AsyncSession, raw_token: str) -> None:
    tok = await token_repo.get_email_verification(db, sha256_hex(raw_token))
    if tok is None or tok.used or tok.expires_at < _now():
        raise TokenError("Invalid or expired verification link.")
    await token_repo.use_email_verification(db, tok)
    user = await user_repo.get_by_id(db, tok.user_id)
    if user:
        await user_repo.mark_verified(db, user)


#  login 
async def login(db: AsyncSession, *, email: str, password: str) -> tuple[str, str]:
    normalized = normalize_email(email)

    locked_until = await login_repo.is_locked(db, normalized)
    if locked_until:
        raise AccountLocked("Too many attempts. Try again later.")

    user = await user_repo.get_by_email(db, normalized)
    # Same generic failure whether the user exists or the password is wrong.
    if user is None or not user.password_hash or not verify_password(password, user.password_hash):
        await login_repo.record_failure(db, normalized)
        raise InvalidCredentials("Incorrect email or password.")

    if not user.is_active:
        raise InvalidCredentials("Incorrect email or password.")

    await login_repo.reset(db, normalized)
    if needs_rehash(user.password_hash):
        await user_repo.set_password(db, user, hash_password(password))
    await user_repo.touch_last_login(db, user)

    return await issue_tokens(db, user)


#  refresh (rotation + reuse detection) 
async def refresh(db: AsyncSession, raw_refresh: str | None) -> tuple[str, str]:
    if not raw_refresh:
        raise TokenError("Missing refresh token.")
    rt = await token_repo.get_refresh_by_hash(db, sha256_hex(raw_refresh))
    if rt is None:
        raise TokenError("Invalid refresh token.")

    # Reuse of an already-rotated or revoked token => compromise; nuke the family.
    if rt.revoked or rt.used_at is not None:
        await token_repo.revoke_refresh_family(db, rt.family_id)
        raise TokenError("Refresh token reuse detected. Please log in again.")

    if rt.expires_at < _now():
        raise TokenError("Refresh token expired.")

    user = await user_repo.get_by_id(db, rt.user_id)
    if user is None or not user.is_active:
        raise TokenError("Invalid refresh token.")

    await token_repo.mark_refresh_used(db, rt)
    return await issue_tokens(db, user, family_id=rt.family_id)


#  logout 
async def logout(db: AsyncSession, *, access_token: str | None, raw_refresh: str | None) -> None:
    if access_token:
        try:
            payload = decode_access_token(access_token)
            await cache.revoke_access_jti(
                db,
                payload["jti"],
                payload["sub"],
                dt.datetime.fromtimestamp(payload["exp"], tz=dt.timezone.utc),
            )
        except Exception:
            pass
    if raw_refresh:
        rt = await token_repo.get_refresh_by_hash(db, sha256_hex(raw_refresh))
        if rt:
            await token_repo.revoke_refresh_family(db, rt.family_id)


#  password reset 
async def request_password_reset(db: AsyncSession, email: str) -> None:
    """Always succeeds from the caller's view (no account enumeration)."""
    normalized = normalize_email(email)
    user = await user_repo.get_by_email(db, normalized)
    if user is None:
        return
    raw = generate_opaque_token(32)
    await token_repo.create_password_reset(
        db, user_id=user.id, token_hash=sha256_hex(raw), expires_at=_now() + RESET_TTL
    )
    await send_password_reset_email(user.email, raw)


async def confirm_password_reset(db: AsyncSession, raw_token: str, new_password: str) -> None:
    _check_password_policy(new_password)
    tok = await token_repo.get_password_reset(db, sha256_hex(raw_token))
    if tok is None or tok.used or tok.expires_at < _now():
        raise TokenError("Invalid or expired reset link.")
    user = await user_repo.get_by_id(db, tok.user_id)
    if user is None:
        raise TokenError("Invalid or expired reset link.")

    await token_repo.use_password_reset(db, tok)
    await user_repo.set_password(db, user, hash_password(new_password))
    # invalidate every active session for this user
    await _revoke_all_user_refresh(db, user.id)


async def _revoke_all_user_refresh(db: AsyncSession, user_id: uuid.UUID) -> None:
    from sqlalchemy import update

    from app.data.models import RefreshToken

    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked=True)
    )
    await db.flush()
