"""Pydantic schemas cho Documents và AI Jobs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AiJobParams(BaseModel):
    """Tham số yêu cầu sinh quiz từ AI (FR-AIGEN-02)."""

    num_questions: int = Field(default=5, ge=5, le=20, description="Số lượng câu hỏi (5-20)")
    difficulty: Literal["easy", "medium", "hard"] = Field(
        default="medium", description="Độ khó câu hỏi"
    )
    language: Literal["vi", "en"] = Field(default="vi", description="Ngôn ngữ sinh quiz")
    question_types: list[Literal["single", "multiple", "true_false"]] = Field(
        default=["single"], description="Các loại câu hỏi được phép sinh"
    )
    notes: str | None = Field(
        default=None, max_length=300, description="Ghi chú thêm cho AI (tối đa 300 ký tự)"
    )


class AiJobEnqueueResponse(BaseModel):
    """Phản hồi sau khi tiếp nhận yêu cầu sinh quiz (HTTP 202 Accepted)."""

    job_id: uuid.UUID
    status: str = "queued"
    document_id: uuid.UUID
    message: str = "Tác vụ sinh quiz đã được đưa vào hàng đợi xử lý"


class AiJobDetailResponse(BaseModel):
    """Chi tiết trạng thái tác vụ nền AI."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    created_by: uuid.UUID
    document_id: uuid.UUID | None = None
    params: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 0
    cache_hit: bool = False
    created_at: datetime
    finished_at: datetime | None = None


class AiJobEventData(BaseModel):
    """Payload sự kiện SSE tiến độ AI Job."""

    job_id: str
    status: str
    quiz_id: str | None = None
    error: str | None = None
    message: str | None = None
