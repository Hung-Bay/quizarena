"""API xác thực: /auth/register, /auth/login, /auth/refresh, /auth/logout, /auth/logout-all, /auth/change-password, /auth/me."""

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_client_ip, get_current_user, get_redis, get_session
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
    return MessageResponse(message="Đăng xuất thành công")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    response: Response,
    session: Session,
    current_user: Annotated[User, Depends(get_current_user)],
) -> MessageResponse:
    await auth_service.logout_all(session, user_id=current_user.id)
    _clear_refresh_cookie(response)
    return MessageResponse(message="Đã đăng xuất khỏi tất cả thiết bị")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
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
