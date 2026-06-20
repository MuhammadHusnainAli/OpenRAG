"""OAuth account-link data access."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import AuthProvider, OAuthAccount


async def get_by_provider(
    db: AsyncSession, provider: AuthProvider, provider_account_id: str
) -> OAuthAccount | None:
    res = await db.execute(
        select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_account_id == provider_account_id,
        )
    )
    return res.scalar_one_or_none()


async def link(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    provider: AuthProvider,
    provider_account_id: str,
    email: str | None = None,
) -> OAuthAccount:
    account = OAuthAccount(
        user_id=user_id,
        provider=provider,
        provider_account_id=provider_account_id,
        email=email,
    )
    db.add(account)
    await db.flush()
    return account
