"""Shared fixtures for the service-backed suites (integration + e2e).

Both suites run against REAL Postgres + Qdrant + RabbitMQ (provided by the
`test` profile in docker-compose.yml) but mock the two things that need paid
API keys / network: the embedding models and the LLM agent stream. That keeps
the tests deterministic and offline while still exercising the full
request → service → repository → DB / vector-store path.

`tests/integration/conftest.py` and `tests/e2e/conftest.py` re-export the names
they need from here so the same fixtures back both suites.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from qdrant_client import models
from sqlalchemy import text

from app.config import get_llm_config
from app.core.db import session_scope
from app.main import app
from app.rag.qdrant import ensure_collection

PASSWORD = "averylongpassword1"
BASE_URL = "http://test"


def _new_transport() -> ASGITransport:
    return ASGITransport(app=app)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_collection():
    await ensure_collection()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _clean_db():
    """Truncate user-owned data + ephemeral tables between tests (FK CASCADE
    clears agents/conversations/documents/messages/tokens)."""
    async with session_scope() as db:
        await db.execute(
            text(
                "TRUNCATE users, login_attempts, rate_limit_counters "
                "RESTART IDENTITY CASCADE"
            )
        )
        await db.commit()
    yield


@pytest.fixture(autouse=True)
def _mock_models(monkeypatch):
    """Replace embeddings + the LLM agent stream with deterministic offline stand-ins."""
    dim = get_llm_config().embedding.dimensions or 3072

    async def fake_dense(texts: list[str]):
        return [[0.01] * dim for _ in texts]

    async def fake_dense_query(_text: str):
        return [0.01] * dim

    def fake_sparse(texts: list[str]):
        return [models.SparseVector(indices=[1, 2, 3], values=[1.0, 1.0, 1.0]) for _ in texts]

    # patch at the call sites (index.py / search.py import the names directly)
    monkeypatch.setattr("app.rag.index.embed_dense", fake_dense)
    monkeypatch.setattr("app.rag.index.embed_sparse", fake_sparse)
    monkeypatch.setattr("app.rag.search.embed_dense_query", fake_dense_query)
    monkeypatch.setattr("app.rag.search.embed_sparse", fake_sparse)

    async def fake_stream(**_kwargs):
        yield {"type": "token", "data": "Test answer."}
        yield {
            "type": "done",
            "citations": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.services.chat_service.stream_agent", fake_stream)
    monkeypatch.setattr(
        "app.services.agent_chat_service.stream_agent_knowledge", fake_stream
    )


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=_new_transport(), base_url=BASE_URL) as c:
        yield c


@pytest_asyncio.fixture
async def make_user():
    """Factory → a fresh authenticated AsyncClient + its email (cookies stick)."""
    clients: list[AsyncClient] = []

    async def _make() -> tuple[AsyncClient, str]:
        c = AsyncClient(transport=_new_transport(), base_url=BASE_URL)
        clients.append(c)
        email = f"u{uuid.uuid4().hex[:10]}@example.com"
        await c.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": "User"},
        )
        await c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        return c, email

    yield _make
    for c in clients:
        await c.aclose()


def upload_file(name: str = "doc.txt", body: bytes = b"The capital of France is Paris."):
    return {"file": (name, body, "text/plain")}
