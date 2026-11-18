import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.request_id import RequestIDMiddleware

log = logging.getLogger("app.core.app_factory")


def init_sentry() -> None:
    settings = get_settings()
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn.get_secret_value(),
            environment=settings.app_env,
            release=f"quizarena@{settings.commit_sha}",
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
        )
        log.info(
            "Sentry/GlitchTip initialized for env=%s, release=%s",
            settings.app_env,
            settings.commit_sha,
        )


def create_app(
    title: str,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    init_sentry()
    setup_logging(get_settings().log_level)
    app = FastAPI(
        title=title,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
    )

    # add_middleware: cái thêm SAU CÙNG nằm NGOÀI CÙNG. Thứ tự mục tiêu (ngoài → trong):
    #   Sentry/GlitchTip → RequestID → Log → CORS → Metrics → Exception
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIDMiddleware)

    register_error_handlers(app)
    return app
