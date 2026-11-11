"""Mô hình dữ liệu cho Documents và AiJobs.

Tuân thủ ERD §4.2 và các ràng buộc nghiệp vụ FR-AIGEN-01...13:
- documents: tài liệu tải lên (PDF, TXT, MD), lưu trữ sha256, số trang, kích thước, đường dẫn.
- ai_jobs: theo dõi tiến trình sinh quiz từ tài liệu qua ARQ worker, lưu tokens usage, trạng thái và kết quả.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class Document(Base):
    """Bảng lưu trữ tài liệu người dùng tải lên phục vụ AI xử lý."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )

    uploader: Mapped[User] = relationship("User", lazy="select")
    ai_jobs: Mapped[list[AiJob]] = relationship(
        "AiJob",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class AiJob(Base):
    """Bảng lưu trữ tác vụ nền AI (quiz generation, post-game report, v.v.)."""

    __tablename__ = "ai_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    kind: Mapped[str] = mapped_column(
        String(32),
        default="quiz_generation",
        server_default=text("'quiz_generation'"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="queued",
        server_default=text("'queued'"),
        nullable=False,
        index=True,
    )  # queued, running, succeeded, failed
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        server_default=text("'{}'::jsonb"),
        nullable=False,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    cache_hit: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (UniqueConstraint("session_id", "kind", name="uq_ai_jobs_session_kind"),)

    creator: Mapped[User] = relationship("User", lazy="select")
    document: Mapped[Document | None] = relationship(
        "Document",
        back_populates="ai_jobs",
        lazy="select",
    )
