"""Mô hình dữ liệu cho Báo cáo AI sau trận đấu (game_reports - FR-AIRPT-01..06)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.game import GameSession


class GameReport(Base):
    """Bảng lưu trữ báo cáo phân tích AI sau khi trận đấu kết thúc (FR-AIRPT-01).

    Chứa:
    - stats: Thống kê tổng quan (tỉ lệ đúng, phân bổ đáp án, mastery theo chủ đề, câu khó/dễ nhất).
    - summary: Nhận xét tổng quan tình hình lớp học.
    - strengths: Các chủ đề lớp nắm vững (mastery >= 80%).
    - weaknesses: Các chủ đề lớp còn yếu (mastery < 50%).
    - advice_for_host: Chỉ dẫn sư phạm cho giảng viên/host.
    - advice_for_learner: Khuyến nghị học tập chung cho người học.
    - status: 'ready' (đầy đủ AI) hoặc 'degraded' (Gemini lỗi, chỉ có thống kê cứng).
    """

    __tablename__ = "game_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ready",
        index=True,
    )  # 'ready', 'degraded', 'failed'
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    strengths: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    weaknesses: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    advice_for_host: Mapped[str] = mapped_column(Text, nullable=False, default="")
    advice_for_learner: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ready', 'degraded', 'failed')",
            name="ck_game_reports_status",
        ),
    )

    # Relationships
    session: Mapped[GameSession] = relationship("GameSession", back_populates="report")
