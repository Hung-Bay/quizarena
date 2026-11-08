"""Schemas Pydantic định nghĩa cấu trúc gói tin WebSocket (v=1) theo WS_PROTOCOL.md."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WSEnvelope(BaseModel):
    """Khung thông điệp chuẩn 2 chiều cho WebSocket."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="Loại thông điệp")
    v: int = Field(default=1, description="Phiên bản giao thức (mặc định 1)")
    data: dict[str, Any] = Field(default_factory=dict, description="Payload riêng của từng type")
    seq: int | None = Field(
        default=None, description="Số thứ tự gói tin để client phát hiện mất tin"
    )


# =========================================================================
# Server -> Client Payloads
# =========================================================================


class PlayerLobbyInfo(BaseModel):
    player_id: uuid.UUID
    nickname: str
    is_host: bool = False
    is_online: bool = True


class LobbyUpdateData(BaseModel):
    game_id: uuid.UUID
    pin: str
    status: str
    players: list[PlayerLobbyInfo]
    player_count: int


class QuestionOptionPublic(BaseModel):
    """Lựa chọn gửi xuống client: TUYỆT ĐỐI KHÔNG chứa is_correct (GM-3)."""

    id: uuid.UUID
    position: int
    content: str


class QuestionStartData(BaseModel):
    """Câu hỏi gửi cho người chơi khi bắt đầu một câu (GM-3, GM-4)."""

    q_index: int
    total_questions: int
    question_id: uuid.UUID
    content: str
    type: str
    points: int
    time_limit_s: int
    server_time: int = Field(..., description="Thời gian server lúc phát (epoch ms)")
    deadline: int = Field(..., description="Thời hạn nhận đáp án (epoch ms)")
    options: list[QuestionOptionPublic]


class AnswerAckData(BaseModel):
    """Phản hồi sau khi nộp đáp án: Chỉ báo đã nhận, chưa báo đúng/sai (GM-5)."""

    accepted: bool
    q_index: int
    reason: str | None = None


class LeaderboardEntry(BaseModel):
    rank: int
    player_id: uuid.UUID
    nickname: str
    score: int
    streak: int = 0


class QuestionResultData(BaseModel):
    """Kết quả sau khi đóng câu hỏi (GM-9)."""

    q_index: int
    correct_option_ids: list[uuid.UUID]
    explanation: str | None = None
    distribution: dict[str, int] = Field(
        default_factory=dict, description="Số lượt chọn cho từng option ID"
    )
    leaderboard: list[LeaderboardEntry] = Field(
        default_factory=list, description="Top 10 người dẫn đầu"
    )
    # Phần cá nhân hóa cho từng player connection
    player_points_awarded: int | None = None
    player_total_score: int | None = None
    player_rank: int | None = None
    player_streak: int | None = None


class GameFinishedData(BaseModel):
    """Kết thúc trận đấu."""

    game_id: uuid.UUID
    podium: list[LeaderboardEntry] = Field(..., description="Top 3 bục vinh danh")
    total_players: int
    # Thông tin riêng cho người chơi
    final_score: int | None = None
    final_rank: int | None = None


class GameSnapshotData(BaseModel):
    """Snapshot gửi cho client khi reconnect giữa trận (GM-10)."""

    game_id: uuid.UUID
    status: str
    q_index: int
    total_questions: int
    current_question: QuestionStartData | None = None
    current_result: QuestionResultData | None = None
    player_score: int = 0
    player_streak: int = 0
    player_rank: int | None = None
    is_host: bool = False


# =========================================================================
# Client -> Server Payloads
# =========================================================================


class AnswerSubmitPayload(BaseModel):
    """Client nộp đáp án."""

    q_index: int
    question_id: uuid.UUID
    selected_option_ids: list[uuid.UUID]
    client_sent_at: int | None = None


class PlayerKickPayload(BaseModel):
    """Host kick người chơi."""

    player_id: uuid.UUID
    reason: str | None = None


class GameTransitionPayload(BaseModel):
    """Host điều khiển trận đấu (game.start, game.next, game.end)."""

    expected_q_index: int | None = Field(
        default=None, description="Chống double-click nhảy cóc câu hỏi"
    )
