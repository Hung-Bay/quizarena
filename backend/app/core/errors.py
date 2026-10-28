"""Chuẩn lỗi thống nhất: {"error": {"code", "message", "request_id"}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id

log = logging.getLogger("app.errors")

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
}


class AppError(Exception):
    """Lỗi nghiệp vụ. Ví dụ: raise AppError("Trận không tồn tại", code="GAME_NOT_FOUND", status_code=404)."""

    def __init__(self, message: str, *, code: str = "BAD_REQUEST", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    request_id = get_request_id()
    body: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details:
        body["details"] = details
    out_headers = dict(headers or {})
    if request_id:
        # Lỗi 500 do Starlette tạo ra nằm NGOÀI middleware nên phải tự thêm header.
        out_headers["X-Request-ID"] = request_id
    return JSONResponse({"error": body}, status_code=status_code, headers=out_headers)


async def _app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(exc.status_code, exc.code, exc.message)


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    message = exc.detail if isinstance(exc.detail, str) else code
    headers = dict(exc.headers) if exc.headers else None
    return error_response(exc.status_code, code, message, headers=headers)


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()
    ]
    return error_response(422, "VALIDATION_ERROR", "Dữ liệu không hợp lệ", details=details)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Ghi đầy đủ traceback vào log (kèm request_id) nhưng KHÔNG trả chi tiết cho client.
    log.error("unhandled exception", exc_info=exc)
    return error_response(500, "INTERNAL_ERROR", "Lỗi hệ thống, vui lòng thử lại sau")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
