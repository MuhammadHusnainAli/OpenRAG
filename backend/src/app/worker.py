"""Celery worker entrypoint.

Run with:  celery -A app.worker.celery_app worker --loglevel=info --concurrency=4

Importing ``app.services.ingestion`` registers the ingestion task, and Qdrant's
collection is ensured once when the worker process boots.
"""

from __future__ import annotations

from celery.signals import worker_process_init

from app.core.celery_app import celery_app
from app.rag.qdrant import ensure_collection
from app.services import ingestion  # registers ingest_document + owns the worker loop

__all__ = ["celery_app"]


@worker_process_init.connect
def _on_worker_start(**_kwargs) -> None:
    # run on the same persistent loop the ingestion tasks use
    ingestion._run(ensure_collection())
