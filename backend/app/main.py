from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import request_id_middleware, unhandled_exception_middleware
from app.db.session import dispose_engine

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.current()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Safe to log: supabase_url is a public project URL (also shipped to
        # the frontend as NEXT_PUBLIC_SUPABASE_URL). Never log the JWT
        # secret/service role key themselves, only whether they're set. A
        # process that silently resolved a stale/wrong project here (e.g.
        # from a leftover shell-exported env var shadowing .env) is exactly
        # what causes every fresh, valid login to 401 -- surface it at boot
        # instead of only as a support ticket.
        logger.info(
            "Backend startup: supabase_url=%s jwt_secret_configured=%s",
            settings.supabase_url,
            bool(settings.supabase_jwt_secret),
        )
        try:
            yield
        finally:
            dispose_engine(settings)
            logger.info("Backend shutdown")

    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        lifespan=lifespan,
    )
    # Order matters here: app.add_middleware(...) / app.middleware("http")(...)
    # each prepend to Starlette's middleware list, so the LAST one registered
    # ends up OUTERMOST at request time. unhandled_exception_middleware is
    # registered first so it lands *inside* CORSMiddleware -- meaning a 500
    # it builds still passes back out through CORS and gets the
    # Access-Control-Allow-Origin header, instead of bypassing CORS entirely
    # the way a generic @app.exception_handler(Exception) would (see
    # app/core/errors.py). request_id_middleware stays outermost since it
    # never raises, so its position doesn't affect CORS.
    app.middleware("http")(unhandled_exception_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Correlation-ID",
            "Idempotency-Key",
        ],
    )
    app.middleware("http")(request_id_middleware)
    install_error_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
