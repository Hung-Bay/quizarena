"""Ghi một dòng log JSON cho mỗi request HTTP: method, path, status, thời gian xử lý và đo HTTP metrics."""

import logging
import re
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import HTTP_REQUEST_DURATION_SECONDS

log = logging.getLogger("app.access")

# Health check chạy mỗi vài giây trên mọi replica → chỉ ghi khi lỗi, tránh ngập log.
_QUIET_PATHS = {"/healthz", "/api/v1/healthz", "/metrics", "/api/v1/metrics"}

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_NUM_RE = re.compile(r"/\d+(?=/|$)")


def _normalize_path(path: str) -> str:
    """Chuẩn hóa đường dẫn để tránh bùng nổ cardinality nhãn trong Prometheus."""
    p = _UUID_RE.sub(":id", path)
    return _NUM_RE.sub("/:id", p)


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
            elapsed = time.perf_counter() - start
            # không ghi query string (có thể chứa ticket, PIN)
            path = scope["path"]
            normalized = _normalize_path(path)
            HTTP_REQUEST_DURATION_SECONDS.labels(
                method=scope["method"],
                path=normalized,
                status=str(status),
            ).observe(elapsed)

            if path not in _QUIET_PATHS or status >= 400:
                log.info(
                    "request",
                    extra={
                        "method": scope["method"],
                        "path": path,
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )
