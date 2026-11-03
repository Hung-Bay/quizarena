"""Dữ liệu vào/ra của các API xác thực."""

import re
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

_PASSWORD_MAX = 128  # chặn mật khẩu khổng lồ: argon2 tốn tài nguyên theo độ dài


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("password")
    @classmethod
    def _check_password_policy(cls, value: str) -> str:
        if not (8 <= len(value) <= _PASSWORD_MAX):
            raise ValueError(f"Mật khẩu phải dài từ 8 đến {_PASSWORD_MAX} ký tự")
        if not (re.search(r"[A-Za-z]", value) and re.search(r"\d", value)):
            raise ValueError("Mật khẩu phải có cả chữ và số")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    # Đăng nhập KHÔNG áp luật độ mạnh (tài khoản cũ có thể khác luật), chỉ chặn độ dài quá lớn.
    password: str = Field(min_length=1, max_length=_PASSWORD_MAX)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    roles: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # giây


class LoginResponse(TokenResponse):
    user: UserOut


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=_PASSWORD_MAX)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password_policy(cls, value: str) -> str:
        if not (8 <= len(value) <= _PASSWORD_MAX):
            raise ValueError(f"Mật khẩu phải dài từ 8 đến {_PASSWORD_MAX} ký tự")
        if not (re.search(r"[A-Za-z]", value) and re.search(r"\d", value)):
            raise ValueError("Mật khẩu phải có cả chữ và số")
        return value


class MessageResponse(BaseModel):
    message: str
