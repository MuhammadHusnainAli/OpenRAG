"""E2E conversation chat (LLM mocked) + history persistence."""

from __future__ import annotations

import pytest

from app.services import ingestion
from tests._support import upload_file

pytestmark = pytest.mark.e2e


async def test_chat_streams_and_persists(make_user):
    c, _ = await make_user()
    sid = (await c.post("/api/sessions", json={"title": "T"})).json()["id"]

    doc_id = (await c.post(f"/api/sessions/{sid}/documents", files=upload_file())).json()["id"]
    await ingestion._ingest(doc_id)

    r = await c.post(f"/api/sessions/{sid}/chat", json={"message": "What is the capital of France?"})
    assert r.status_code == 200
    assert "Test answer." in r.text
    assert '"done": true' in r.text

    # both turns persisted as history
    r = await c.get(f"/api/sessions/{sid}")
    roles = [m["role"] for m in r.json()["messages"]]
    assert "user" in roles and "assistant" in roles


async def test_chat_requires_ownership(make_user):
    a, _ = await make_user()
    b, _ = await make_user()
    sid = (await a.post("/api/sessions", json={"title": "T"})).json()["id"]

    r = await b.post(f"/api/sessions/{sid}/chat", json={"message": "hi"})
    assert r.status_code == 404
