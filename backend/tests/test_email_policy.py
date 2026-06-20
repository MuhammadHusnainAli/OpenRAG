"""Email gating: normalization + disposable blocking."""

from __future__ import annotations

import pytest

from app.core.disposable_email import EmailRejected, normalize_email, validate_registration_email


def test_normalize_lowercases_and_trims():
    assert normalize_email("  User@Example.COM ") == "user@example.com"


def test_valid_email_passes():
    # deliverability off so no DNS/network in unit tests
    out = validate_registration_email("alice@example.com", check_deliverability=False)
    assert out == "alice@example.com"


def test_disposable_domain_blocked():
    with pytest.raises(EmailRejected):
        validate_registration_email("burner@mailinator.com", check_deliverability=False)
