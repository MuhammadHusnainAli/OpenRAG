"""Settings-derived limit math (document caps)."""

from __future__ import annotations

from app.config import get_settings


def test_byte_limit_properties():
    s = get_settings()
    assert s.max_upload_bytes == s.max_upload_mb * 1024 * 1024
    assert s.max_session_total_bytes == s.max_session_total_mb * 1024 * 1024


def test_default_document_limits():
    s = get_settings()
    # 50 files / 50 MB each / 1 GB total per chat
    assert s.max_files_per_session == 50
    assert s.max_upload_mb == 50
    assert s.max_session_total_mb == 1024


def test_cors_origins_split(monkeypatch):
    s = get_settings()
    assert isinstance(s.cors_origins, list)
    assert all(o for o in s.cors_origins)
