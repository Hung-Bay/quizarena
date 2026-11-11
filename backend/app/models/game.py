"""Mô hình dữ liệu cho Trận đấu Live Game (game_sessions, game_players, player_answers)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.report import GameReport

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class GameSession(Base):
    """Một phiên trận đấu live (GM-1, GM-14)."""

    __tablename__ = "game_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quizzes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    quiz_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    host_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    pin: Mapped[str] = mapped_column(String(6), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="lobby", index=True)
    strategy_id: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    quiz_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
            "status IN ('lobby', 'question_active', 'question_result', 'finished', 'cancelled')",
            name="ck_game_sessions_status",
        ),
    )

    # Relationships
    players: Mapped[list[GamePlayer]] = relationship(
        "GamePlayer", back_populates="session", cascade="all, delete-orphan"
    )
    answers: Mapped[list[PlayerAnswer]] = relationship(
        "PlayerAnswer", back_populates="session", cascade="all, delete-orphan"
    )
    report: Mapped[GameReport | None] = relationship(
        "GameReport", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )


class GamePlayer(Base):
    """Người chơi tham gia trận đấu (GM-2)."""

    __tablename__ = "game_players"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    guest_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nickname: Mapped[str] = mapped_column(String(50), nullable=False)
    final_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("session_id", "nickname", name="uq_game_players_session_nickname"),
        CheckConstraint(
            "length(nickname) >= 2 AND length(nickname) <= 50",
            name="ck_game_players_nickname_length",
        ),
    )

    # Relationships
    session: Mapped[GameSession] = relationship("GameSession", back_populates="players")
    answers: Mapped[list[PlayerAnswer]] = relationship(
        "PlayerAnswer", back_populates="player", cascade="all, delete-orphan"
    )


class PlayerAnswer(Base):
    """Lưu đáp án từng câu hỏi của người chơi để lưu bền (write-behind, GM-5, GM-12)."""

    __tablename__ = "player_answers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_players.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # QZ-4 / GM-12: Không đặt FK tới bảng questions để khi quiz gốc bị sửa/xóa không ảnh hưởng dữ liệu lịch sử
    question_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    option_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    response_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    points_awarded: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Lớp bảo vệ cuối cùng cho tính idempotent (GM-5) khi worker ghi theo lô
        UniqueConstraint(
            "session_id",
            "player_id",
            "question_id",
            name="uq_player_answers_session_player_question",
        ),
    )

    # Relationships
    session: Mapped[GameSession] = relationship("GameSession", back_populates="answers")
    player: Mapped[GamePlayer] = relationship("GamePlayer", back_populates="answers")
