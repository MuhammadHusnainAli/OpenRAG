"""The one retrieval function. The ``user_id`` filter is mandatory on every
query — it is a keyword-only argument with no default, so no call site can
forget it. Treat any retrieval path without it as a critical bug.
"""

from __future__ import annotations

from typing import Literal

from qdrant_client import models

from app.config import get_settings
from app.rag.embeddings import embed_dense_query, embed_sparse
from app.rag.qdrant import DENSE, SPARSE, qdrant

_settings = get_settings()

SearchType = Literal["hybrid", "dense", "sparse"]


def _tenant_filter(
    user_id: str, session_id: str, document_ids: list[str] | None
) -> models.Filter:
    must = [
        models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
        models.FieldCondition(key="session_id", match=models.MatchValue(value=session_id)),
    ]
    if document_ids:
        must.append(
            models.FieldCondition(key="document_id", match=models.MatchAny(any=document_ids))
        )
    return models.Filter(must=must)


async def search_documents(
    *,
    user_id: str,
    session_id: str,
    query: str,
    search_type: SearchType = "hybrid",
    top_k: int = 8,
    document_ids: list[str] | None = None,
) -> list[dict]:
    top_k = max(1, min(top_k, 20))
    flt = _tenant_filter(user_id, session_id, document_ids)

    if search_type == "dense":
        dense_vec = await embed_dense_query(query)
        res = await qdrant.query_points(
            _settings.qdrant_collection, query=dense_vec, using=DENSE,
            query_filter=flt, limit=top_k, with_payload=True,
        )
    elif search_type == "sparse":
        sparse_vec = embed_sparse([query])[0]
        res = await qdrant.query_points(
            _settings.qdrant_collection, query=sparse_vec, using=SPARSE,
            query_filter=flt, limit=top_k, with_payload=True,
        )
    else:  # hybrid — prefetch from both, fuse with RRF
        dense_vec = await embed_dense_query(query)
        sparse_vec = embed_sparse([query])[0]
        res = await qdrant.query_points(
            _settings.qdrant_collection,
            prefetch=[
                models.Prefetch(query=dense_vec, using=DENSE, filter=flt, limit=top_k * 3),
                models.Prefetch(query=sparse_vec, using=SPARSE, filter=flt, limit=top_k * 3),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=flt, limit=top_k, with_payload=True,
        )

    return _format(res.points)


def _format(points) -> list[dict]:
    return [
        {
            "text": p.payload["text"],
            "document_id": p.payload["document_id"],
            "chunk_index": p.payload["chunk_index"],
            "source": p.payload["source"],
            "score": p.score,
        }
        for p in points
    ]


async def search_agent_knowledge(
    *,
    agent_id: str,
    version_id: str,
    query: str,
    search_type: SearchType = "hybrid",
    top_k: int = 8,
) -> list[dict]:
    """Retrieve from a custom agent's version-scoped knowledge. The version_id is
    bound by the server (never the model), keeping each version's knowledge isolated.
    """
    top_k = max(1, min(top_k, 20))
    flt = models.Filter(
        must=[
            models.FieldCondition(key="agent_id", match=models.MatchValue(value=agent_id)),
            models.FieldCondition(key="version_id", match=models.MatchValue(value=version_id)),
        ]
    )

    if search_type == "dense":
        dense_vec = await embed_dense_query(query)
        res = await qdrant.query_points(
            _settings.qdrant_collection, query=dense_vec, using=DENSE,
            query_filter=flt, limit=top_k, with_payload=True,
        )
    elif search_type == "sparse":
        sparse_vec = embed_sparse([query])[0]
        res = await qdrant.query_points(
            _settings.qdrant_collection, query=sparse_vec, using=SPARSE,
            query_filter=flt, limit=top_k, with_payload=True,
        )
    else:
        dense_vec = await embed_dense_query(query)
        sparse_vec = embed_sparse([query])[0]
        res = await qdrant.query_points(
            _settings.qdrant_collection,
            prefetch=[
                models.Prefetch(query=dense_vec, using=DENSE, filter=flt, limit=top_k * 3),
                models.Prefetch(query=sparse_vec, using=SPARSE, filter=flt, limit=top_k * 3),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=flt, limit=top_k, with_payload=True,
        )
    return _format(res.points)
