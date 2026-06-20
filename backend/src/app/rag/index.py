"""Upsert chunks into Qdrant as points with dense + sparse named vectors and a
tenant-scoped payload. Used by the ingestion worker.
"""

from __future__ import annotations

import uuid

from qdrant_client import models

from app.config import get_settings
from app.rag.embeddings import embed_dense, embed_sparse
from app.rag.qdrant import DENSE, SPARSE, qdrant

_settings = get_settings()
_BATCH = 128


async def index_chunks(
    *,
    user_id: str,
    session_id: str,
    document_id: str,
    source: str,
    chunks: list[str],
) -> int:
    if not chunks:
        return 0

    dense = await embed_dense(chunks)
    sparse = embed_sparse(chunks)

    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector={DENSE: dense[i], SPARSE: sparse[i]},
            payload={
                "user_id": user_id,
                "session_id": session_id,
                "document_id": document_id,
                "chunk_index": i,
                "text": chunks[i],
                "source": source,
            },
        )
        for i in range(len(chunks))
    ]

    for start in range(0, len(points), _BATCH):
        await qdrant.upsert(
            _settings.qdrant_collection, points=points[start : start + _BATCH], wait=True
        )
    return len(points)


async def index_agent_chunks(
    *,
    agent_id: str,
    version_id: str,
    document_id: str,
    owner_id: str,
    source: str,
    chunks: list[str],
) -> int:
    """Index a custom-agent knowledge document, scoped to its version (own vectors)."""
    if not chunks:
        return 0

    dense = await embed_dense(chunks)
    sparse = embed_sparse(chunks)

    points = [
        models.PointStruct(
            id=str(uuid.uuid4()),
            vector={DENSE: dense[i], SPARSE: sparse[i]},
            payload={
                "agent_id": agent_id,
                "version_id": version_id,
                "document_id": document_id,
                "owner_id": owner_id,
                "chunk_index": i,
                "text": chunks[i],
                "source": source,
            },
        )
        for i in range(len(chunks))
    ]

    for start in range(0, len(points), _BATCH):
        await qdrant.upsert(
            _settings.qdrant_collection, points=points[start : start + _BATCH], wait=True
        )
    return len(points)
