"""API xác thực: /auth/register, /auth/login, /auth/refresh."""

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import get_settings
from app.core.errors import AppError, error_response
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest, TokenResponse, UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"  # cookie chỉ được gửi tới các endpoint /auth/*

Session = Annotated[AsyncSession, Depends(get_session)]


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
async def login(body: LoginRequest, response: Response, session: Session) -> LoginResponse:
    result = await auth_service.login(session, email=body.email, password=body.password)
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
