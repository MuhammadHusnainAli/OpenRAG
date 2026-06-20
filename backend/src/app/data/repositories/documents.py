"""Document data access. Reads are ownership-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.models import Document, DocStatus


async def create(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    content_type: str,
    size_bytes: int,
    sha256: str,
    storage_path: str,
) -> Document:
    doc = Document(
        conversation_id=conversation_id,
        user_id=user_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_path=storage_path,
        status=DocStatus.pending,
    )
    db.add(doc)
    await db.flush()
    return doc


async def list_for_session(db: AsyncSession, conversation_id: uuid.UUID) -> list[Document]:
    res = await db.execute(
        select(Document)
        .where(Document.conversation_id == conversation_id)
        .order_by(Document.created_at.asc())
    )
    return list(res.scalars().all())


async def count_in_session(db: AsyncSession, conversation_id: uuid.UUID) -> int:
    res = await db.execute(
        select(func.count())
        .select_from(Document)
        .where(Document.conversation_id == conversation_id)
    )
    return int(res.scalar() or 0)


async def total_size(db: AsyncSession, conversation_id: uuid.UUID) -> int:
    """Sum of stored bytes in the session — single fast aggregate query."""
    res = await db.execute(
        select(func.coalesce(func.sum(Document.size_bytes), 0)).where(
            Document.conversation_id == conversation_id
        )
    )
    return int(res.scalar() or 0)


async def get_by_id(db: AsyncSession, document_id: str | uuid.UUID) -> Document | None:
    try:
        return await db.get(Document, uuid.UUID(str(document_id)))
    except (ValueError, AttributeError):
        return None


async def get_owned(
    db: AsyncSession, document_id: str | uuid.UUID, user_id: uuid.UUID
) -> Document | None:
    doc = await get_by_id(db, document_id)
    if doc is None or doc.user_id != user_id:
        return None
    return doc


async def exists_sha(db: AsyncSession, conversation_id: uuid.UUID, sha256: str) -> bool:
    res = await db.execute(
        select(Document.id).where(
            Document.conversation_id == conversation_id, Document.sha256 == sha256
        )
    )
    return res.first() is not None


async def set_status(
    db: AsyncSession,
    doc: Document,
    status: DocStatus,
    *,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    doc.status = status
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    doc.error = error
    await db.flush()
