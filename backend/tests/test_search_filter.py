"""Tenant-isolation invariant: every search filter scopes by user_id + session_id."""

from __future__ import annotations

import pytest

pytest.importorskip("qdrant_client")
pytest.importorskip("litellm")

from app.rag.search import _tenant_filter  # noqa: E402


def test_filter_always_includes_user_and_session():
    flt = _tenant_filter("user-1", "sess-1", None)
    keys = {c.key for c in flt.must}
    assert "user_id" in keys
    assert "session_id" in keys


def test_document_ids_add_a_condition_but_keep_tenant():
    flt = _tenant_filter("user-1", "sess-1", ["doc-1", "doc-2"])
    keys = {c.key for c in flt.must}
    assert {"user_id", "session_id", "document_id"} <= keys
