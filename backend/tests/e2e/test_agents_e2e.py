"""E2E custom agents: create → knowledge → deploy live → versions → chat →
public sharing (key) → restricted access control."""

from __future__ import annotations

import pytest

from app.services import ingestion
from tests._support import upload_file

pytestmark = pytest.mark.e2e


async def _make_live_agent(c):
    """Create an agent, add+ingest knowledge, set a prompt, deploy live. Returns id."""
    aid = (await c.post("/api/agents", json={"name": "Helper", "description": "d"})).json()["id"]

    kdoc = (await c.post(f"/api/agents/{aid}/knowledge", files=upload_file())).json()
    await ingestion._ingest_agent(kdoc["id"])

    await c.patch(f"/api/agents/{aid}/draft", json={"system_prompt": "You are helpful."})

    r = await c.post(f"/api/agents/{aid}/deploy", json={"mode": "live", "change_summary": "first"})
    assert r.status_code == 200
    assert r.json()["version_number"] == 1
    assert r.json()["status"] == "live"
    return aid


async def test_agent_full_lifecycle(make_user):
    c, _ = await make_user()
    aid = await _make_live_agent(c)

    # agent now has a default (live) version
    agent = (await c.get(f"/api/agents/{aid}")).json()
    assert agent["default_version_id"] is not None

    # version history
    versions = (await c.get(f"/api/agents/{aid}/versions")).json()
    assert len(versions) >= 1
    assert any(v["change_summary"] for v in versions)

    # owner can chat with the live agent (LLM mocked)
    r = await c.post(f"/api/agents/{aid}/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert "Test answer." in r.text


async def test_versioning_creates_v2_and_updates_default(make_user):
    c, _ = await make_user()
    aid = await _make_live_agent(c)
    v1 = (await c.get(f"/api/agents/{aid}")).json()["default_version_id"]

    # editing again creates a fresh draft cloned from v1, then deploy → v2
    await c.post(f"/api/agents/{aid}/draft")
    await c.patch(f"/api/agents/{aid}/draft", json={"system_prompt": "v2 prompt"})
    r = await c.post(f"/api/agents/{aid}/deploy", json={"mode": "live"})
    assert r.json()["version_number"] == 2

    v2 = (await c.get(f"/api/agents/{aid}")).json()["default_version_id"]
    assert v2 != v1  # newest live becomes default


async def test_public_sharing_with_key(make_user, client):
    owner, _ = await make_user()
    aid = await _make_live_agent(owner)

    r = await owner.patch(
        f"/api/agents/{aid}/sharing", json={"visibility": "public", "public_key": "s3cret"}
    )
    slug = r.json()["public_slug"]
    assert slug

    # anonymous meta
    meta = (await client.get(f"/api/agents/public/{slug}")).json()
    assert meta["needs_key"] is True and meta["is_live"] is True

    # wrong / missing key rejected
    assert (await client.post(f"/api/agents/public/{slug}/chat", json={"message": "hi"})).status_code == 401

    # correct key streams an answer
    r = await client.post(
        f"/api/agents/public/{slug}/chat",
        json={"message": "hi"},
        headers={"X-Agent-Key": "s3cret"},
    )
    assert r.status_code == 200
    assert "Test answer." in r.text


async def test_restricted_access_control(make_user):
    owner, _ = await make_user()
    other, other_email = await make_user()
    aid = await _make_live_agent(owner)

    await owner.patch(f"/api/agents/{aid}/sharing", json={"visibility": "restricted"})

    # not granted yet → hidden
    assert (await other.post(f"/api/agents/{aid}/chat", json={"message": "hi"})).status_code == 404

    # grant, then they can chat
    r = await owner.post(f"/api/agents/{aid}/access", json={"email": other_email})
    assert r.status_code == 204
    r = await other.post(f"/api/agents/{aid}/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert "Test answer." in r.text


async def test_delete_agent(make_user):
    c, _ = await make_user()
    aid = await _make_live_agent(c)
    assert (await c.delete(f"/api/agents/{aid}")).status_code == 204
    assert (await c.get(f"/api/agents/{aid}")).status_code == 404
