# ── OpenRAG backend image (also runs the Celery worker) ─────────────────────
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# build tooling + libmagic (python-magic) — kept minimal
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# install deps first for better layer caching
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e .

# upload volume mount point, owned by the unprivileged user
RUN useradd -m appuser && mkdir -p /data/uploads && chown -R appuser /app /data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
