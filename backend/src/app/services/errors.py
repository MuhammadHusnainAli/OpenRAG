"""Domain errors. Services raise these; a single handler in main.py maps them to
clean JSON responses so internals never leak and auth stays non-enumerable.
"""

from __future__ import annotations


class ServiceError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, detail: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class InvalidCredentials(ServiceError):
    status_code = 401
    code = "invalid_credentials"


class AccountLocked(ServiceError):
    status_code = 429
    code = "account_locked"


class EmailPolicyError(ServiceError):
    status_code = 400
    code = "email_rejected"


class TokenError(ServiceError):
    status_code = 401
    code = "invalid_token"


class NotFound(ServiceError):
    status_code = 404
    code = "not_found"


class Conflict(ServiceError):
    status_code = 409
    code = "conflict"


class BudgetExceeded(ServiceError):
    status_code = 429
    code = "token_budget_exceeded"


class UploadError(ServiceError):
    status_code = 400
    code = "upload_rejected"
