"""Conversation (chat session) data access. All reads are ownership-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Conversation


async def create(db: AsyncSession, *, user_id: uuid.UUID, title: str = "New chat") -> Conversation:
    conv = Conversation(user_id=user_id, title=title)
    db.add(conv)
    await db.flush()
    return conv


async def list_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[Conversation]:
    res = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(res.scalars().all())


async def get_owned(
    db: AsyncSession, conversation_id: str | uuid.UUID, user_id: uuid.UUID
) -> Conversation | None:
    """Return the conversation only if owned by ``user_id`` (else None => 404)."""
    try:
        cid = uuid.UUID(str(conversation_id))
    except (ValueError, AttributeError):
        return None
    res = await db.execute(
        select(Conversation).where(
            Conversation.id == cid, Conversation.user_id == user_id
        )
    )
    return res.scalar_one_or_none()


async def rename(db: AsyncSession, conv: Conversation, title: str) -> Conversation:
    conv.title = title
    await db.flush()
    return conv


async def delete(db: AsyncSession, conv: Conversation) -> None:
    await db.delete(conv)
    await db.flush()
