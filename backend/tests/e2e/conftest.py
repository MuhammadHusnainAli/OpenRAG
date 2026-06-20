"""E2E fixtures — re-exported from tests._support so the HTTP-level suite and
the integration suite share the same service-backed setup. See _support.py."""

from __future__ import annotations

from tests._support import (  # noqa: F401
    BASE_URL,
    PASSWORD,
    _clean_db,
    _mock_models,
    _prepare_collection,
    client,
    make_user,
    upload_file,
)
