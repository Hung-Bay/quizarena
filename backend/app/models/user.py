import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    # citext: so sánh không phân biệt hoa thường → UNIQUE chặn "A@x.com" và "a@x.com" (FR-AUTH-01)
    email: Mapped[str] = mapped_column(CITEXT(), unique=True)
    password_hash: Mapped[str]
    display_name: Mapped[str]
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    token_version: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
