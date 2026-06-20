"""Rate-limit spec parsing."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("starlette")

from app.core.rate_limit import parse_rate  # noqa: E402


def test_parse_minute():
    assert parse_rate("5/minute") == (5, 60)


def test_parse_hour():
    assert parse_rate("30/hour") == (30, 3600)


def test_parse_second_and_day():
    assert parse_rate("10/second") == (10, 1)
    assert parse_rate("1000/day") == (1000, 86400)


def test_parse_plural_units():
    assert parse_rate("3/minutes") == (3, 60)
