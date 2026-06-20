"""OAuth signed-state CSRF protection."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")
pytest.importorskip("itsdangerous")

from app.services import oauth_service  # noqa: E402
from app.services.errors import ServiceError  # noqa: E402


def test_state_roundtrip_ok():
    state = oauth_service.make_state("google")
    # cookie == query and provider matches => no exception
    oauth_service.verify_state("google", state, state)


def test_state_mismatch_rejected():
    state = oauth_service.make_state("google")
    with pytest.raises(ServiceError):
        oauth_service.verify_state("google", state, "tampered")


def test_state_provider_mismatch_rejected():
    state = oauth_service.make_state("google")
    with pytest.raises(ServiceError):
        oauth_service.verify_state("github", state, state)


def test_missing_state_rejected():
    with pytest.raises(ServiceError):
        oauth_service.verify_state("google", None, None)
