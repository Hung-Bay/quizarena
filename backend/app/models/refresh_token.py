"""Refresh token (AU-4, AU-5). Trong DB chỉ lưu bản băm SHA-256, không lưu token gốc."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Mọi token sinh ra từ cùng một lần đăng nhập có chung family_id → thu hồi cả "họ" khi phát hiện dùng lại.
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 dạng hex = 64 ký tự
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Có giá trị = token đã bị xoay vòng hoặc thu hồi. Token đã thu hồi mà bị gửi lại = dấu hiệu bị đánh cắp.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
