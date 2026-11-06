"""Pydantic schemas cho module AI (QuizDraft, QuestionDraft, v.v.).

LƯU Ý: Gemini Structured Output yêu cầu schema đơn giản, tránh union phức tạp
để đảm bảo LLM sinh JSON chính xác và ổn định.
"""

from typing import Literal

from pydantic import BaseModel, Field

QuestionType = Literal["single", "multiple", "true_false"]


def _default_allowed_types() -> list[QuestionType]:
    return ["single", "multiple", "true_false"]


class QuestionOptionDraft(BaseModel):
    """Một phương án lựa chọn trong câu hỏi nháp."""

    content: str = Field(description="Nội dung phương án lựa chọn")
    is_correct: bool = Field(description="Đánh dấu phương án này là đúng (true) hay sai (false)")


class QuestionDraft(BaseModel):
    """Một câu hỏi nháp do AI sinh ra."""

    content: str = Field(description="Nội dung câu hỏi")
    type: QuestionType = Field(
        default="single",
        description="Loại câu hỏi: 'single' (1 đáp án đúng), 'multiple' (>=1 đáp án đúng), 'true_false' (Đúng/Sai)",
    )
    options: list[QuestionOptionDraft] = Field(
        description="Danh sách lựa chọn (2-4 phương án; với true_false là 2 phương án: Đúng, Sai)"
    )
    explanation: str | None = Field(
        default=None, description="Giải thích ngắn gọn tại sao đáp án lại đúng"
    )
    topic: str | None = Field(default=None, description="Chủ đề hoặc nhãn phân loại câu hỏi")
    source_ref: str | None = Field(
        default=None,
        description="Trang nguồn hoặc trích dẫn tham chiếu trong tài liệu (ví dụ: 'Trang 3')",
    )
    time_limit_s: int = Field(
        default=20, ge=5, le=60, description="Thời gian trả lời (5 - 60 giây)"
    )
    points: int = Field(default=1000, description="Điểm thưởng: 500, 1000, hoặc 2000")


class QuizDraft(BaseModel):
    """Toàn bộ bản nháp bộ câu hỏi do AI tạo ra từ tài liệu."""

    title: str = Field(description="Tiêu đề tổng quát gợi ý cho bộ quiz")
    description: str | None = Field(
        default=None, description="Mô tả tóm tắt nội dung kiến thức của quiz"
    )
    questions: list[QuestionDraft] = Field(description="Danh sách các câu hỏi nháp")


class AiGenerationParams(BaseModel):
    """Tham số đầu vào khi Host yêu cầu sinh quiz từ tài liệu (AG-2)."""

    num_questions: int = Field(default=5, ge=1, le=20, description="Số câu hỏi mong muốn (1 - 20)")
    difficulty: Literal["easy", "medium", "hard"] = Field(
        default="medium", description="Độ khó câu hỏi"
    )
    language: Literal["vi", "en"] = Field(
        default="vi", description="Ngôn ngữ sinh câu hỏi ('vi' hoặc 'en')"
    )
    allowed_types: list[QuestionType] = Field(
        default_factory=_default_allowed_types,
        description="Các loại câu hỏi cho phép tạo",
    )
    note: str | None = Field(
        default=None,
        max_length=300,
        description="Ghi chú thêm của host gửi cho AI (tối đa 300 ký tự)",
    )


class GatewayUsage(BaseModel):
    """Thống kê token sử dụng của một lần gọi Gemini."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class GeneratedQuizResponse(BaseModel):
    """Kết quả trả về từ Gemini Gateway."""

    draft: QuizDraft
    raw_json: str
    usage: GatewayUsage
    model_name: str
