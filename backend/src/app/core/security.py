"""Cryptographic primitives: password hashing, JWT access tokens, opaque tokens.

* Passwords: argon2id (argon2-cffi defaults: mem >= 64MiB, time >= 3).
* Access token: short-lived HS256 JWT, ``algorithms=["HS256"]`` pinned on decode
  so a forged ``alg: none`` / RS-HS-confusion token is rejected. Carries
  ``sub`` (user id), ``jti``, ``type``, ``iat``, ``exp``.
* Refresh tokens: opaque random strings; only their SHA-256 hash is persisted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

_settings = get_settings()
_ph = PasswordHasher()  # argon2id with modern defaults

ALGORITHM = "HS256"


#  passwords 
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except Exception:
        return False


#  access JWT
def create_access_token(user_id: str | uuid.UUID) -> tuple[str, str, dt.datetime]:
    """Return (token, jti, expires_at)."""
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(minutes=_settings.access_token_ttl_min)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "jti": jti,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, _settings.secret_key, algorithm=ALGORITHM)
    return token, jti, expires_at


def decode_access_token(token: str) -> dict:
    """Decode + validate an access token. Raises jwt exceptions on failure."""
    payload = jwt.decode(
        token,
        _settings.secret_key,
        algorithms=[ALGORITHM],  # pinned — rejects alg:none / confusion
        options={"require": ["exp", "iat", "sub", "jti"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


#  opaque tokens (refresh / verification / reset) 
def generate_opaque_token(nbytes: int = 32) -> str:
    """A URL-safe random token with >= 128 bits of entropy."""
    return secrets.token_urlsafe(nbytes)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
