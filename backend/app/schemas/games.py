"""Schemas cho Game Session, Join Game và Guest Token (GM-1, GM-2, AU-9, AU-10)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateGameRequest(BaseModel):
    quiz_id: uuid.UUID = Field(..., description="ID của Quiz đã xuất bản cần tạo trận")
    strategy_id: str = Field(
        default="standard", description="Chiến lược tính điểm (standard / binary)"
    )


class CreateGameResponse(BaseModel):
    game_id: uuid.UUID
    pin: str
    quiz_id: uuid.UUID
    quiz_title: str
    status: str
    total_questions: int
    strategy_id: str
    created_at: datetime


class GameDetailResponse(BaseModel):
    game_id: uuid.UUID
    pin: str
    status: str
    quiz_id: uuid.UUID
    quiz_title: str
    host_id: uuid.UUID
    total_questions: int
    current_question_index: int
    player_count: int
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class JoinGameRequest(BaseModel):
    pin: str = Field(min_length=6, max_length=6, description="Mã PIN 6 ký tự số của trận đấu")
    nickname: str = Field(
        min_length=2, max_length=20, description="Biệt danh trong trận (2-20 ký tự)"
    )


class JoinGameResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    game_id: uuid.UUID
    player_id: uuid.UUID
    nickname: str
    type: Literal["guest", "player"]


class GameReportResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    status: str
    stats: dict[str, Any]
    summary: str
    strengths: list[dict[str, Any]]
    weaknesses: list[dict[str, Any]]
    advice_for_host: str
    advice_for_learner: str
    personal_feedback: dict[str, Any] | None = None
    created_at: datetime


class GameListItemResponse(BaseModel):
    game_id: uuid.UUID
    pin: str
    status: str
    quiz_id: uuid.UUID
    quiz_title: str
    player_count: int
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class UserGameHistoryItemResponse(BaseModel):
    game_id: uuid.UUID
    quiz_title: str
    nickname: str
    final_score: int
    final_rank: int | None = None
    ended_at: datetime | None = None
