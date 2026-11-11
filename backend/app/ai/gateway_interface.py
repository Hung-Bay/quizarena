"""Protocol định nghĩa giao diện chuẩn cho Gemini Gateway.

Mọi module khác (workers, services, routers, CLI) chỉ phụ thuộc vào Protocol này,
tuyệt đối không import trực tiếp SDK `google.genai` ra ngoài gateway.
"""

from typing import Any, Protocol, runtime_checkable

from app.ai.schemas import (
    AiGenerationParams,
    GeneratedQuizResponse,
    GeneratedReportResponse,
    QuestionDraft,
)


@runtime_checkable
class GeminiGatewayProtocol(Protocol):
    """Giao diện trừu tượng cho Gateway giao tiếp với mô hình AI."""

    def get_model_info(self) -> dict[str, Any]:
        """Lấy thông tin và trạng thái kết nối của mô hình AI."""
        ...

    async def generate_quiz_from_text(
        self, text_content: str, params: AiGenerationParams
    ) -> GeneratedQuizResponse:
        """Sinh bộ câu hỏi từ chuỗi văn bản."""
        ...

    async def generate_quiz_from_file(
        self, file_bytes: bytes, mime_type: str, params: AiGenerationParams
    ) -> GeneratedQuizResponse:
        """Sinh bộ câu hỏi từ tài liệu nhị phân (PDF, TXT, MD)."""
        ...

    async def regenerate_single_question(
        self,
        current_question: str,
        current_options: list[str],
        params: AiGenerationParams,
        context: str | None = None,
    ) -> QuestionDraft:
        """Sinh lại một câu hỏi đơn lẻ dựa trên câu hỏi cũ và tham số bổ sung (FR-AIGEN-09)."""
        ...

    async def generate_game_report(self, stats: dict[str, Any]) -> GeneratedReportResponse:
        """Sinh báo cáo phân tích sư phạm sau trận đấu từ số liệu thống kê (FR-AIRPT-01..05)."""
        ...
