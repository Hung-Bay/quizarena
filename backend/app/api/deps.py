"""Dependency dùng chung cho các router."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway_interface import GeminiGatewayProtocol
from app.ai.gemini_gateway import create_gemini_gateway
from app.core import security
from app.core.errors import AppError
from app.core.rbac import PermissionCode
from app.models import User
from app.repositories import user_repository as users


def get_gemini_gateway() -> GeminiGatewayProtocol:
    """Khởi tạo hoặc cung cấp Gemini Gateway Adapter."""
    return create_gemini_gateway()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Mỗi request một AsyncSession. Service tự mở/đóng transaction bằng `session.begin()`."""
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    """Lấy Redis client từ app.state."""
    return request.app.state.redis


def get_client_ip(request: Request) -> str:
    """Lấy IP của client, hỗ trợ X-Forwarded-For hoặc X-Real-IP từ reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "127.0.0.1"


async def get_token_claims(
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """Trích xuất và xác thực claims từ Authorization header (chấp nhận cả access token và guest token)."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise AppError("Thiếu access token", code="TOKEN_MISSING", status_code=401)

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AppError(
            "Header Authorization không đúng định dạng Bearer",
            code="TOKEN_INVALID",
            status_code=401,
        )

    token = parts[1]
    try:
        claims = security.decode_token(token)
    except security.TokenError as exc:
        if exc.reason == "expired":
            raise AppError("Token đã hết hạn", code="TOKEN_EXPIRED", status_code=401) from exc
        raise AppError("Token không hợp lệ", code="TOKEN_INVALID", status_code=401) from exc

    # AU-6: Kiểm tra token có nằm trong denylist Redis không
    jti = claims.get("jti")
    if jti and await redis.exists(f"deny:jti:{jti}"):
        raise AppError("Token đã bị thu hồi", code="TOKEN_REVOKED", status_code=401)

    return claims


async def get_current_user_claims(
    claims: Annotated[dict[str, Any], Depends(get_token_claims)],
) -> dict[str, Any]:
    """Trích xuất và kiểm tra JWT access token (AU-3, AU-6)."""
    if claims.get("type") != "access":
        raise AppError("Yêu cầu access token", code="TOKEN_INVALID", status_code=401)
    return claims


async def get_current_user(
    claims: Annotated[dict[str, Any], Depends(get_current_user_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Lấy User từ access token và kiểm tra token_version (AU-7)."""
    user_id_raw = claims.get("sub")
    if not user_id_raw:
        raise AppError("Token không hợp lệ: thiếu sub", code="TOKEN_INVALID", status_code=401)

    try:
        user_id = uuid.UUID(user_id_raw)
    except ValueError as exc:
        raise AppError(
            "Token không hợp lệ: sub không phải UUID", code="TOKEN_INVALID", status_code=401
        ) from exc

    async with session.begin():
        user = await users.get_by_id(session, user_id)

    if user is None:
        raise AppError("Người dùng không tồn tại", code="USER_NOT_FOUND", status_code=401)
    if not user.is_active:
        raise AppError("Tài khoản đã bị khóa", code="ACCOUNT_DISABLED", status_code=403)

    # AU-7: Kiểm tra token_version (nếu nhỏ hơn token_version hiện tại thì đã bị thu hồi do logout-all hoặc đổi mật khẩu)
    token_ver = claims.get("ver")
    if token_ver is None or token_ver < user.token_version:
        raise AppError(
            "Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại",
            code="TOKEN_REVOKED",
            status_code=401,
        )

    return user


def require_permissions(*required_permissions: PermissionCode | str) -> Callable[..., Any]:
    """Dependency kiểm tra quyền người dùng dựa trên permissions trong token (RBAC §3.2)."""
    required = {str(p) for p in required_permissions}

    async def _require_permissions_dep(
        claims: Annotated[dict[str, Any], Depends(get_token_claims)],
    ) -> dict[str, Any]:
        token_permissions = set(claims.get("permissions", []))
        if not required.issubset(token_permissions):
            raise AppError(
                "Không có quyền thực hiện thao tác này",
                code="FORBIDDEN",
                status_code=403,
            )
        return claims

    return _require_permissions_dep
