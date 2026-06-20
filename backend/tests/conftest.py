"""Test bootstrap. Sets the minimal env the config requires BEFORE any app
module is imported (Settings has required fields with no defaults)."""

from __future__ import annotations

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-0123456789abcdef")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://openrag:openrag@localhost:5432/openrag_test"
)
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("REQUIRE_EMAIL_VERIFICATION", "false")

# The integration/ and e2e/ suites need real Postgres + Qdrant + RabbitMQ and the
# heavy ML deps. They are collected ONLY when RUN_INTEGRATION=1 (set by the Docker
# test harness). Otherwise both dirs (incl. their conftests) are skipped so the
# unit suite runs anywhere with no services.
collect_ignore: list[str] = []
if os.environ.get("RUN_INTEGRATION") != "1":
    collect_ignore = ["integration", "e2e"]
