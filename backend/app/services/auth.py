"""Nghiệp vụ xác thực: đăng ký, đăng nhập, làm mới token (FR-AUTH-01…05)."""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rbac import RoleName
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password_async,
    hash_token,
    refresh_token_expires_at,
    verify_password_or_dummy_async,
)
from app.models import User
from app.repositories import refresh_token_repository as tokens
from app.repositories import user_repository as users
from app.schemas.auth import UserOut

log = logging.getLogger("app.auth")


@dataclass(frozen=True)
class AuthTokens:
    access_token: str
    refresh_token: str  # chuỗi gốc, chỉ gửi cho client qua cookie; DB chỉ lưu bản băm
    expires_in: int


@dataclass(frozen=True)
class LoginResult:
    user: UserOut
    tokens: AuthTokens


def _invalid_credentials() -> AppError:
    # Cùng một thông điệp cho "email không tồn tại" và "sai mật khẩu" (AU-8: không lộ email có tồn tại)
    return AppError("Email hoặc mật khẩu không đúng", code="INVALID_CREDENTIALS", status_code=401)


async def _issue_tokens(
    session: AsyncSession, user: User, *, family_id: uuid.UUID
) -> tuple[AuthTokens, list[str]]:
    """Tạo access token + refresh token mới và lưu bản băm của refresh token. Gọi TRONG một transaction."""
    roles = await users.get_roles(session, user.id)
    permissions = await users.get_permissions(session, user.id)
    access = create_access_token(
        user_id=user.id, roles=roles, permissions=permissions, token_version=user.token_version
    )
    raw_refresh = generate_refresh_token()
    await tokens.create(
        session,
        user_id=user.id,
        family_id=family_id,
        token_hash=hash_token(raw_refresh),
        expires_at=refresh_token_expires_at(),
    )
    expires_in = get_settings().access_token_minutes * 60
    return AuthTokens(access, raw_refresh, expires_in), roles


# ----------------------------------------------------------------------------- đăng ký (AU-1)


async def register(
    session: AsyncSession, *, email: str, password: str, display_name: str | None
) -> UserOut:
    email = email.strip().lower()
    display_name = display_name or email.split("@")[0]
    password_hash = await hash_password_async(password)  # băm TRƯỚC khi mở transaction

    try:
        async with session.begin():
            role_id = await users.role_id_by_name(session, RoleName.PLAYER.value)
            if role_id is None:
                raise AppError(
                    "Hệ thống chưa nạp dữ liệu vai trò (chạy `python -m app.cli seed`)",
                    code="ROLES_NOT_SEEDED",
                    status_code=500,
                )
            user = await users.create(
                session, email=email, password_hash=password_hash, display_name=display_name
            )
            await users.add_role(session, user.id, role_id)
    except IntegrityError as exc:
        if "uq_users_email" in str(exc.orig):
            # Bắt lỗi ở DB (không SELECT trước rồi INSERT) để hai request đăng ký cùng lúc vẫn chỉ một thành công
            raise AppError(
                "Email đã được đăng ký", code="EMAIL_ALREADY_EXISTS", status_code=409
            ) from exc
        raise

    return UserOut(
        id=user.id, email=user.email, display_name=user.display_name, roles=[RoleName.PLAYER.value]
    )


# ----------------------------------------------------------------------------- đăng nhập (AU-2, AU-3)


async def login(session: AsyncSession, *, email: str, password: str) -> LoginResult:
    async with session.begin():
        user = await users.get_by_email(session, email.strip().lower())
    # Băm/kiểm tra mật khẩu NGOÀI transaction để không giữ kết nối DB trong lúc tính toán nặng.
    ok = await verify_password_or_dummy_async(password, user.password_hash if user else None)
    if user is None or not ok:
        raise _invalid_credentials()
    if not user.is_active:
        # Chỉ nói "tài khoản bị khóa" khi đã đúng mật khẩu, nên không lộ thông tin cho người lạ
        raise AppError("Tài khoản đã bị khóa", code="ACCOUNT_DISABLED", status_code=403)

    async with session.begin():
        issued, roles = await _issue_tokens(session, user, family_id=uuid.uuid4())

    return LoginResult(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name, roles=roles),
        tokens=issued,
    )


# ----------------------------------------------------------------------------- làm mới (AU-4, AU-5)


async def refresh(session: AsyncSession, *, raw_token: str) -> AuthTokens:
    token_hash = hash_token(raw_token)
    failure: AppError | None = None

    async with session.begin():
        rotated = await tokens.revoke_if_active(session, token_hash)

        if rotated is not None:
            user = await users.get_by_id(session, rotated.user_id)
            if user is not None and user.is_active:
                issued, _ = await _issue_tokens(session, user, family_id=rotated.family_id)
                return issued  # thoát khỏi `with` bình thường → transaction được commit
            await tokens.revoke_family(session, rotated.family_id)
            failure = AppError("Tài khoản đã bị khóa", code="ACCOUNT_DISABLED", status_code=403)
        else:
            existing = await tokens.find_by_hash(session, token_hash)
            if existing is not None and existing.revoked_at is not None:
                # AU-5: token ĐÃ bị xoay vòng/thu hồi mà vẫn được gửi lại → nghi bị đánh cắp
                revoked = await tokens.revoke_family(session, existing.family_id)
                log.warning(
                    "refresh token reuse detected",
                    extra={
                        "user_id": str(existing.user_id),
                        "family_id": str(existing.family_id),
                        "revoked_count": revoked,
                    },
                )
                failure = AppError(
                    "Phiên đăng nhập không an toàn, hãy đăng nhập lại",
                    code="REFRESH_TOKEN_REUSED",
                    status_code=401,
                )
            else:
                # không tồn tại, hoặc còn nguyên nhưng đã hết hạn
                failure = AppError(
                    "Refresh token không hợp lệ hoặc đã hết hạn",
                    code="REFRESH_TOKEN_INVALID",
                    status_code=401,
                )

    # Chỉ ném lỗi SAU khi transaction đã commit: nếu ném bên trong `with`, việc thu hồi family sẽ bị rollback.
    assert failure is not None
    raise failure
