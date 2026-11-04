"""Unit tests cho Gemini Gateway và FakeGemini.

Đảm bảo:
- FakeGemini xử lý đúng các kịch bản: thành công, lỗi 429, lỗi JSON hỏng, timeout.
- SDK google.genai không bị import rò rỉ ra ngoài module app/ai/gemini_gateway.py.
"""

from pathlib import Path

import pytest

from app.ai.fake import FakeGemini, GeminiGatewayError, GeminiRateLimitError
from app.ai.schemas import AiGenerationParams


@pytest.mark.asyncio
async def test_fake_gemini_generates_valid_draft():
    fake = FakeGemini()
    params = AiGenerationParams(
        num_questions=5,
        difficulty="medium",
        language="vi",
        allowed_types=["single", "multiple", "true_false"],
    )

    res = await fake.generate_quiz_from_text("Tài liệu kiểm thử nội dung", params)
    assert res.model_name == "fake-gemini-2.5-flash"
    assert len(res.draft.questions) == 5
    assert res.usage.total_tokens > 0

    # Kiểm tra loại câu hỏi
    types_found = {q.type for q in res.draft.questions}
    assert "single" in types_found
    assert "multiple" in types_found
    assert "true_false" in types_found


@pytest.mark.asyncio
async def test_fake_gemini_simulates_429_rate_limit():
    fake = FakeGemini(should_fail_429=True, retry_delay_seconds=2.5)
    params = AiGenerationParams(num_questions=5)

    with pytest.raises(GeminiRateLimitError) as exc_info:
        await fake.generate_quiz_from_text("Nội dung", params)

    assert exc_info.value.retry_delay == 2.5
    assert "Resource has been exhausted" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fake_gemini_simulates_malformed_json():
    fake = FakeGemini(should_return_malformed_json=True)
    params = AiGenerationParams(num_questions=5)

    with pytest.raises(GeminiGatewayError) as exc_info:
        await fake.generate_quiz_from_text("Nội dung", params)

    assert "không đúng chuẩn JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fake_gemini_simulates_timeout():
    fake = FakeGemini(should_timeout=True)
    params = AiGenerationParams(num_questions=5)

    with pytest.raises(TimeoutError) as exc_info:
        await fake.generate_quiz_from_text("Nội dung", params)

    assert "timeout" in str(exc_info.value).lower()


def test_sdk_not_imported_outside_gateway():
    """Kiểm tra quy định bất biến: Không còn chỗ nào import SDK Gemini ngoài gemini_gateway.py."""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    forbidden_patterns = ["from google import genai", "from google.genai", "import google.genai"]

    violating_files = []
    for py_file in app_dir.rglob("*.py"):
        # Bỏ qua chính file gateway
        if py_file.name == "gemini_gateway.py":
            continue

        text = py_file.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            if pat in text:
                violating_files.append(f"{py_file.name}: chứa '{pat}'")

    assert not violating_files, (
        f"Phát hiện rò rỉ import Google GenAI SDK ngoài Gateway: {violating_files}"
    )
