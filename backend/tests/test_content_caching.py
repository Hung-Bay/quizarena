"""Kiểm thử tính năng Content Caching (AG-8, FR-AIGEN-10).

Tiêu chí Thứ Năm Phase 2 và SRS:
- Upload lại cùng file + cùng tham số -> Không gọi Gemini (chứng minh bằng call_count / log).
- Cache hit không tiêu tốn hạn mức quota N lần/ngày của host (FR-AIGEN-13).
- Bản nháp kết quả được sao chép thành bộ Quiz riêng biệt thuộc quyền sở hữu của host yêu cầu (ASM-2).
- ai_jobs.cache_hit = True được lưu trong cơ sở dữ liệu để chứng minh bằng truy vấn SQL.
- Upload cùng file nhưng khác tham số -> Cache miss và kích hoạt gọi Gemini bình thường.
"""

from __future__ import annotations

import io
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.ai.fake import FakeGemini
from app.api.deps import get_redis, get_session
from app.core import security
from app.core.rbac import ROLE_PERMISSIONS, RoleName
from app.main_api import app
from app.models.ai import AiJob, Document
from app.models.quiz import Question, QuestionOption, Quiz
from app.repositories import ai_repository, quiz_repository
from app.services import ai_job_service
from app.worker import generate_quiz_task


class FakeRedisForCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def set(self, key: str, value: str, *args: Any, **kwargs: Any) -> None:
        self.store[key] = str(value)

    async def incr(self, key: str) -> int:
        val = int(self.store.get(key, "0")) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def publish(self, ch: str, msg: str) -> int:
        self.published.append((ch, msg))
        return 1

    async def eval(self, *args: Any) -> list[Any]:
        return [1, "0", "15"]


HOST_A_ID = uuid.uuid4()
HOST_B_ID = uuid.uuid4()


def make_token(user_id: uuid.UUID) -> str:
    perms = [p.value for p in ROLE_PERMISSIONS[RoleName.HOST]]
    return security.create_access_token(
        user_id=user_id,
        roles=[RoleName.HOST.value],
        permissions=perms,
        token_version=1,
    )


def generate_pdf_bytes(num_pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_content_caching_full_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Kiểm thử vòng đời đầy đủ của Content Caching:

    1. Host A upload file F với tham số P: Cache miss -> gọi Gemini -> tạo Quiz A.
    2. Host B upload cùng file F với cùng tham số P: Cache hit -> KHÔNG gọi Gemini,
       KHÔNG tốn quota của Host B, tạo Quiz B riêng biệt cho Host B, ai_jobs.cache_hit = True.
    3. Host A upload cùng file F nhưng tham số P2 khác (difficulty="hard"):
       Cache miss -> gọi Gemini lần 2 -> tạo Quiz mới.
    """
    # 1. Bộ nhớ in-memory giả lập database
    db_docs: dict[uuid.UUID, Document] = {}
    db_jobs: dict[uuid.UUID, AiJob] = {}
    db_quizzes: dict[uuid.UUID, Quiz] = {}
    db_questions: dict[uuid.UUID, Question] = {}
    db_options: dict[uuid.UUID, QuestionOption] = {}

    fake_redis = FakeRedisForCache()
    fake_gateway = FakeGemini()

    # Mock DB Session
    class MockDbSession:
        def begin(self):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _ctx():
                yield self

            return _ctx()

        def add(self, obj: Any) -> None:
            if isinstance(obj, Document):
                obj.id = obj.id or uuid.uuid4()
                db_docs[obj.id] = obj
            elif isinstance(obj, AiJob):
                obj.id = obj.id or uuid.uuid4()
                db_jobs[obj.id] = obj
            elif isinstance(obj, Quiz):
                obj.id = obj.id or uuid.uuid4()
                db_quizzes[obj.id] = obj
            elif isinstance(obj, Question):
                obj.id = obj.id or uuid.uuid4()
                db_questions[obj.id] = obj
                if hasattr(obj, "quiz_id") and obj.quiz_id in db_quizzes:
                    q_list = getattr(db_quizzes[obj.quiz_id], "questions", None)
                    if q_list is not None and obj not in q_list:
                        q_list.append(obj)
            elif isinstance(obj, QuestionOption):
                obj.id = obj.id or uuid.uuid4()
                db_options[obj.id] = obj
                if hasattr(obj, "question_id") and obj.question_id in db_questions:
                    opt_list = getattr(db_questions[obj.question_id], "options", None)
                    if opt_list is not None and obj not in opt_list:
                        opt_list.append(obj)

        async def flush(self) -> None:
            pass

        async def refresh(self, obj: Any, attrs: Any = None) -> None:
            pass

    class MockSessionFactory:
        def __call__(self):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _ctx():
                yield MockDbSession()

            return _ctx()

    async def fake_get_session_override():
        yield MockDbSession()

    # Monkeypatch ai_repository
    async def mock_create_doc(session: Any, **kwargs: Any) -> Document:
        doc = Document(**kwargs)
        doc.id = doc.id or uuid.uuid4()
        db_docs[doc.id] = doc
        return doc

    async def mock_create_job(session: Any, **kwargs: Any) -> AiJob:
        from datetime import UTC, datetime

        job = AiJob(**kwargs)
        job.id = job.id or uuid.uuid4()
        job.status = "queued"
        job.input_tokens = 0
        job.output_tokens = 0
        job.attempts = 0
        job.cache_hit = False
        job.created_at = datetime.now(UTC)
        db_jobs[job.id] = job
        return job

    async def mock_get_job(session: Any, jid: uuid.UUID) -> AiJob | None:
        job = db_jobs.get(jid)
        if job and job.document_id and job.document_id in db_docs:
            job.document = db_docs[job.document_id]
        return job

    async def mock_update_job(session: Any, job_id: uuid.UUID, **kwargs: Any) -> AiJob | None:
        job = db_jobs.get(job_id)
        if not job:
            return None
        for k, v in kwargs.items():
            if k == "finished" and v:
                from datetime import UTC, datetime

                job.finished_at = datetime.now(UTC)
            elif hasattr(job, k):
                setattr(job, k, v)
        return job

    async def mock_find_cached(session: Any, sha256: str, params_hash: str) -> AiJob | None:
        for j in db_jobs.values():
            if j.status == "succeeded":
                doc = db_docs.get(j.document_id)
                if doc and doc.sha256 == sha256:
                    j_hash = j.params.get("_params_hash") or j.params.get("params_hash")
                    if j_hash == params_hash and j.result and j.result.get("quiz_id"):
                        return j
        return None

    monkeypatch.setattr(ai_repository, "create_document", mock_create_doc)
    monkeypatch.setattr(ai_repository, "create_ai_job", mock_create_job)
    monkeypatch.setattr(ai_repository, "get_ai_job_by_id", mock_get_job)
    monkeypatch.setattr(ai_repository, "update_ai_job_status", mock_update_job)
    monkeypatch.setattr(ai_repository, "find_cached_ai_job", mock_find_cached)

    # Monkeypatch quiz_repository.get_by_id
    async def mock_get_quiz_by_id(session: Any, qid: uuid.UUID, **kwargs: Any) -> Quiz | None:
        quiz = db_quizzes.get(qid)
        if quiz:
            # Gắn các câu hỏi tương ứng
            q_list = [q for q in db_questions.values() if q.quiz_id == qid]
            for q in q_list:
                q.options = [opt for opt in db_options.values() if opt.question_id == q.id]
            quiz.questions = q_list
        return quiz

    monkeypatch.setattr(quiz_repository, "get_by_id", mock_get_quiz_by_id)

    # Mock ARQ pool enqueue
    async def mock_enqueue(*args, **kwargs):
        return True

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = mock_enqueue
    monkeypatch.setattr(ai_job_service, "get_arq_redis_pool", AsyncMock(return_value=mock_pool))

    # Setup isolated FastAPI overrides
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = fake_get_session_override
    app.dependency_overrides[get_redis] = lambda: fake_redis

    client = TestClient(app)

    try:
        # Chuẩn bị file PDF mẫu
        pdf_bytes = generate_pdf_bytes(num_pages=3)
        token_a = make_token(HOST_A_ID)
        token_b = make_token(HOST_B_ID)

        # -------------------------------------------------------------
        # Bước 1: Host A upload file F lần đầu (CACHE MISS)
        # -------------------------------------------------------------
        resp_a = client.post(
            "/api/v1/ai/quiz-generations",
            files={"file": ("lecture.pdf", pdf_bytes, "application/pdf")},
            data={"num_questions": 5, "difficulty": "medium", "language": "vi"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 202
        data_a = resp_a.json()
        job_a_id = data_a["job_id"]
        assert data_a["status"] == "queued"

        # Host A quota bị trừ 1 lần
        from app.services.gemini_rate_limiter import get_pacific_date_str

        p_date = get_pacific_date_str()
        quota_key_a = f"ai:quota:host:{HOST_A_ID}:{p_date}"
        assert await fake_redis.get(quota_key_a) == "1"

        # Worker xử lý Job A
        worker_ctx = {
            "session_factory": MockSessionFactory(),
            "redis": fake_redis,
            "gateway": fake_gateway,
        }
        res_worker = await generate_quiz_task(worker_ctx, job_a_id)
        assert res_worker["status"] == "succeeded"
        assert fake_gateway.call_count == 1  # Đã gọi Gemini 1 lần

        quiz_a_id = uuid.UUID(res_worker["quiz_id"])
        quiz_a = db_quizzes[quiz_a_id]
        assert quiz_a.owner_id == HOST_A_ID
        assert db_jobs[uuid.UUID(job_a_id)].cache_hit is False

        # -------------------------------------------------------------
        # Bước 2: Host B upload CÙNG file F và CÙNG tham số (CACHE HIT)
        # -------------------------------------------------------------
        resp_b = client.post(
            "/api/v1/ai/quiz-generations",
            files={"file": ("lecture_renamed.pdf", pdf_bytes, "application/pdf")},
            data={"num_questions": 5, "difficulty": "medium", "language": "vi"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 202
        data_b = resp_b.json()
        job_b_id = data_b["job_id"]
        # Trả về succeeded ngay lập tức vì Cache Hit
        assert data_b["status"] == "succeeded"
        assert "Cache Hit" in data_b["message"]

        # Kiểm chứng 1: Gateway KHÔNG ĐƯỢC GỌI THÊM LẦN NÀO
        assert fake_gateway.call_count == 1  # Vẫn giữ nguyên 1 lần gọi!

        # Kiểm chứng 2: Quota của Host B KHÔNG HỀ BỊ TRỪ
        quota_key_b = f"ai:quota:host:{HOST_B_ID}:{p_date}"
        quota_b_val = await fake_redis.get(quota_key_b)
        assert quota_b_val is None or quota_b_val == "0"

        # Kiểm chứng 3: Cờ cache_hit trong database là True
        job_b = db_jobs[uuid.UUID(job_b_id)]
        assert job_b.cache_hit is True
        assert job_b.status == "succeeded"

        # Kiểm chứng 4: Quiz B là một bản sao độc lập thuộc quyền sở hữu của Host B
        quiz_b_id = uuid.UUID(job_b.result["quiz_id"])
        assert quiz_b_id != quiz_a_id  # Hai Quiz ID hoàn toàn khác nhau
        quiz_b = db_quizzes[quiz_b_id]
        assert quiz_b.owner_id == HOST_B_ID  # Thuộc sở hữu của Host B
        assert len(quiz_b.questions) == len(quiz_a.questions)  # Cùng số lượng câu hỏi

        # -------------------------------------------------------------
        # Bước 3: Host A upload lại file F nhưng THAM SỐ KHÁC (CACHE MISS)
        # -------------------------------------------------------------
        resp_a2 = client.post(
            "/api/v1/ai/quiz-generations",
            files={"file": ("lecture.pdf", pdf_bytes, "application/pdf")},
            data={"num_questions": 5, "difficulty": "hard", "language": "vi"},  # difficulty khác
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a2.status_code == 202
        data_a2 = resp_a2.json()
        assert data_a2["status"] == "queued"  # Cache miss nên đưa vào hàng đợi

        # Quota của Host A tăng lên 2
        assert await fake_redis.get(quota_key_a) == "2"

        # Worker xử lý job A2
        res_worker2 = await generate_quiz_task(worker_ctx, data_a2["job_id"])
        assert res_worker2["status"] == "succeeded"
        assert fake_gateway.call_count == 2  # Gemini được gọi thêm lần thứ 2

        # -------------------------------------------------------------
        # Bước 4: Kiểm tra bằng truy vấn SQL mô phỏng: SELECT COUNT(*) WHERE cache_hit = true
        # -------------------------------------------------------------
        cache_hits_count = sum(1 for j in db_jobs.values() if j.cache_hit is True)
        assert cache_hits_count == 1

    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)
