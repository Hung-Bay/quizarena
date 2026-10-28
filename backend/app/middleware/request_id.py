"""Gắn request_id cho mỗi request (HTTP và WebSocket)."""

import re
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_ctx

# nginx sinh $request_id gồm 32 ký tự hex. Chỉ nhận chuỗi an toàn, tránh bị chèn ký tự lạ vào log.
_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
HEADER = "X-Request-ID"


class RequestIDMiddleware:
    """Middleware ASGI thuần (không dùng BaseHTTPMiddleware) để contextvar và WebSocket hoạt động đúng."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(HEADER)
        request_id = incoming if incoming and _VALID.match(incoming) else uuid.uuid4().hex
        # Không reset ở cuối: handler lỗi 500 của Starlette chạy SAU middleware này, vẫn cần giá trị.
        # An toàn vì uvicorn xử lý mỗi request trong một task riêng (contextvar không rò sang request khác).
        request_id_ctx.set(request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_header)
