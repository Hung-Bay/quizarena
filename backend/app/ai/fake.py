"""FakeGemini: Giả lập toàn diện cho Gemini Gateway phục vụ Unit Test và Development.

Hỗ trợ mô phỏng:
- Thành công: trả về QuizDraft hợp lệ theo đúng params (số câu, ngôn ngữ, loại câu).
- Lỗi 429: mô phỏng Rate Limit kèm thời gian retryDelay, hỗ trợ `fail_429_count` để test cơ chế retry tự động tối đa 3 lần.
- Lỗi Malformed JSON: mô phỏng LLM trả về cú pháp hỏng để test tầng validate 1.
- Lỗi Timeout: mô phỏng kết nối quá hạn 90s.
- Lớp 2 Validation vi phạm: mô phỏng câu hỏi lỗi để test cơ chế retry khi < 50% số câu hợp lệ.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.ai.gateway_interface import GeminiGatewayProtocol
from app.ai.schemas import (
    AiGenerationParams,
    GameReportDraft,
    GatewayUsage,
    GeneratedQuizResponse,
    GeneratedReportResponse,
    QuestionDraft,
    QuestionOptionDraft,
    QuizDraft,
    TopicAnalysis,
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
        fail_429_count: int = 0,
        retry_delay_seconds: float = 0.05,
        should_fail_429: bool = False,
        should_return_malformed_json: bool = False,
        should_timeout: bool = False,
        custom_draft: QuizDraft | None = None,
        custom_report_draft: GameReportDraft | None = None,
        invalid_questions_count: int = 0,
        max_retries: int = 3,
    ) -> None:
        self.fail_429_count = fail_429_count
        self.retry_delay_seconds = retry_delay_seconds
        self.should_fail_429 = should_fail_429
        self.should_return_malformed_json = should_return_malformed_json
        self.should_timeout = should_timeout
        self.custom_draft = custom_draft
        self.custom_report_draft = custom_report_draft
        self.invalid_questions_count = invalid_questions_count
        self.max_retries = max_retries

        self.call_count: int = 0
        self.call_history: list[dict[str, Any]] = []
        self.last_stats: dict[str, Any] | None = None

    def _build_default_draft(self, params: AiGenerationParams) -> QuizDraft:
        """Tạo dữ liệu QuizDraft mẫu hợp lệ theo tham số."""
        if self.custom_draft:
            return self.custom_draft

        questions: list[QuestionDraft] = []
        is_vi = params.language == "vi"
        allowed = params.allowed_types or ["single", "multiple", "true_false"]

        # Nếu note chứa thông báo phản hồi lỗi lần trước, coi như Gemini đã sửa hết lỗi
        is_retry_feedback = bool(params.note and "PHẢN HỒI LỖI" in params.note)

        for i in range(1, params.num_questions + 1):
            q_type = allowed[(i - 1) % len(allowed)]

            # Mô phỏng câu hỏi vi phạm nếu cấu hình invalid_questions_count và chưa phải lần retry phản hồi
            is_deliberately_invalid = not is_retry_feedback and i <= self.invalid_questions_count

            if q_type == "true_false":
                if is_deliberately_invalid:
                    opts = [
                        QuestionOptionDraft(content="Đúng" if is_vi else "True", is_correct=False),
                        QuestionOptionDraft(content="Sai" if is_vi else "False", is_correct=False),
                    ]
                else:
                    opts = [
                        QuestionOptionDraft(content="Đúng" if is_vi else "True", is_correct=True),
                        QuestionOptionDraft(content="Sai" if is_vi else "False", is_correct=False),
                    ]
                q = QuestionDraft(
                    content=f"Câu hỏi {i} (Đúng/Sai): Đây là phát biểu mẫu số {i}?",
                    type="true_false",
                    options=opts,
                    explanation=f"Giải thích cho câu {i}",
                    topic="Kiến thức chung",
                    source_ref=f"Trang {min(i, 5)}",
                    time_limit_s=20,
                    points=1000,
                )
            elif q_type == "multiple":
                if is_deliberately_invalid:
                    opts = [
                        QuestionOptionDraft(content=f"Lựa chọn A của câu {i}", is_correct=False),
                        QuestionOptionDraft(content=f"Lựa chọn B của câu {i}", is_correct=False),
                    ]
                else:
                    opts = [
                        QuestionOptionDraft(content=f"Lựa chọn A của câu {i}", is_correct=True),
                        QuestionOptionDraft(content=f"Lựa chọn B của câu {i}", is_correct=True),
                        QuestionOptionDraft(content=f"Lựa chọn C của câu {i}", is_correct=False),
                        QuestionOptionDraft(content=f"Lựa chọn D của câu {i}", is_correct=False),
                    ]
                q = QuestionDraft(
                    content=f"Câu hỏi {i} (Nhiều đáp án): Những lựa chọn nào sau đây là đúng?",
                    type="multiple",
                    options=opts,
                    explanation=f"Giải thích cho câu {i}",
                    topic="Kiến thức nâng cao",
                    source_ref=f"Trang {min(i, 5)}",
                    time_limit_s=30,
                    points=2000,
                )
            else:  # single
                if is_deliberately_invalid:
                    opts = [
                        QuestionOptionDraft(content="Đáp án giống nhau", is_correct=False),
                        QuestionOptionDraft(content="Đáp án giống nhau", is_correct=False),
                    ]
                else:
                    opts = [
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
                    ]
                q = QuestionDraft(
                    content=f"Câu hỏi {i}: Đâu là câu trả lời chính xác nhất?",
                    type="single",
                    options=opts,
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
            await asyncio.sleep(0.01)
            raise TimeoutError("Mô phỏng Gemini API timeout sau 90 giây")

        if self.should_fail_429 or self.call_count <= self.fail_429_count:
            raise GeminiRateLimitError(
                message=f"Resource has been exhausted (rate limit) - attempt {self.call_count}",
                retry_delay=self.retry_delay_seconds,
            )

        if self.should_return_malformed_json:
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
        for attempt in range(self.max_retries + 1):
            try:
                return await self._execute_simulation(
                    source_info=f"text(len={len(text_content)})", params=params
                )
            except GeminiRateLimitError as rle:
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(rle.retry_delay)
        raise GeminiGatewayError("Đã hết số lần thử lại kết nối Gemini.")

    async def generate_quiz_from_file(
        self, file_bytes: bytes, mime_type: str, params: AiGenerationParams
    ) -> GeneratedQuizResponse:
        for attempt in range(self.max_retries + 1):
            try:
                return await self._execute_simulation(
                    source_info=f"file(bytes={len(file_bytes)}, mime={mime_type})", params=params
                )
            except GeminiRateLimitError as rle:
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(rle.retry_delay)
        raise GeminiGatewayError("Đã hết số lần thử lại kết nối Gemini.")

    async def regenerate_single_question(
        self,
        current_question: str,
        current_options: list[str],
        params: AiGenerationParams,
        context: str | None = None,
    ) -> QuestionDraft:
        self.call_count += 1
        q_type = (params.allowed_types or ["single"])[0]
        if q_type == "true_false":
            opts = [
                QuestionOptionDraft(content="Đúng", is_correct=True),
                QuestionOptionDraft(content="Sai", is_correct=False),
            ]
        elif q_type == "multiple":
            opts = [
                QuestionOptionDraft(content="Lựa chọn A được sinh lại", is_correct=True),
                QuestionOptionDraft(content="Lựa chọn B được sinh lại", is_correct=True),
                QuestionOptionDraft(content="Lựa chọn C sai", is_correct=False),
                QuestionOptionDraft(content="Lựa chọn D sai", is_correct=False),
            ]
        else:
            opts = [
                QuestionOptionDraft(content="Đáp án đúng mới", is_correct=True),
                QuestionOptionDraft(content="Phương án sai 1 mới", is_correct=False),
                QuestionOptionDraft(content="Phương án sai 2 mới", is_correct=False),
                QuestionOptionDraft(content="Phương án sai 3 mới", is_correct=False),
            ]
        return QuestionDraft(
            content=f"Câu hỏi sinh lại: {current_question[:30]}...",
            type=q_type,
            options=opts,
            explanation=f"Giải thích mới cho câu hỏi sinh lại ({params.difficulty})",
            topic="Kiến thức cập nhật",
            source_ref="Tài liệu tham khảo",
            time_limit_s=20,
            points=1000,
        )

    async def generate_game_report(self, stats: dict[str, Any]) -> GeneratedReportResponse:
        """Mô phỏng sinh báo cáo phân tích sư phạm bằng FakeGemini."""
        self.call_count += 1
        self.last_stats = stats
        self.call_history.append({"method": "generate_game_report", "stats": stats})

        if self.should_fail_429 or self.fail_429_count > 0:
            if self.fail_429_count > 0:
                self.fail_429_count -= 1
            raise GeminiRateLimitError(
                "FakeGemini rate limit 429", retry_delay=self.retry_delay_seconds
            )

        if self.should_timeout:
            raise TimeoutError("FakeGemini timeout (90s)")

        if self.should_return_malformed_json:
            raise GeminiGatewayError("FakeGemini malformed JSON error")

        if self.custom_report_draft:
            draft = self.custom_report_draft
        else:
            topic_stats = stats.get("topic_stats", [])
            strengths: list[TopicAnalysis] = []
            weaknesses: list[TopicAnalysis] = []

            for t in topic_stats:
                top_name = t.get("topic", "Chung")
                acc = t.get("accuracy_rate", 0.0)
                if acc >= 0.5:
                    strengths.append(
                        TopicAnalysis(
                            topic=top_name,
                            analysis=f"Học viên thể hiện hiểu biết tốt về {top_name} với tỉ lệ đúng {acc * 100:.0f}%.",
                        )
                    )
                else:
                    weaknesses.append(
                        TopicAnalysis(
                            topic=top_name,
                            analysis=f"Chủ đề {top_name} có tỉ lệ trả lời sai cao ({(1 - acc) * 100:.0f}%), cần củng cố.",
                        )
                    )

            draft = GameReportDraft(
                summary=(
                    f"Trận đấu hoàn tất với {stats.get('total_players', 0)} người chơi. "
                    f"Tỉ lệ đúng trung bình đạt {stats.get('overall_accuracy', 0.0) * 100:.1f}%."
                ),
                strengths=strengths,
                weaknesses=weaknesses,
                advice_for_host=(
                    "Giảng viên nên dành thời gian giải thích kỹ các câu hỏi khó "
                    "và hệ thống hóa lại các chủ đề học viên hay nhầm lẫn."
                ),
                advice_for_learner=(
                    "Học viên cần tự ôn lại các khái niệm trong chủ đề có tỉ lệ đúng thấp "
                    "và xem lại giải thích đáp án chi tiết."
                ),
            )

        return GeneratedReportResponse(
            draft=draft,
            raw_json=draft.model_dump_json(),
            usage=GatewayUsage(input_tokens=150, output_tokens=300, total_tokens=450),
            model_name="fake-gemini-report",
        )
