"""Refresh / email-verification / password-reset token data access.

Tokens are stored only as SHA-256 hashes. Refresh tokens rotate and carry a
``family_id`` so that reuse of an already-rotated token revokes the family.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
)


#  refresh tokens
async def create_refresh(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    token_hash: str,
    expires_at: dt.datetime,
) -> RefreshToken:
    rt = RefreshToken(
        user_id=user_id, family_id=family_id, token_hash=token_hash, expires_at=expires_at
    )
    db.add(rt)
    await db.flush()
    return rt


async def get_refresh_by_hash(db: AsyncSession, token_hash: str) -> RefreshToken | None:
    res = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    return res.scalar_one_or_none()


async def mark_refresh_used(db: AsyncSession, rt: RefreshToken) -> None:
    rt.used_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()


async def revoke_refresh_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id)
        .values(revoked=True)
    )
    await db.flush()


#  email verification
async def create_email_verification(
    db: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at: dt.datetime
) -> EmailVerificationToken:
    tok = EmailVerificationToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(tok)
    await db.flush()
    return tok


async def get_email_verification(
    db: AsyncSession, token_hash: str
) -> EmailVerificationToken | None:
    res = await db.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
    )
    return res.scalar_one_or_none()


async def use_email_verification(db: AsyncSession, tok: EmailVerificationToken) -> None:
    tok.used = True
    await db.flush()


#  password reset
async def create_password_reset(
    db: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at: dt.datetime
) -> PasswordResetToken:
    tok = PasswordResetToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    db.add(tok)
    await db.flush()
    return tok


async def get_password_reset(db: AsyncSession, token_hash: str) -> PasswordResetToken | None:
    res = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    return res.scalar_one_or_none()


async def use_password_reset(db: AsyncSession, tok: PasswordResetToken) -> None:
    tok.used = True
    await db.flush()
