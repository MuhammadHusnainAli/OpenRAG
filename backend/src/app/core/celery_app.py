"""Celery application configured against RabbitMQ (the Redis replacement broker).

Task results are ignored — ingestion progress is tracked in the ``documents``
table (status column), not in a result backend, so no extra store is needed.
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "openrag",
    broker=_settings.rabbitmq_url,
    backend=None,
    include=["app.services.ingestion"],
)

celery_app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,                 # redeliver if a worker dies mid-task
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # fair dispatch for long ingestion jobs
    task_default_queue="ingestion",
    task_time_limit=600,                 # hard 10-min ceiling per document
    task_soft_time_limit=540,
    broker_connection_retry_on_startup=True,
)
