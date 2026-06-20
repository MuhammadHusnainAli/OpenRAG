"""Login-attempt tracking for progressive backoff and temporary lockout."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LOCK_THRESHOLD = 10          # failures before a temporary lock
LOCK_MINUTES = 15            # lock duration once the threshold is hit


async def is_locked(db: AsyncSession, email: str) -> dt.datetime | None:
    res = await db.execute(
        text("SELECT locked_until FROM login_attempts WHERE email = :e"), {"e": email}
    )
    locked_until = res.scalar()
    if locked_until and locked_until > dt.datetime.now(dt.timezone.utc):
        return locked_until
    return None


async def record_failure(db: AsyncSession, email: str) -> None:
    """Increment the failure counter; lock the account once the threshold trips."""
    await db.execute(
        text(
            "INSERT INTO login_attempts (email, failed_count, last_attempt_at) "
            "VALUES (:e, 1, now()) "
            "ON CONFLICT (email) DO UPDATE SET "
            "  failed_count = login_attempts.failed_count + 1, "
            "  last_attempt_at = now(), "
            "  locked_until = CASE "
            "    WHEN login_attempts.failed_count + 1 >= :thr "
            "    THEN now() + (:mins || ' minutes')::interval "
            "    ELSE login_attempts.locked_until END"
        ),
        {"e": email, "thr": LOCK_THRESHOLD, "mins": LOCK_MINUTES},
    )


async def reset(db: AsyncSession, email: str) -> None:
    await db.execute(text("DELETE FROM login_attempts WHERE email = :e"), {"e": email})
