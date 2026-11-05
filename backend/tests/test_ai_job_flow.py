"""Kiểm thử tích hợp luồng AI Job: Upload PDF, Hạn mức Quota, Quyền sở hữu (BOLA) và SSE.

Tiêu chí nghiệm thu Thứ Tư (Image 2, 4):
1. Upload PDF trả HTTP 202 Accepted trong < 500 ms (FR-AIGEN-01...03).
2. Tham số không hợp lệ (num_questions ngoài [5, 20], notes > 300) trả 422 (FR-AIGEN-02).
3. Hạn mức N lần/ngày: Lần N+1 trả 429 kèm thời điểm reset giờ Pacific (FR-AIGEN-13).
4. Phân quyền: Host B truy cập job của Host A (chi tiết hoặc SSE) nhận 404 (FR-AUTHZ-03).
5. ARQ Worker xử lý hoàn tất, SSE nhận chuỗi trạng thái: queued -> running -> succeeded.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
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
from app.models.quiz import Question, Quiz
from app.repositories import ai_repository
from app.services import ai_job_service
from app.worker import generate_quiz_task

# =========================================================================
# In-memory Datastores
# =========================================================================

docs_db: dict[uuid.UUID, Document] = {}
jobs_db: dict[uuid.UUID, AiJob] = {}
quizzes_db: dict[uuid.UUID, Quiz] = {}
questions_db: dict[uuid.UUID, Question] = {}


class FakeSession:
    """Giả lập AsyncSession."""

    def begin(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _ctx():
            yield self

        return _ctx()

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Any, attrs: Any = None) -> None:
        pass

    def add(self, obj: Any) -> None:
        if isinstance(obj, Document):
            docs_db[obj.id] = obj
        elif isinstance(obj, AiJob):
            jobs_db[obj.id] = obj
        elif isinstance(obj, Quiz):
            quizzes_db[obj.id] = obj
        elif isinstance(obj, Question):
            questions_db[obj.id] = obj

    async def get(self, entity: Any, ident: Any) -> Any:
        from app.models.user import User

        if entity is User:
            return User(
                id=ident,
                email=f"user_{ident}@quizarena.local",
                password_hash="fakehash",
                display_name=f"User {str(ident)[:6]}",
                is_active=True,
                token_version=1,
            )
        return None

    async def execute(self, stmt: Any) -> Any:
        pass


class FakeSessionFactory:
    @asynccontextmanager
    async def __call__(self):
        yield FakeSession()


async def fake_get_session():
    yield FakeSession()


class FakeRedis:
    """Giả lập Redis có hỗ trợ get, incr, expire, publish, pubsub."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.published_messages: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def set(self, key: str, value: str, *args, **kwargs) -> None:
        self.store[key] = str(value)

    async def incr(self, key: str) -> int:
        val = int(self.store.get(key, "0")) + 1
        self.store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def publish(self, channel: str, message: str) -> int:
        self.published_messages.append((channel, message))
        return 1

    def pubsub(self):
        fake_r = self

        class FakePubSub:
            async def subscribe(self, channel: str):
                self.channel = channel

            async def listen(self):
                # Phát các message đã được lưu trong published_messages
                for ch, msg in fake_r.published_messages:
                    if ch == getattr(self, "channel", ""):
                        yield {"type": "message", "data": msg}

            async def unsubscribe(self, channel: str):
                pass

            async def close(self):
                pass

        return FakePubSub()


# Helper tạo token
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


@pytest.fixture(autouse=True)
def setup_teardown_isolation(monkeypatch):
    """Cô lập test environment và dependency overrides."""
    docs_db.clear()
    jobs_db.clear()
    quizzes_db.clear()
    questions_db.clear()

    fake_redis = FakeRedis()
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = fake_get_session
    app.dependency_overrides[get_redis] = lambda: fake_redis

    # Monkeypatch repositories
    async def mock_create_doc(session, **kwargs):
        doc = Document(**kwargs)
        doc.id = doc.id or uuid.uuid4()
        docs_db[doc.id] = doc
        return doc

    async def mock_create_job(session, **kwargs):
        from datetime import UTC, datetime

        job = AiJob(**kwargs)
        job.id = job.id or uuid.uuid4()
        job.status = "queued"
        job.input_tokens = 0
        job.output_tokens = 0
        job.attempts = 0
        job.cache_hit = False
        job.created_at = datetime.now(UTC)
        jobs_db[job.id] = job
        return job

    async def mock_get_job(session, job_id):
        job = jobs_db.get(job_id)
        if job and job.document_id and job.document_id in docs_db:
            job.document = docs_db[job.document_id]
        return job

    async def mock_update_job(session, job_id, **kwargs):
        job = jobs_db.get(job_id)
        if not job:
            return None
        for k, v in kwargs.items():
            if k == "finished" and v:
                from datetime import UTC, datetime

                job.finished_at = datetime.now(UTC)
            elif hasattr(job, k):
                setattr(job, k, v)
        return job

    async def mock_find_cached(session, sha256, params_hash):
        for j in jobs_db.values():
            if j.status == "succeeded":
                doc = docs_db.get(j.document_id)
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

    # Mock ARQ pool enqueue
    async def mock_enqueue(*args, **kwargs):
        return True

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = mock_enqueue
    monkeypatch.setattr(ai_job_service, "get_arq_redis_pool", AsyncMock(return_value=mock_pool))

    yield fake_redis

    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)


# =========================================================================
# Test Cases
# =========================================================================


def test_upload_pdf_success_and_timing_under_500ms(setup_teardown_isolation):
    """Tiêu chuẩn Thứ Tư: Upload PDF trả HTTP 202 Accepted trong < 500 ms."""
    client = TestClient(app)
    token = make_token(HOST_A_ID)
    pdf_bytes = generate_pdf_bytes(num_pages=3)

    files = {"file": ("lecture_slides.pdf", pdf_bytes, "application/pdf")}
    data = {
        "num_questions": 5,
        "difficulty": "medium",
        "language": "vi",
        "question_types": "single",
        "notes": "Tập trung vào chương 1",
    }

    t_start = time.perf_counter()
    response = client.post(
        "/api/v1/ai/quiz-generations",
        files=files,
        data=data,
        headers={"Authorization": f"Bearer {token}"},
    )
    elapsed_s = time.perf_counter() - t_start

    assert response.status_code == 202
    assert elapsed_s < 0.5, f"Thời gian xử lý upload ({elapsed_s:.3f}s) vượt quá 500 ms!"

    res_data = response.json()
    assert "job_id" in res_data
    assert res_data["status"] == "queued"
    assert "document_id" in res_data


def test_upload_parameter_validation_422():
    """FR-AIGEN-02: Tham số ngoài khoảng (num_questions < 5 hoặc > 20, notes > 300) trả 422."""
    client = TestClient(app)
    token = make_token(HOST_A_ID)
    pdf_bytes = generate_pdf_bytes(num_pages=2)

    # 1. num_questions = 25 (> 20) -> 422
    res_large = client.post(
        "/api/v1/ai/quiz-generations",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        data={"num_questions": 25},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_large.status_code == 422

    # 2. num_questions = 3 (< 5) -> 422
    res_small = client.post(
        "/api/v1/ai/quiz-generations",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        data={"num_questions": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_small.status_code == 422

    # 3. notes > 300 ký tự -> 422
    res_long_notes = client.post(
        "/api/v1/ai/quiz-generations",
        files={"file": ("test.pdf", pdf_bytes, "application/pdf")},
        data={"num_questions": 5, "notes": "x" * 305},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_long_notes.status_code == 422


def test_host_daily_quota_limit_429(setup_teardown_isolation):
    """FR-AIGEN-13: Host vượt quá N lần sinh/ngày trả 429 kèm Retry-After và reset_at."""
    client = TestClient(app)
    fake_redis = setup_teardown_isolation
    token = make_token(HOST_A_ID)
    pdf_bytes = generate_pdf_bytes(num_pages=1)

    # Giả lập host A đã thực hiện 5 lần sinh trong ngày
    from app.services.quota_service import get_pacific_today

    today_str, _, _ = get_pacific_today()
    quota_key = f"ai:quota:host:{HOST_A_ID}:{today_str}"
    fake_redis.store[quota_key] = "5"

    response = client.post(
        "/api/v1/ai/quiz-generations",
        files={"file": ("quota_test.pdf", pdf_bytes, "application/pdf")},
        data={"num_questions": 5},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.json()["error"]["code"] == "HOST_DAILY_QUOTA_EXCEEDED"
    assert "reset_at" in response.json()["error"]["details"][0]


def test_host_b_accessing_host_a_job_gets_404():
    """FR-AUTHZ-03 & BOLA: Host B truy cập Job của Host A (chi tiết & SSE) nhận 404."""
    client = TestClient(app)
    token_a = make_token(HOST_A_ID)
    token_b = make_token(HOST_B_ID)
    pdf_bytes = generate_pdf_bytes(num_pages=2)

    # Host A tạo job
    upload_res = client.post(
        "/api/v1/ai/quiz-generations",
        files={"file": ("doc_a.pdf", pdf_bytes, "application/pdf")},
        data={"num_questions": 5},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    job_id = upload_res.json()["job_id"]

    # Host A truy cập -> 200 OK
    res_a = client.get(
        f"/api/v1/ai-jobs/{job_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert res_a.status_code == 200
    assert res_a.json()["id"] == job_id

    # Host B truy cập chi tiết -> 404 Not Found
    res_b_detail = client.get(
        f"/api/v1/ai-jobs/{job_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b_detail.status_code == 404

    # Host B truy cập luồng SSE -> 404 Not Found
    res_b_sse = client.get(
        f"/api/v1/ai-jobs/{job_id}/events",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert res_b_sse.status_code == 404


@pytest.mark.asyncio
async def test_worker_generate_quiz_task_and_sse_events(setup_teardown_isolation):
    """Kiểm tra ARQ Worker xử lý job và chuỗi sự kiện SSE: queued -> running -> succeeded."""
    fake_redis = setup_teardown_isolation

    # Tạo tệp PDF thực tế trong thư mục tạm
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
        pdf_bytes = generate_pdf_bytes(num_pages=5)
        tmp_file.write(pdf_bytes)
        tmp_path = tmp_file.name

    doc = Document(
        id=uuid.uuid4(),
        uploaded_by=HOST_A_ID,
        filename="lecture.pdf",
        mime="application/pdf",
        size_bytes=len(pdf_bytes),
        pages=5,
        sha256="testsha256",
        storage_path=tmp_path,
    )
    docs_db[doc.id] = doc

    job = AiJob(
        id=uuid.uuid4(),
        kind="quiz_generation",
        status="queued",
        created_by=HOST_A_ID,
        document_id=doc.id,
        params={"num_questions": 5, "difficulty": "medium", "language": "vi"},
    )
    jobs_db[job.id] = job

    # Giả lập worker context với FakeGemini
    fake_gateway = FakeGemini()
    worker_ctx = {
        "session_factory": FakeSessionFactory(),
        "redis": fake_redis,
        "gateway": fake_gateway,
    }

    # Chạy worker task
    task_res = await generate_quiz_task(worker_ctx, str(job.id))
    assert task_res["status"] == "succeeded"
    assert "quiz_id" in task_res

    # Kiểm tra trạng thái trong jobs_db
    updated_job = jobs_db[job.id]
    assert updated_job.status == "succeeded"
    assert updated_job.result is not None
    assert updated_job.finished_at is not None

    # Kiểm tra các message đã được phát lên Redis pub/sub
    published_events = [
        json.loads(msg) for ch, msg in fake_redis.published_messages if ch == f"ai:job:{job.id}"
    ]
    statuses = [e["status"] for e in published_events]
    assert "running" in statuses
    assert "succeeded" in statuses

    # Dùng TestClient đọc luồng SSE
    client = TestClient(app)
    token_a = make_token(HOST_A_ID)
    sse_res = client.get(
        f"/api/v1/ai-jobs/{job.id}/events",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert sse_res.status_code == 200
    assert "text/event-stream" in sse_res.headers["content-type"]
    assert "X-Accel-Buffering" in sse_res.headers
    assert sse_res.headers["X-Accel-Buffering"] == "no"
    assert f'"job_id": "{job.id}"' in sse_res.text
    assert '"status": "succeeded"' in sse_res.text
