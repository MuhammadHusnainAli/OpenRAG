"""Background document ingestion (Celery task on the RabbitMQ broker).

parse -> chunk -> embed -> upsert to Qdrant -> mark ready/failed. Status lives in
the ``documents`` table (no result backend needed).

Celery runs tasks synchronously, but our I/O is async. Each worker child keeps
ONE persistent event loop (``_run``) so the cached async engine / Qdrant client /
embedders stay bound to a single, stable loop across tasks.
"""

from __future__ import annotations

import asyncio

from app.core.celery_app import celery_app
from app.core.db import session_scope
from app.core.logging import get_logger
from app.data.models import DocStatus
from app.data.repositories import agents as agent_repo
from app.data.repositories import documents as doc_repo
from app.rag.chunking import chunk
from app.rag.index import index_agent_chunks, index_chunks
from app.rag.parsing import parse_document

log = get_logger("ingestion")

_loop: asyncio.AbstractEventLoop | None = None


def _run(coro):
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


async def _set_status(
    document_id: str,
    status: DocStatus,
    *,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    async with session_scope() as db:
        doc = await doc_repo.get_by_id(db, document_id)
        if doc is not None:
            await doc_repo.set_status(db, doc, status, chunk_count=chunk_count, error=error)
        await db.commit()


async def _ingest(document_id: str) -> None:
    async with session_scope() as db:
        doc = await doc_repo.get_by_id(db, document_id)
        if doc is None:
            log.warning("ingest.missing_document", document_id=document_id)
            return
        storage_path = doc.storage_path
        user_id = str(doc.user_id)
        conversation_id = str(doc.conversation_id)
        filename = doc.filename

    await _set_status(document_id, DocStatus.processing)
    try:
        text = await asyncio.to_thread(parse_document, storage_path)
        chunks = await asyncio.to_thread(chunk, text)
        n = await index_chunks(
            user_id=user_id,
            session_id=conversation_id,
            document_id=document_id,
            source=filename,
            chunks=chunks,
        )
    except Exception as exc:  # noqa: BLE001 — record and mark failed, don't crash worker
        log.error("ingest.failed", document_id=document_id, error=str(exc))
        await _set_status(document_id, DocStatus.failed, error=str(exc)[:500])
        return

    await _set_status(document_id, DocStatus.ready, chunk_count=n)
    log.info("ingest.ready", document_id=document_id, chunks=n)


@celery_app.task(name="app.services.ingestion.ingest_document")
def ingest_document(document_id: str) -> None:
    _run(_ingest(document_id))


#  custom-agent knowledge ingestion 
async def _set_agent_doc_status(
    document_id: str,
    status: DocStatus,
    *,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    async with session_scope() as db:
        doc = await agent_repo.get_document(db, document_id)
        if doc is not None:
            await agent_repo.set_doc_status(db, doc, status, chunk_count=chunk_count, error=error)
        await db.commit()


async def _ingest_agent(document_id: str) -> None:
    async with session_scope() as db:
        doc = await agent_repo.get_document(db, document_id)
        if doc is None:
            log.warning("ingest.agent.missing_document", document_id=document_id)
            return
        storage_path = doc.storage_path
        agent_id = str(doc.agent_id)
        version_id = str(doc.version_id)
        owner_id = str(doc.owner_id)
        filename = doc.filename

    await _set_agent_doc_status(document_id, DocStatus.processing)
    try:
        text = await asyncio.to_thread(parse_document, storage_path)
        chunks = await asyncio.to_thread(chunk, text)
        n = await index_agent_chunks(
            agent_id=agent_id,
            version_id=version_id,
            document_id=document_id,
            owner_id=owner_id,
            source=filename,
            chunks=chunks,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("ingest.agent.failed", document_id=document_id, error=str(exc))
        await _set_agent_doc_status(document_id, DocStatus.failed, error=str(exc)[:500])
        return

    await _set_agent_doc_status(document_id, DocStatus.ready, chunk_count=n)
    log.info("ingest.agent.ready", document_id=document_id, chunks=n)


@celery_app.task(name="app.services.ingestion.ingest_agent_document")
def ingest_agent_document(document_id: str) -> None:
    _run(_ingest_agent(document_id))
