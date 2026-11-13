"""Mô hình dữ liệu cho Quizzes, Questions và QuestionOptions.

Tuân thủ ERD §4.2 và các ràng buộc nghiệp vụ QZ-1...QZ-3:
- quizzes: quản lý bộ câu hỏi, trạng thái draft/published, visibility, version, tags, description.
- questions: câu hỏi thuộc quiz, loại câu (single, multiple, true_false), thứ tự, giới hạn thời gian, điểm, source_ref.
- question_options: các phương án lựa chọn, thứ tự position, cờ is_correct.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class Quiz(Base):
    """Bảng lưu trữ thông tin bộ câu hỏi (quizzes)."""

    __tablename__ = "quizzes"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(32), server_default=text("'private'"), nullable=False
    )  # private / unlisted / public
    status: Mapped[str] = mapped_column(
        String(32), server_default=text("'draft'"), nullable=False, index=True
    )  # draft / published / archived
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("ARRAY[]::varchar[]"), nullable=False
    )
    play_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False, index=True
    )
    hidden_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner: Mapped[User] = relationship("User", back_populates="quizzes")
    questions: Mapped[list[Question]] = relationship(
        "Question",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="Question.position",
    )


class Question(Base):
    """Bảng lưu trữ từng câu hỏi thuộc Quiz."""

    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    quiz_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)  # single / multiple / true_false
    content: Mapped[str] = mapped_column(Text, nullable=False)
    time_limit_s: Mapped[int] = mapped_column(
        Integer, server_default=text("20"), nullable=False
    )  # 5 - 60s
    points: Mapped[int] = mapped_column(
        Integer, server_default=text("1000"), nullable=False
    )  # 500 / 1000 / 2000
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_generated: Mapped[bool] = mapped_column(server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )

    quiz: Mapped[Quiz] = relationship("Quiz", back_populates="questions")
    options: Mapped[list[QuestionOption]] = relationship(
        "QuestionOption",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionOption.position",
    )


class QuestionOption(Base):
    """Bảng lưu trữ các phương án lựa chọn của một câu hỏi."""

    __tablename__ = "question_options"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(server_default=text("false"), nullable=False)

    question: Mapped[Question] = relationship("Question", back_populates="options")
