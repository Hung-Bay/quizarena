"""Ghi một dòng log JSON cho mỗi request HTTP: method, path, status, thời gian xử lý."""

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("app.access")

# Health check chạy mỗi vài giây trên mọi replica → chỉ ghi khi lỗi, tránh ngập log.
_QUIET_PATHS = {"/healthz", "/api/v1/healthz"}


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status = 500  # nếu có ngoại lệ thoát ra trước khi có response

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # không ghi query string (có thể chứa ticket, PIN)
            path = scope["path"]
            if path not in _QUIET_PATHS or status >= 400:
                log.info(
                    "request",
                    extra={
                        "method": scope["method"],
                        "path": path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    },
                )
