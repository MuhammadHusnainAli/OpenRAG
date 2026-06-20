"""E2E auth: register → login → me → refresh → logout, plus negative paths."""

from __future__ import annotations

import uuid

import pytest

from tests._support import PASSWORD

pytestmark = pytest.mark.e2e


async def test_register_login_me(client):
    email = f"u{uuid.uuid4().hex[:10]}@example.com"

    r = await client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Alice"},
    )
    assert r.status_code == 202

    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["email"] == email

    r = await client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["email"] == email


async def test_login_wrong_password_is_generic(client):
    email = f"u{uuid.uuid4().hex[:10]}@example.com"
    await client.post(
        "/api/auth/register", json={"email": email, "password": PASSWORD, "display_name": "A"}
    )
    r = await client.post("/api/auth/login", json={"email": email, "password": "wrongwrongwrong1"})
    assert r.status_code == 401


async def test_me_requires_auth(client):
    r = await client.get("/api/me")
    assert r.status_code == 401


async def test_refresh_then_logout(make_user):
    c, _email = await make_user()

    r = await c.post("/api/auth/refresh")
    assert r.status_code == 200

    r = await c.get("/api/me")
    assert r.status_code == 200

    r = await c.post("/api/auth/logout")
    assert r.status_code == 200

    r = await c.get("/api/me")
    assert r.status_code == 401
