"""Document orchestration: validated upload + enqueue ingestion, listing, and
delete (Qdrant points + blob + row)."""

from __future__ import annotations

import os

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.celery_app import celery_app
from app.data.models import Conversation, Document, User
from app.data.repositories import documents as doc_repo
from app.rag.qdrant import delete_by_document
from app.services.errors import Conflict, NotFound, UploadError
from app.utils.files import UploadRejected, validate_and_store

_settings = get_settings()

INGEST_TASK = "app.services.ingestion.ingest_document"


async def upload(
    db: AsyncSession, conv: Conversation, user: User, file: UploadFile
) -> Document:
    # 1) file-count cap (50 per chat)
    if await doc_repo.count_in_session(db, conv.id) >= _settings.max_files_per_session:
        raise Conflict(
            f"Document limit reached ({_settings.max_files_per_session} per chat)."
        )

    # 2) stream to disk with the per-file size cap (50 MB) enforced as bytes arrive
    try:
        meta = await validate_and_store(file, user.id, conv.id)
    except UploadRejected as exc:
        raise UploadError(str(exc)) from exc

    # 3) total-size cap for the chat (1 GB) — one aggregate query + the new file
    existing_total = await doc_repo.total_size(db, conv.id)
    if existing_total + meta["size_bytes"] > _settings.max_session_total_bytes:
        _safe_unlink(meta["storage_path"])
        raise Conflict(
            f"Chat storage limit reached ({_settings.max_session_total_mb} MB total)."
        )

    # 4) dedupe identical files within the chat
    if await doc_repo.exists_sha(db, conv.id, meta["sha256"]):
        _safe_unlink(meta["storage_path"])
        raise Conflict("This file has already been uploaded to this session.")

    doc = await doc_repo.create(
        db, conversation_id=conv.id, user_id=user.id, **meta
    )
    # commit so the worker can see the row before it picks up the job
    await db.commit()

    celery_app.send_task(INGEST_TASK, args=[str(doc.id)])
    return doc


async def list_for_session(db: AsyncSession, conv: Conversation) -> list[Document]:
    return await doc_repo.list_for_session(db, conv.id)


async def delete(db: AsyncSession, document_id: str, user: User) -> None:
    doc = await doc_repo.get_owned(db, document_id, user.id)
    if doc is None:
        raise NotFound("Document not found.")

    storage_path = doc.storage_path
    doc_id, user_id = str(doc.id), str(doc.user_id)

    await db.delete(doc)
    await db.flush()
    await delete_by_document(user_id, doc_id)
    _safe_unlink(storage_path)


def _safe_unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
