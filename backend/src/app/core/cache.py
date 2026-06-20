"""Postgres-backed replacements for what Redis used to do.

Covers the access-token (JTI) denylist and the per-user daily token budget.
Rate limiting lives in ``rate_limit.py``. All functions operate on a provided
``AsyncSession``.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import broker, token_cache


#  access-token denylist 
async def revoke_access_jti(
    db: AsyncSession, jti: str, user_id: str | uuid.UUID, expires_at: dt.datetime
) -> None:
    """Durably revoke (Postgres) + update the local cache + fan out to peers."""
    await db.execute(
        text(
            "INSERT INTO revoked_access_tokens (jti, user_id, expires_at) "
            "VALUES (:jti, :uid, :exp) ON CONFLICT (jti) DO NOTHING"
        ),
        {"jti": jti, "uid": str(user_id), "exp": expires_at},
    )
    exp_epoch = expires_at.timestamp()
    token_cache.add(jti, exp_epoch)             # immediate local consistency
    await broker.publish_revocation(jti, exp_epoch)  # coherence across instances


async def is_access_jti_revoked(db: AsyncSession, jti: str) -> bool:
    """Postgres fallback check (used only when the broker cache is degraded)."""
    row = await db.execute(
        text("SELECT 1 FROM revoked_access_tokens WHERE jti = :jti"), {"jti": jti}
    )
    return row.first() is not None


async def load_active_revocations(db: AsyncSession) -> list[tuple[str, float]]:
    """All not-yet-expired revoked JTIs, to warm the in-memory cache on startup."""
    rows = await db.execute(
        text(
            "SELECT jti, EXTRACT(EPOCH FROM expires_at) FROM revoked_access_tokens "
            "WHERE expires_at > now()"
        )
    )
    return [(r[0], float(r[1])) for r in rows.all()]


#  per-user daily token budget
async def get_daily_tokens(db: AsyncSession, user_id: str | uuid.UUID) -> int:
    row = await db.execute(
        text(
            "SELECT tokens_used FROM token_usage_daily "
            "WHERE user_id = :uid AND usage_date = CURRENT_DATE"
        ),
        {"uid": str(user_id)},
    )
    val = row.scalar()
    return int(val or 0)


async def add_daily_tokens(db: AsyncSession, user_id: str | uuid.UUID, tokens: int) -> int:
    """Atomically increment and return the new daily total."""
    row = await db.execute(
        text(
            "INSERT INTO token_usage_daily (user_id, usage_date, tokens_used) "
            "VALUES (:uid, CURRENT_DATE, :t) "
            "ON CONFLICT (user_id, usage_date) "
            "DO UPDATE SET tokens_used = token_usage_daily.tokens_used + :t "
            "RETURNING tokens_used"
        ),
        {"uid": str(user_id), "t": tokens},
    )
    return int(row.scalar() or 0)
