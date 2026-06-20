"""User data access."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import User


async def get_by_id(db: AsyncSession, user_id: str | uuid.UUID) -> User | None:
    return await db.get(User, uuid.UUID(str(user_id)))


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    res = await db.execute(select(User).where(User.email == email))
    return res.scalar_one_or_none()


async def create(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str | None = None,
    display_name: str | None = None,
    email_verified: bool = False,
    avatar_url: str | None = None,
) -> User:
    user = User(
        email=email,
        password_hash=password_hash,
        display_name=display_name,
        email_verified=email_verified,
        avatar_url=avatar_url,
    )
    db.add(user)
    await db.flush()
    return user


async def set_password(db: AsyncSession, user: User, password_hash: str) -> None:
    user.password_hash = password_hash
    await db.flush()


async def mark_verified(db: AsyncSession, user: User) -> None:
    user.email_verified = True
    await db.flush()


async def touch_last_login(db: AsyncSession, user: User) -> None:
    user.last_login_at = dt.datetime.now(dt.timezone.utc)
    await db.flush()
