"""API xác thực: /auth/register, /auth/login, /auth/refresh, /auth/logout, /auth/logout-all, /auth/change-password, /auth/me."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip, get_current_user, get_redis, get_session
from app.core import security
from app.core.config import get_settings
from app.core.errors import AppError, error_response
from app.models import User
from app.repositories import user_repository as users
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"  # cookie chỉ được gửi tới các endpoint /auth/*

ADMIN_SESSION_COOKIE = "admin_session"
ADMIN_SESSION_PATH = "/"  # cookie gửi tới gateway (bao gồm /grafana/ và /internal/auth/verify)

Session = Annotated[AsyncSession, Depends(get_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=get_settings().refresh_token_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,  # JavaScript không đọc được → XSS không lấy được refresh token
        secure=True,  # chỉ gửi qua HTTPS (trình duyệt vẫn chấp nhận trên localhost)
        samesite="strict",  # không gửi kèm request từ trang khác → chống CSRF
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )


def _set_admin_cookie(response: Response, token: str) -> None:
    """Cấp cookie phiên cho Gateway để truy cập Grafana qua auth_request."""
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=get_settings().access_token_minutes * 60,
        path=ADMIN_SESSION_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def _clear_admin_cookie(response: Response) -> None:
    """Xóa cookie phiên Gateway của Admin khi logout."""
    response.delete_cookie(
        ADMIN_SESSION_COOKIE,
        path=ADMIN_SESSION_PATH,
        httponly=True,
        secure=True,
        samesite="lax",
    )


@router.post("/register", status_code=201, response_model=UserOut)
async def register(body: RegisterRequest, session: Session) -> UserOut:
    return await auth_service.register(
        session, email=body.email, password=body.password, display_name=body.display_name
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    session: Session,
    redis: RedisClient,
) -> LoginResponse:
    client_ip = get_client_ip(request)
    result = await auth_service.login(
        session, redis, email=body.email, password=body.password, client_ip=client_ip
    )
    _set_refresh_cookie(response, result.tokens.refresh_token)
    user_roles = [str(r).upper() for r in result.user.roles]
    if "ADMIN" in user_roles:
        _set_admin_cookie(response, result.tokens.access_token)
    elif request.cookies.get(ADMIN_SESSION_COOKIE):
        _clear_admin_cookie(response)
    return LoginResponse(
        access_token=result.tokens.access_token,
        expires_in=result.tokens.expires_in,
        user=result.user,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    session: Session,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> Any:
    try:
        if not refresh_token:
            raise AppError("Thiếu refresh token", code="REFRESH_TOKEN_INVALID", status_code=401)
        issued = await auth_service.refresh(session, raw_token=refresh_token)
    except AppError as exc:
        # Refresh thất bại → xóa cookie để trình duyệt không gửi lại token hỏng
        error: JSONResponse = error_response(exc.status_code, exc.code, exc.message)
        _clear_refresh_cookie(error)
        return error

    _set_refresh_cookie(response, issued.refresh_token)
    return TokenResponse(access_token=issued.access_token, expires_in=issued.expires_in)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    session: Session,
    redis: RedisClient,
    refresh_token: Annotated[str | None, Cookie()] = None,
    admin_session: Annotated[str | None, Cookie()] = None,
) -> MessageResponse:
    auth_header = request.headers.get("Authorization")
    access_token: str | None = None
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            access_token = parts[1]

    await auth_service.logout(
        session, redis, access_token=access_token, refresh_token=refresh_token
    )
    _clear_refresh_cookie(response)
    if admin_session or request.cookies.get(ADMIN_SESSION_COOKIE):
        _clear_admin_cookie(response)
    elif access_token:
        try:
            claims = security.decode_token(access_token)
            if "ADMIN" in [str(r).upper() for r in claims.get("roles", [])]:
                _clear_admin_cookie(response)
        except Exception:
            pass
    return MessageResponse(message="Đăng xuất thành công")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    request: Request,
    response: Response,
    session: Session,
    current_user: Annotated[User, Depends(get_current_user)],
) -> MessageResponse:
    await auth_service.logout_all(session, user_id=current_user.id)
    _clear_refresh_cookie(response)
    user_roles = [str(r).upper() for r in getattr(current_user, "roles", [])]
    if "ADMIN" in user_roles or request.cookies.get(ADMIN_SESSION_COOKIE):
        _clear_admin_cookie(response)
    return MessageResponse(message="Đã đăng xuất khỏi tất cả thiết bị")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    response: Response,
    session: Session,
    current_user: Annotated[User, Depends(get_current_user)],
) -> MessageResponse:
    await auth_service.change_password(
        session,
        user=current_user,
        old_password=body.old_password,
        new_password=body.new_password,
    )
    _clear_refresh_cookie(response)
    user_roles = [str(r).upper() for r in getattr(current_user, "roles", [])]
    if "ADMIN" in user_roles or request.cookies.get(ADMIN_SESSION_COOKIE):
        _clear_admin_cookie(response)
    return MessageResponse(message="Đổi mật khẩu thành công")


@router.get("/me", response_model=UserOut)
async def me(
    session: Session,
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserOut:
    roles = await users.get_roles(session, current_user.id)
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        roles=roles,
    )


@router.get("/verify-admin")
@router.get("/verify")
async def verify_admin(
    request: Request,
    session: Session,
    redis: RedisClient,
) -> Response:
    """Xác thực quyền ADMIN phục vụ nginx auth_request bảo vệ Grafana (§7.4, NF-9).

    Hỗ trợ cả hai phương thức xác thực:
    1. Header Authorization: Bearer <access_token>
    2. Cookie admin_session: <access_token>

    Mã phản hồi:
    - 200 OK: Hợp lệ và có vai trò ADMIN (trả về các header X-User-*).
    - 401 Unauthorized: Thiếu token, token không hợp lệ, token đã hết hạn hoặc bị thu hồi.
    - 403 Forbidden: Đã xác thực nhưng người dùng không có vai trò ADMIN.
    """
    token: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        parts = auth_header.split()
        if len(parts) == 2:
            token = parts[1]
    elif request.cookies.get(ADMIN_SESSION_COOKIE):
        token = request.cookies.get(ADMIN_SESSION_COOKIE)

    if not token:
        return JSONResponse(
            status_code=401,
            content={
                "error": {"code": "UNAUTHORIZED", "message": "Yêu cầu đăng nhập tài khoản ADMIN"}
            },
        )

    try:
        claims = security.decode_token(token, expected_type="access")
    except security.TokenError:
        return JSONResponse(
            status_code=401,
            content={
                "error": {"code": "TOKEN_INVALID", "message": "Token không hợp lệ hoặc đã hết hạn"}
            },
        )

    jti = claims.get("jti")
    if jti and await redis.exists(f"deny:jti:{jti}"):
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "TOKEN_REVOKED", "message": "Token đã bị thu hồi"}},
        )

    user_id_raw = claims.get("sub")
    if not user_id_raw:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "TOKEN_INVALID", "message": "Thiếu thông tin người dùng"}},
        )

    try:
        user_id = uuid.UUID(user_id_raw)
    except ValueError:
        return JSONResponse(
            status_code=401,
            content={
                "error": {"code": "TOKEN_INVALID", "message": "Định danh người dùng không hợp lệ"}
            },
        )

    async with session.begin():
        user = await users.get_by_id(session, user_id)
        if user is None:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {"code": "USER_NOT_FOUND", "message": "Người dùng không tồn tại"}
                },
            )
        if not user.is_active:
            return JSONResponse(
                status_code=403,
                content={"error": {"code": "ACCOUNT_DISABLED", "message": "Tài khoản đã bị khóa"}},
            )

        token_ver = claims.get("ver")
        if token_ver is None or token_ver < user.token_version:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {"code": "TOKEN_REVOKED", "message": "Phiên đăng nhập đã bị hủy"}
                },
            )

        roles = await users.get_roles(session, user_id)
        normalized_roles = [str(r).upper() for r in roles]
        if "ADMIN" not in normalized_roles:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "Tài khoản không có quyền ADMIN để truy cập Grafana",
                    }
                },
            )

    return Response(
        status_code=200,
        headers={
            "X-User-Id": str(user.id),
            "X-User-Email": user.email,
            "X-User-Role": "ADMIN",
        },
    )
