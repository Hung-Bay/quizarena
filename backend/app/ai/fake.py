"""FakeGemini: Giả lập toàn diện cho Gemini Gateway phục vụ Unit Test và Development.

Hỗ trợ mô phỏng:
- Thành công: trả về QuizDraft hợp lệ theo đúng params (số câu, ngôn ngữ, loại câu).
- Lỗi 429: mô phỏng Rate Limit kèm thời gian retryDelay.
- Lỗi Malformed JSON: mô phỏng LLM trả về cú pháp hỏng để test tầng validate 1.
- Lỗi Timeout: mô phỏng kết nối quá hạn 90s.
"""

import asyncio
import json
from typing import Any

from app.ai.gateway_interface import GeminiGatewayProtocol
from app.ai.schemas import (
    AiGenerationParams,
    GatewayUsage,
    GeneratedQuizResponse,
    QuestionDraft,
    QuestionOptionDraft,
    QuizDraft,
)


class GeminiRateLimitError(Exception):
    """Mô phỏng lỗi HTTP 429 Too Many Requests từ Gemini API."""

    def __init__(
        self, message: str = "Resource has been exhausted", retry_delay: float = 1.0
    ) -> None:
        super().__init__(message)
        self.retry_delay = retry_delay


class GeminiGatewayError(Exception):
    """Lỗi chung từ Gateway."""


class FakeGemini(GeminiGatewayProtocol):
    """Triển khai giả lập của GeminiGatewayProtocol."""

    def __init__(
        self,
        *,
        should_fail_429: bool = False,
        retry_delay_seconds: float = 1.0,
        should_return_malformed_json: bool = False,
        should_timeout: bool = False,
        custom_draft: QuizDraft | None = None,
    ) -> None:
        self.should_fail_429 = should_fail_429
        self.retry_delay_seconds = retry_delay_seconds
        self.should_return_malformed_json = should_return_malformed_json
        self.should_timeout = should_timeout
        self.custom_draft = custom_draft

        self.call_count: int = 0
        self.call_history: list[dict[str, Any]] = []

    def _build_default_draft(self, params: AiGenerationParams) -> QuizDraft:
        """Tạo dữ liệu QuizDraft mẫu hợp lệ theo tham số."""
        if self.custom_draft:
            return self.custom_draft

        questions: list[QuestionDraft] = []
        is_vi = params.language == "vi"
        allowed = params.allowed_types or ["single", "multiple", "true_false"]

        for i in range(1, params.num_questions + 1):
            q_type = allowed[(i - 1) % len(allowed)]
            if q_type == "true_false":
                q = QuestionDraft(
                    content=f"Câu hỏi {i} (Đúng/Sai): Đây là phát biểu mẫu số {i}?",
                    type="true_false",
                    options=[
                        QuestionOptionDraft(content="Đúng" if is_vi else "True", is_correct=True),
                        QuestionOptionDraft(content="Sai" if is_vi else "False", is_correct=False),
                    ],
                    explanation=f"Giải thích cho câu {i}",
                    topic="Kiến thức chung",
                    source_ref=f"Trang {min(i, 5)}",
                    time_limit_s=20,
                    points=1000,
                )
            elif q_type == "multiple":
                q = QuestionDraft(
                    content=f"Câu hỏi {i} (Nhiều đáp án): Những lựa chọn nào sau đây là đúng?",
                    type="multiple",
                    options=[
                        QuestionOptionDraft(content=f"Lựa chọn A của câu {i}", is_correct=True),
                        QuestionOptionDraft(content=f"Lựa chọn B của câu {i}", is_correct=True),
                        QuestionOptionDraft(content=f"Lựa chọn C của câu {i}", is_correct=False),
                        QuestionOptionDraft(content=f"Lựa chọn D của câu {i}", is_correct=False),
                    ],
                    explanation=f"Giải thích cho câu {i}",
                    topic="Kiến thức nâng cao",
                    source_ref=f"Trang {min(i, 5)}",
                    time_limit_s=30,
                    points=2000,
                )
            else:  # single
                q = QuestionDraft(
                    content=f"Câu hỏi {i}: Đâu là câu trả lời chính xác nhất?",
                    type="single",
                    options=[
                        QuestionOptionDraft(content=f"Đáp án đúng của câu {i}", is_correct=True),
                        QuestionOptionDraft(
                            content=f"Phương án sai 1 của câu {i}", is_correct=False
                        ),
                        QuestionOptionDraft(
                            content=f"Phương án sai 2 của câu {i}", is_correct=False
                        ),
                        QuestionOptionDraft(
                            content=f"Phương án sai 3 của câu {i}", is_correct=False
                        ),
                    ],
                    explanation=f"Giải thích cho câu {i}",
                    topic="Kiến thức cơ bản",
                    source_ref=f"Trang {min(i, 5)}",
                    time_limit_s=20,
                    points=1000,
                )
            questions.append(q)

        return QuizDraft(
            title="Bộ câu hỏi kiểm tra kiến thức mẫu",
            description="Tài liệu trích xuất tự động qua hệ thống QuizArena AI.",
            questions=questions,
        )

    def get_model_info(self) -> dict[str, Any]:
        """Trả về thông tin mô hình giả lập."""
        return {
            "name": "models/fake-gemini-3.5-flash",
            "display_name": "Fake Gemini 3.5 Flash",
            "input_token_limit": 1048576,
            "output_token_limit": 65536,
            "status": "connected (fake)",
        }

    async def _execute_simulation(
        self, source_info: str, params: AiGenerationParams
    ) -> GeneratedQuizResponse:
        self.call_count += 1
        self.call_history.append({"source": source_info, "params": params.model_dump()})

        if self.should_timeout:
            await asyncio.sleep(0.1)
            raise TimeoutError("Mô phỏng Gemini API timeout sau 90 giây")

        if self.should_fail_429:
            raise GeminiRateLimitError(
                message="Resource has been exhausted (rate limit)",
                retry_delay=self.retry_delay_seconds,
            )

        if self.should_return_malformed_json:
            # Trả về JSON hỏng
            malformed_text = '{"title": "Quiz hỏng", "questions": [{"content": "Thiếu options"}'
            raise GeminiGatewayError(f"Phản hồi từ Gemini không đúng chuẩn JSON: {malformed_text}")

        draft = self._build_default_draft(params)
        raw_json = json.dumps(draft.model_dump(), ensure_ascii=False)
        usage = GatewayUsage(input_tokens=500, output_tokens=800, total_tokens=1300)

        return GeneratedQuizResponse(
            draft=draft,
            raw_json=raw_json,
            usage=usage,
            model_name="fake-gemini-2.5-flash",
        )

    async def generate_quiz_from_text(
        self, text_content: str, params: AiGenerationParams
    ) -> GeneratedQuizResponse:
        return await self._execute_simulation(
            source_info=f"text(len={len(text_content)})", params=params
        )

    async def generate_quiz_from_file(
        self, file_bytes: bytes, mime_type: str, params: AiGenerationParams
    ) -> GeneratedQuizResponse:
        return await self._execute_simulation(
            source_info=f"file(bytes={len(file_bytes)}, mime={mime_type})", params=params
        )
