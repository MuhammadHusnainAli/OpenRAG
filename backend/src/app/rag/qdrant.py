"""Qdrant client + collection bootstrap.

One shared collection, two named vectors (dense + sparse), tenant isolation by
payload filtering. ``user_id`` is registered as a tenant payload index so
per-tenant filtering stays fast and hot.
"""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient, models

from app.config import get_llm_config, get_settings

_settings = get_settings()

qdrant = AsyncQdrantClient(url=_settings.qdrant_url, api_key=_settings.qdrant_api_key)

DENSE = "dense"
SPARSE = "sparse"


async def ensure_collection() -> None:
    if await qdrant.collection_exists(_settings.qdrant_collection):
        return

    dim = get_llm_config().embedding.dimensions or 3072
    await qdrant.create_collection(
        collection_name=_settings.qdrant_collection,
        vectors_config={
            DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF),
        },
        # multitenant-friendly HNSW: keep the per-tenant payload graph hot
        hnsw_config=models.HnswConfigDiff(payload_m=16, m=0),
    )
    await qdrant.create_payload_index(
        _settings.qdrant_collection,
        field_name="user_id",
        field_schema=models.KeywordIndexParams(type="keyword", is_tenant=True),
    )
    for field in ("session_id", "document_id", "agent_id", "version_id"):
        await qdrant.create_payload_index(
            _settings.qdrant_collection,
            field_name=field,
            field_schema=models.KeywordIndexParams(type="keyword"),
        )


async def delete_by_document(user_id: str, document_id: str) -> None:
    """Delete all points for one document (tenant-scoped)."""
    await qdrant.delete(
        _settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                    models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=document_id)
                    ),
                ]
            )
        ),
    )


async def delete_by_session(user_id: str, session_id: str) -> None:
    """Delete all points for a whole conversation (tenant-scoped)."""
    await qdrant.delete(
        _settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                    models.FieldCondition(
                        key="session_id", match=models.MatchValue(value=session_id)
                    ),
                ]
            )
        ),
    )


async def _delete_by(field: str, value: str) -> None:
    await qdrant.delete(
        _settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key=field, match=models.MatchValue(value=value))]
            )
        ),
    )


async def delete_by_agent(agent_id: str) -> None:
    """Delete all points for every version of an agent."""
    await _delete_by("agent_id", agent_id)


async def delete_by_agent_document(version_id: str, document_id: str) -> None:
    """Delete points for one document within a version."""
    await qdrant.delete(
        _settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="version_id", match=models.MatchValue(value=version_id)),
                    models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=document_id)
                    ),
                ]
            )
        ),
    )
