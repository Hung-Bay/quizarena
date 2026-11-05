"""Kiểm thử xử lý lỗi Gemini (429, JSON hỏng, Timeout) và cơ chế Validate 2 lớp kèm Retry.

Tuân thủ tiêu chí Thứ Năm Phase 2 và FR-AIGEN-04...06, FR-AIGEN-12:
- FakeGemini giả lập HTTP 429: Gateway tự động retry theo retryDelay (tối đa 3 lần).
- FakeGemini giả lập JSON hỏng: Bắt lỗi ở Lớp 1 (Schema validation).
- FakeGemini giả lập câu hỏi vi phạm: Lớp 2 (Nghiệp vụ QZ-2) loại bỏ câu lỗi.
  Nếu số câu hợp lệ < 50% yêu cầu -> Tự động retry 1 lần kèm feedback; sau đó hoàn thành thành công.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.ai.fake import FakeGemini, GeminiGatewayError, GeminiRateLimitError
from app.ai.schemas import AiGenerationParams
from app.models.ai import AiJob, Document
from app.models.quiz import Question, Quiz
from app.repositories import ai_repository
from app.worker import generate_quiz_task


@pytest.mark.asyncio
async def test_fake_gemini_429_retry_success_within_3_attempts() -> None:
    """Kiểm tra FakeGemini trả về 429 hai lần đầu, tự động retry và thành công ở lần 3."""
    # fail_429_count = 2 -> 2 lần đầu bị 429, lần 3 thành công
    fake_gateway = FakeGemini(fail_429_count=2, retry_delay_seconds=0.01)
    params = AiGenerationParams(num_questions=5)

    res = await fake_gateway.generate_quiz_from_file(
        file_bytes=b"%PDF-fake",
        mime_type="application/pdf",
        params=params,
    )

    assert res.draft is not None
    assert len(res.draft.questions) == 5
    assert fake_gateway.call_count == 3  # Đã gọi đúng 3 lần (2 lần retry)


@pytest.mark.asyncio
async def test_fake_gemini_429_exceeds_max_retries() -> None:
    """Kiểm tra khi bị 429 liên tục quá 3 lần thì ném ngoại lệ GeminiRateLimitError."""
    fake_gateway = FakeGemini(should_fail_429=True, retry_delay_seconds=0.01)
    params = AiGenerationParams(num_questions=5)

    with pytest.raises(GeminiRateLimitError):
        await fake_gateway.generate_quiz_from_file(
            file_bytes=b"%PDF-fake",
            mime_type="application/pdf",
            params=params,
        )


@pytest.mark.asyncio
async def test_fake_gemini_malformed_json_fails_layer_1() -> None:
    """Kiểm tra khi Gemini trả về JSON hỏng thì bị bắt ở Lớp 1 (Schema Validation)."""
    fake_gateway = FakeGemini(should_return_malformed_json=True)
    params = AiGenerationParams(num_questions=5)

    with pytest.raises(GeminiGatewayError) as exc_info:
        await fake_gateway.generate_quiz_from_file(
            file_bytes=b"%PDF-fake",
            mime_type="application/pdf",
            params=params,
        )

    assert "JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_worker_layer_2_tolerance_retry_when_under_50_percent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Kiểm thử worker: Khi lần đầu sinh ra < 50% câu hợp lệ, worker tự động retry 1 lần với feedback.

    Lần 1: 3/5 câu bị lỗi (chỉ còn 2 câu hợp lệ = 40% < 50%).
    Worker gửi prompt feedback lỗi.
    Lần 2: FakeGemini nhận feedback và trả về đủ 5 câu hợp lệ.
    Kết quả: Job thành công với attempts=2, 5 câu hỏi được lưu vào DB.
    """
    # 1. Chuẩn bị file PDF giả lập
    fake_pdf_path = tmp_path / "test_doc.pdf"
    fake_pdf_path.write_bytes(b"%PDF-1.4 sample content")

    host_id = uuid.uuid4()
    job_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    doc = Document(
        id=doc_id,
        uploaded_by=host_id,
        filename="test_doc.pdf",
        mime="application/pdf",
        size_bytes=100,
        pages=5,
        sha256="fake_sha256",
        storage_path=str(fake_pdf_path),
    )

    job = AiJob(
        id=job_id,
        created_by=host_id,
        document_id=doc_id,
        params={"num_questions": 5, "difficulty": "medium", "language": "vi"},
        status="queued",
    )
    job.document = doc

    # 2. Khởi tạo FakeGemini với 3 câu lỗi ở lần 1
    fake_gateway = FakeGemini(invalid_questions_count=3)

    # 3. Mock DB Session và Repositories
    saved_quizzes: list[Quiz] = []
    saved_questions: list[Question] = []

    class MockSession:
        def begin(self):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _ctx():
                yield self

            return _ctx()

        def add(self, obj: Any) -> None:
            if isinstance(obj, Quiz):
                saved_quizzes.append(obj)
            elif isinstance(obj, Question):
                saved_questions.append(obj)

        async def flush(self) -> None:
            pass

    class MockSessionFactory:
        def __call__(self):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _ctx():
                yield MockSession()

            return _ctx()

    async def mock_get_job(session: Any, job_id: uuid.UUID) -> AiJob | None:
        return job if job_id == job.id else None

    async def mock_update_status(session: Any, job_id: uuid.UUID, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(job, k, v)

    monkeypatch.setattr(ai_repository, "get_ai_job_by_id", mock_get_job)
    monkeypatch.setattr(ai_repository, "update_ai_job_status", mock_update_status)

    # 4. Mock Redis Pub/Sub và Rate Limiter
    class MockRedis:
        async def publish(self, ch: str, msg: str) -> int:
            return 1

        async def get(self, key: str) -> str | None:
            return "0"

        async def incr(self, key: str) -> int:
            return 1

        async def expire(self, key: str, sec: int) -> bool:
            return True

        async def eval(self, *args: Any) -> list[Any]:
            return [1, "0", "10"]

    ctx = {
        "session_factory": MockSessionFactory(),
        "redis": MockRedis(),
        "gateway": fake_gateway,
    }

    # 5. Thực thi tác vụ worker
    res = await generate_quiz_task(ctx, str(job_id))

    # 6. Kiểm tra kết quả
    assert res["status"] == "succeeded"
    assert res["attempts"] == 2  # Đã retry đúng 1 lần
    assert job.status == "succeeded"
    assert job.attempts == 2
    assert fake_gateway.call_count == 2  # Gateway được gọi 2 lần (lần 1 + retry)
    assert len(saved_quizzes) == 1
    assert len(saved_questions) == 5  # Toàn bộ 5 câu hợp lệ sau lần retry
