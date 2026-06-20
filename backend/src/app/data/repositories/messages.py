"""Message (chat history) data access — history lives in Postgres."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Message

# How many prior turns to feed the model (history is truncated to bound prompt size).
HISTORY_LIMIT = 40


async def add(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    *,
    citations: list | None = None,
    token_usage: dict | None = None,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        citations=citations,
        token_usage=token_usage,
    )
    db.add(msg)
    await db.flush()
    return msg


async def list_for_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> list[Message]:
    res = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return list(res.scalars().all())


async def history_as_llm(db: AsyncSession, conversation_id: uuid.UUID) -> list[dict]:
    """Recent user/assistant turns shaped for the LLM, oldest-first."""
    res = await db.execute(
        select(Message)
        .where(
            Message.conversation_id == conversation_id,
            Message.role.in_(("user", "assistant")),
        )
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    rows = list(res.scalars().all())
    rows.reverse()
    return [{"role": m.role, "content": m.content} for m in rows]
