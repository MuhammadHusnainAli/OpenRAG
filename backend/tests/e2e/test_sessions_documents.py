"""E2E sessions + document upload/ingest + tenant isolation."""

from __future__ import annotations

import pytest

from app.services import ingestion
from tests._support import upload_file

pytestmark = pytest.mark.e2e


async def test_session_crud(make_user):
    c, _ = await make_user()

    r = await c.post("/api/sessions", json={"title": "My chat"})
    assert r.status_code == 201
    sid = r.json()["id"]

    r = await c.get("/api/sessions")
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())

    r = await c.patch(f"/api/sessions/{sid}", json={"title": "Renamed"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed"

    r = await c.delete(f"/api/sessions/{sid}")
    assert r.status_code == 204


async def test_upload_then_ingest_marks_ready(make_user):
    c, _ = await make_user()
    sid = (await c.post("/api/sessions", json={"title": "T"})).json()["id"]

    r = await c.post(f"/api/sessions/{sid}/documents", files=upload_file())
    assert r.status_code == 201
    doc_id = r.json()["id"]
    assert r.json()["status"] == "pending"

    # run the ingestion coroutine directly (stands in for the Celery worker)
    await ingestion._ingest(doc_id)

    r = await c.get(f"/api/sessions/{sid}/documents")
    docs = r.json()
    assert len(docs) == 1
    assert docs[0]["status"] == "ready"
    assert docs[0]["chunk_count"] >= 1


async def test_tenant_isolation_404(make_user):
    a, _ = await make_user()
    b, _ = await make_user()

    sid = (await a.post("/api/sessions", json={"title": "secret"})).json()["id"]

    # B must not see A's session
    assert (await b.get(f"/api/sessions/{sid}")).status_code == 404
    assert (await b.delete(f"/api/sessions/{sid}")).status_code == 404
    assert (await b.get(f"/api/sessions/{sid}/documents")).status_code == 404


async def test_upload_rejects_bad_type(make_user):
    c, _ = await make_user()
    sid = (await c.post("/api/sessions", json={"title": "T"})).json()["id"]

    bad = {"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")}
    r = await c.post(f"/api/sessions/{sid}/documents", files=bad)
    assert r.status_code == 400
