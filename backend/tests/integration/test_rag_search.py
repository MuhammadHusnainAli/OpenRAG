"""Integration test of the RAG index/search path against real Qdrant, asserting
version-scoped isolation (embeddings mocked)."""

from __future__ import annotations

import uuid

import pytest

from app.rag.index import index_agent_chunks
from app.rag.search import search_agent_knowledge

pytestmark = pytest.mark.integration


async def test_index_then_search_and_version_isolation():
    agent_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())

    n = await index_agent_chunks(
        agent_id=agent_id,
        version_id=version_id,
        document_id=str(uuid.uuid4()),
        owner_id=str(uuid.uuid4()),
        source="france.txt",
        chunks=["The capital of France is Paris.", "Paris is on the Seine."],
    )
    assert n == 2

    hits = await search_agent_knowledge(
        agent_id=agent_id, version_id=version_id, query="capital of France", top_k=5
    )
    assert len(hits) >= 1
    assert all(h["source"] == "france.txt" for h in hits)

    # a different version of the same agent must see nothing (isolation)
    other = await search_agent_knowledge(
        agent_id=agent_id, version_id=str(uuid.uuid4()), query="capital", top_k=5
    )
    assert other == []
