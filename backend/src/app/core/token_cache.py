"""In-process JWT denylist cache.

Access-token validation must be fast (every authenticated request hits it), so
revoked JTIs are held in-memory and checked in O(1). Postgres is the durable
source of truth (loaded on startup) and RabbitMQ fan-out keeps every process's
cache coherent (see ``core/broker.py``). Entries self-expire at the token's
own exp, so the set stays bounded.
"""

from __future__ import annotations

import time

# jti -> expiry (epoch seconds)
_revoked: dict[str, float] = {}


def add(jti: str, exp_epoch: float) -> None:
    if exp_epoch > time.time():
        _revoked[jti] = exp_epoch


def is_revoked(jti: str) -> bool:
    exp = _revoked.get(jti)
    if exp is None:
        return False
    if exp <= time.time():
        _revoked.pop(jti, None)
        return False
    return True


def bulk_load(entries: list[tuple[str, float]]) -> None:
    for jti, exp in entries:
        add(jti, exp)


def prune() -> None:
    now = time.time()
    for jti in [j for j, e in _revoked.items() if e <= now]:
        _revoked.pop(jti, None)


def size() -> int:
    return len(_revoked)


def clear() -> None:
    _revoked.clear()
