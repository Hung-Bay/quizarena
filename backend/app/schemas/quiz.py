"""Pydantic schemas cho Quiz và Question (REST API request/response)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QuestionOptionCreate(BaseModel):
    """Schema tạo phương án lựa chọn."""

    content: str = Field(min_length=1, description="Nội dung phương án")
    is_correct: bool = Field(default=False, description="Đánh dấu đáp án đúng")


class QuestionOptionResponse(BaseModel):
    """Schema trả về phương án lựa chọn."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    content: str
    is_correct: bool


class QuestionCreate(BaseModel):
    """Schema tạo câu hỏi mới cho Quiz."""

    type: Literal["single", "multiple", "true_false"] = Field(
        default="single", description="Loại câu hỏi"
    )
    content: str = Field(min_length=1, description="Nội dung câu hỏi")
    time_limit_s: int = Field(default=20, ge=5, le=60, description="Thời gian làm bài (giây)")
    points: int = Field(default=1000, description="Điểm thưởng (500, 1000, 2000)")
    explanation: str | None = Field(default=None, description="Giải thích đáp án")
    topic: str | None = Field(default=None, description="Chủ đề phân loại")
    source_ref: str | None = Field(default=None, description="Trang tham chiếu tài liệu")
    options: list[QuestionOptionCreate] = Field(
        min_length=2, max_length=4, description="Danh sách lựa chọn"
    )


class QuestionUpdate(BaseModel):
    """Schema cập nhật nội dung câu hỏi."""

    type: Literal["single", "multiple", "true_false"] | None = None
    content: str | None = Field(default=None, min_length=1)
    time_limit_s: int | None = Field(default=None, ge=5, le=60)
    points: int | None = None
    explanation: str | None = None
    topic: str | None = None
    source_ref: str | None = None
    options: list[QuestionOptionCreate] | None = None


class QuestionResponse(BaseModel):
    """Schema chi tiết một câu hỏi."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    quiz_id: uuid.UUID
    position: int
    type: str
    content: str
    time_limit_s: int
    points: int
    explanation: str | None = None
    topic: str | None = None
    source_ref: str | None = None
    ai_generated: bool = False
    created_at: datetime
    options: list[QuestionOptionResponse] = Field(default_factory=list)


class QuestionReorderRequest(BaseModel):
    """Schema đổi thứ tự các câu hỏi trong quiz."""

    question_ids: list[uuid.UUID] = Field(
        min_length=1, description="Danh sách ID các câu hỏi theo thứ tự mới mong muốn"
    )


class QuestionRegenerateRequest(BaseModel):
    """Schema yêu cầu AI sinh lại một câu hỏi (FR-AIGEN-09)."""

    notes: str | None = Field(default=None, max_length=300, description="Chỉ dẫn thêm từ host")
    difficulty: Literal["easy", "medium", "hard"] | None = Field(
        default=None, description="Độ khó câu hỏi"
    )
    type: Literal["single", "multiple", "true_false"] | None = Field(
        default=None, description="Loại câu hỏi"
    )


class QuizCreate(BaseModel):
    """Schema tạo Quiz mới."""

    title: str = Field(min_length=1, max_length=255, description="Tiêu đề quiz")
    description: str | None = Field(default=None, description="Mô tả chi tiết")
    visibility: Literal["private", "unlisted", "public"] = Field(
        default="private", description="Chế độ hiển thị"
    )
    tags: list[str] = Field(default_factory=list, description="Thẻ phân loại")


class QuizUpdate(BaseModel):
    """Schema chỉnh sửa Quiz."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["private", "unlisted", "public"] | None = None
    tags: list[str] | None = None


class QuizCloneRequest(BaseModel):
    """Schema yêu cầu sao chép bộ Quiz (QZ-5)."""

    title: str | None = Field(
        default=None, max_length=255, description="Tiêu đề mới cho quiz sao chép"
    )


class QuizResponse(BaseModel):
    """Schema tóm tắt bộ Quiz."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str | None = None
    visibility: str
    status: str
    version: int
    tags: list[str] = Field(default_factory=list)
    play_count: int = 0
    question_count: int = 0
    created_at: datetime
    updated_at: datetime


class QuizDetailResponse(QuizResponse):
    """Schema chi tiết bộ Quiz kèm danh sách câu hỏi."""

    questions: list[QuestionResponse] = Field(default_factory=list)
