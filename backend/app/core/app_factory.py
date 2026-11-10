from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.request_id import RequestIDMiddleware


def create_app(
    title: str,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    setup_logging(get_settings().log_level)
    app = FastAPI(title=title, lifespan=lifespan)

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
