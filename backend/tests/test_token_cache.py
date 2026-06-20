"""In-memory JWT denylist cache (the RabbitMQ-backed auth cache's local store)."""

from __future__ import annotations

import time

from app.core import token_cache


def setup_function():
    token_cache.clear()


def test_unknown_jti_is_not_revoked():
    assert not token_cache.is_revoked("nope")


def test_add_and_detect_revoked():
    token_cache.add("jti-1", time.time() + 60)
    assert token_cache.is_revoked("jti-1")


def test_already_expired_entry_is_ignored():
    token_cache.add("jti-old", time.time() - 5)  # add() drops past-exp entries
    assert not token_cache.is_revoked("jti-old")
    assert token_cache.size() == 0


def test_bulk_load_and_size():
    token_cache.bulk_load([("a", time.time() + 60), ("b", time.time() + 60)])
    assert token_cache.is_revoked("a") and token_cache.is_revoked("b")
    assert token_cache.size() == 2


def test_prune_removes_expired(monkeypatch):
    token_cache.add("live", time.time() + 60)
    token_cache._revoked["dead"] = time.time() - 1  # inject a stale entry
    token_cache.prune()
    assert token_cache.is_revoked("live")
    assert "dead" not in token_cache._revoked
