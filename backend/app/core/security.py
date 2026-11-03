"""Băm mật khẩu (argon2id), JWT (HS256) và refresh token ngẫu nhiên."""

import asyncio
import hashlib
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings
from app.core.rbac import PermissionCode, RoleName

ALGORITHM = "HS256"
GUEST_TOKEN_TTL = timedelta(hours=3)  # AU-9: guest token sống tối đa 3 giờ
TokenType = Literal["access", "guest"]

# --------------------------------------------------------------------------- mật khẩu

# Tham số mặc định của argon2-cffi: argon2id, theo khuyến nghị RFC 9106. Mỗi lần băm có salt riêng.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):  # sai mật khẩu hoặc chuỗi băm hỏng
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True nếu bản băm dùng tham số cũ (sau khi nâng cấp cấu hình) → nên băm lại khi đăng nhập."""
    return _hasher.check_needs_rehash(password_hash)


@lru_cache
def _dummy_hash() -> str:
    return _hasher.hash("mat-khau-gia-khong-ai-dung")


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """Đăng nhập: nếu email không tồn tại (`password_hash is None`) vẫn tốn thời gian băm như bình thường,
    để kẻ tấn công không phân biệt được "email không có" với "sai mật khẩu" qua thời gian phản hồi (AU-8)."""
    if password_hash is None:
        verify_password(password, _dummy_hash())
        return False
    return verify_password(password, password_hash)


# Băm là việc nặng CPU (~vài chục ms): chạy trong thread để không chặn event loop của FastAPI.
async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_or_dummy_async(password: str, password_hash: str | None) -> bool:
    return await asyncio.to_thread(verify_password_or_dummy, password, password_hash)


# --------------------------------------------------------------------------- JWT


class TokenError(Exception):
    """Token không dùng được. `reason`: "expired" (hết hạn) hoặc "invalid" (sai chữ ký, sai loại, thiếu trường…)."""

    def __init__(self, reason: Literal["expired", "invalid"], message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _secret() -> str:
    return get_settings().jwt_secret.get_secret_value()


def _encode(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM)


def create_access_token(
    *,
    user_id: uuid.UUID | str,
    roles: Sequence[RoleName | str],
    permissions: Sequence[PermissionCode | str],
    token_version: int,
    now: datetime | None = None,
) -> str:
    """Access token (AU-3). `now` chỉ để test dựng token ở quá khứ, khỏi phải chờ hết hạn."""
    now = now or datetime.now(UTC)
    return _encode(
        {
            "sub": str(user_id),
            "roles": [str(r) for r in roles],
            "permissions": [str(p) for p in permissions],
            "iat": now,
            "exp": now + timedelta(minutes=get_settings().access_token_minutes),
            "jti": uuid.uuid4().hex,
            "ver": token_version,
            "type": "access",
        }
    )


def create_guest_token(
    *, game_id: uuid.UUID | str, guest_id: uuid.UUID | None = None, now: datetime | None = None
) -> str:
    """Guest token (AU-9): chỉ có quyền `game:play`, gắn với đúng một trận, tối đa 3 giờ, không có refresh."""
    now = now or datetime.now(UTC)
    return _encode(
        {
            "sub": f"guest:{guest_id or uuid.uuid4()}",
            "roles": [RoleName.GUEST.value],
            "permissions": [PermissionCode.GAME_PLAY.value],
            "game_id": str(game_id),
            "iat": now,
            "exp": now + GUEST_TOKEN_TTL,
            "jti": uuid.uuid4().hex,
            "type": "guest",
        }
    )


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Giải mã và kiểm tra token. Luôn chỉ định thuật toán (chống tấn công hoán đổi thuật toán / alg=none)
    và kiểm tra `type` để guest token không thể dùng như access token (và ngược lại)."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            _secret(),
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat", "sub", "jti", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("expired", "Token đã hết hạn") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid", "Token không hợp lệ") from exc

    if payload["type"] != expected_type:
        raise TokenError("invalid", "Sai loại token")
    if expected_type == "guest" and not payload.get("game_id"):
        raise TokenError("invalid", "Guest token thiếu game_id")
    return payload


# --------------------------------------------------------------------------- refresh token


def generate_refresh_token() -> str:
    """Chuỗi ngẫu nhiên không đoán được. Gửi cho client; trong DB chỉ lưu bản băm."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """SHA-256 (hex). Dùng cho refresh token: chuỗi ngẫu nhiên entropy cao nên không cần argon2 chậm."""
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_token_expires_at(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(days=get_settings().refresh_token_days)
