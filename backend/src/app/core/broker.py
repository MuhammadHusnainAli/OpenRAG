"""RabbitMQ fan-out for auth-cache coherence.

When a token is revoked (logout / reset), the JTI is published to a durable
fanout exchange. Every API process binds an exclusive queue to that exchange and
applies revocations to its in-memory ``token_cache`` — so a logout on one
instance is reflected on all of them within milliseconds.

Degrades gracefully: if the broker is unreachable, ``connected`` stays False and
``current_user`` falls back to the Postgres denylist, so it never fails open.
"""

from __future__ import annotations

import json

import aio_pika

from app.config import get_settings
from app.core import token_cache
from app.core.logging import get_logger

_settings = get_settings()
log = get_logger("broker")

EXCHANGE = "auth.revocations"

_connection: aio_pika.abc.AbstractRobustConnection | None = None
_exchange: aio_pika.abc.AbstractExchange | None = None
connected: bool = False


async def connect() -> None:
    """Open the connection, declare the fanout exchange, and start consuming."""
    global _connection, _exchange, connected
    try:
        _connection = await aio_pika.connect_robust(_settings.rabbitmq_url)
        channel = await _connection.channel()
        _exchange = await channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )
        queue = await channel.declare_queue("", exclusive=True, auto_delete=True)
        await queue.bind(_exchange)
        await queue.consume(_on_message, no_ack=False)
        connected = True
        log.info("broker.connected", exchange=EXCHANGE)
    except Exception as exc:  # noqa: BLE001 — degrade to PG fallback
        connected = False
        log.error("broker.connect_failed", error=str(exc))


async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
    async with message.process():
        try:
            payload = json.loads(message.body)
            token_cache.add(payload["jti"], float(payload["exp"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("broker.bad_message", error=str(exc))


async def publish_revocation(jti: str, exp_epoch: float) -> None:
    if not _exchange:
        return
    try:
        await _exchange.publish(
            aio_pika.Message(
                body=json.dumps({"jti": jti, "exp": exp_epoch}).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key="",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("broker.publish_failed", error=str(exc))


async def close() -> None:
    global connected
    connected = False
    if _connection is not None:
        await _connection.close()
