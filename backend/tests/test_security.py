"""Password hashing + JWT logic."""

from __future__ import annotations

import jwt
import pytest

from app.core import security


def test_password_roundtrip():
    h = security.hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert security.verify_password("correct horse battery staple", h)
    assert not security.verify_password("wrong password", h)


def test_verify_handles_garbage_hash():
    assert not security.verify_password("anything", "not-a-valid-hash")


def test_access_token_roundtrip():
    token, jti, exp = security.create_access_token("11111111-1111-1111-1111-111111111111")
    payload = security.decode_access_token(token)
    assert payload["sub"] == "11111111-1111-1111-1111-111111111111"
    assert payload["jti"] == jti
    assert payload["type"] == "access"


def test_decode_rejects_tampered_token():
    token, _, _ = security.create_access_token("u")
    tampered = token[:-3] + ("abc" if not token.endswith("abc") else "xyz")
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token(tampered)


def test_decode_rejects_wrong_secret():
    forged = jwt.encode(
        {"sub": "u", "jti": "x", "type": "access", "iat": 0, "exp": 9999999999},
        "a-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(jwt.PyJWTError):
        security.decode_access_token(forged)


def test_sha256_hex_is_deterministic():
    assert security.sha256_hex("abc") == security.sha256_hex("abc")
    assert security.sha256_hex("abc") != security.sha256_hex("abd")


def test_opaque_tokens_are_unique():
    a, b = security.generate_opaque_token(), security.generate_opaque_token()
    assert a != b and len(a) > 20
