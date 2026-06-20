"""FastAPI application factory + middleware wiring.

Middleware (outermost → innermost): CORS → Session (OAuth state) → RateLimit →
SecurityHeaders → routes. Domain ``ServiceError``s are mapped to clean JSON so
internals never leak.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import agents, auth, chat, documents, health, me, sessions
from app.config import get_llm_config, get_settings
from app.core import broker, token_cache
from app.core.cache import load_active_revocations
from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger
from app.core.middleware import SecurityHeadersMiddleware
from app.core.rate_limit import RateLimitMiddleware
from app.rag.qdrant import ensure_collection
from app.services.errors import ServiceError

_settings = get_settings()
configure_logging(production=_settings.is_production)
log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_collection()
    # warm the in-memory JWT denylist from Postgres, then start the fanout consumer
    async with session_scope() as db:
        token_cache.bulk_load(await load_active_revocations(db))
    await broker.connect()
    log.info(
        "startup.ready",
        env=_settings.app_env,
        chat_model=get_llm_config().llm.model,
        revocations_loaded=token_cache.size(),
    )
    yield
    await broker.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="OpenRAG",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if _settings.is_production else "/docs",
        redoc_url=None if _settings.is_production else "/redoc",
        openapi_url=None if _settings.is_production else "/openapi.json",
    )

    # innermost first; add CORS last so it ends up outermost
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ServiceError)
    async def _service_error_handler(_request: Request, exc: ServiceError):
        return JSONResponse(
            {"detail": exc.detail, "code": exc.code}, status_code=exc.status_code
        )

    for r in (
        auth.router,
        me.router,
        sessions.router,
        documents.router,
        chat.router,
        agents.router,
        health.router,
    ):
        app.include_router(r)

    return app


app = create_app()
