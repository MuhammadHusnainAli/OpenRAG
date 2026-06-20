"""Email gating: syntax + deliverability, disposable-domain block, allowlist.

Used at registration and OAuth signup. Verification + rate limits remain the
durable backstop; the blocklist is best-effort.
"""

from __future__ import annotations

from email_validator import EmailNotValidError, validate_email

from app.config import get_settings

try:
    from disposable_email_domains import blocklist as _disposable_blocklist
except Exception:  # pragma: no cover - package optional at import time
    _disposable_blocklist = set()


class EmailRejected(ValueError):
    """Raised when an email fails policy. Message is safe to surface generically."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_registration_email(email: str, *, check_deliverability: bool = True) -> str:
    """Validate and normalize an email per policy. Returns the normalized address."""
    settings = get_settings()
    try:
        result = validate_email(email, check_deliverability=check_deliverability)
    except EmailNotValidError as exc:
        raise EmailRejected("Please enter a valid email address.") from exc

    normalized = result.normalized.lower()
    domain = normalized.rsplit("@", 1)[-1]

    allowlist = settings.allowlist_domains
    if allowlist and domain not in allowlist:
        raise EmailRejected("This email domain is not permitted to register.")

    if settings.disposable_block and domain in _disposable_blocklist:
        raise EmailRejected("Disposable email addresses are not allowed.")

    return normalized
