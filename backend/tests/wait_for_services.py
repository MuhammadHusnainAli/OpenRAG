"""Block until Postgres, RabbitMQ, and Qdrant accept TCP connections.

Used by the Docker test harness so pytest doesn't start before the services are
reachable. Hosts/ports are parsed from the same env the app uses.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from urllib.parse import urlsplit

TIMEOUT_S = 90
INTERVAL_S = 1.5


def _hostport(url: str, default_port: int) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "localhost", parts.port or default_port


def _wait(name: str, host: str, port: int, deadline: float) -> None:
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                print(f"[wait] {name} reachable at {host}:{port}")
                return
        except OSError:
            time.sleep(INTERVAL_S)
    print(f"[wait] TIMEOUT waiting for {name} at {host}:{port}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    targets = [
        ("postgres", *_hostport(os.environ.get("DATABASE_URL", ""), 5432)),
        ("rabbitmq", *_hostport(os.environ.get("RABBITMQ_URL", ""), 5672)),
        ("qdrant", *_hostport(os.environ.get("QDRANT_URL", "http://qdrant:6333"), 6333)),
    ]
    deadline = time.time() + TIMEOUT_S
    for name, host, port in targets:
        _wait(name, host, port, deadline)
    print("[wait] all services reachable")


if __name__ == "__main__":
    main()
