"""Integration fixtures — re-exported from tests._support. These tests drive the
internal layers (services / repositories / RAG index + search) directly against
real Postgres + Qdrant + RabbitMQ, without going through the HTTP API. See
_support.py."""

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
