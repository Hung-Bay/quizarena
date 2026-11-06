"""Kiểm thử tính năng Clone Quiz, Danh sách Public Quizzes (QZ-5) và Sinh lại 1 câu hỏi (FR-AIGEN-09).

Tuân thủ tiêu chí Thứ Sáu Phase 2:
- GET /quizzes/public: Xem danh sách các quiz public & published, lọc theo tag, tìm kiếm theo tiêu đề.
- POST /quizzes/{id}/clone:
  - Host A clone quiz của chính mình -> Thành công, tạo bản nháp mới (draft, version 1, play_count 0).
  - Host B clone quiz public của Host A -> Thành công, tạo bản nháp thuộc sở hữu của Host B.
  - Host B clone quiz private/draft của Host A -> Trả về 404 Not Found (BOLA protection - FR-AUTHZ-03).
- POST /quizzes/{id}/questions/{qid}/regenerate:
  - Host yêu cầu sinh lại 1 câu hỏi cụ thể -> Gateway sinh câu mới, validate QZ-2, cập nhật đè lên question cũ.
  - Host B gọi sinh lại câu hỏi của Host A -> Trả về 404 Not Found.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.ai.fake import FakeGemini
from app.api.deps import get_gemini_gateway, get_redis, get_session
from app.core import security
from app.core.rbac import ROLE_PERMISSIONS, RoleName
from app.main_api import app
from app.models.quiz import Question, QuestionOption, Quiz
from app.repositories import quiz_repository
from app.services import gemini_rate_limiter


class FakeRedisForQuiz:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, *args: Any, **kwargs: Any) -> None:
        pass

    async def exists(self, key: str) -> int:
        return 0

    async def incr(self, key: str) -> int:
        return 1

    async def expire(self, key: str, seconds: int) -> bool:
        return True

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


@pytest.fixture(autouse=True)
def setup_test_isolation(monkeypatch: pytest.MonkeyPatch):
    """Cô lập in-memory database và dependency overrides cho quiz."""
    quizzes_db: dict[uuid.UUID, Quiz] = {}
    questions_db: dict[uuid.UUID, Question] = {}
    options_db: dict[uuid.UUID, QuestionOption] = {}

    fake_redis = FakeRedisForQuiz()
    fake_gateway = FakeGemini()

    class MockDbSession:
        def begin(self):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _ctx():
                yield self

            return _ctx()

        def add(self, obj: Any) -> None:
            if isinstance(obj, Quiz):
                obj.id = obj.id or uuid.uuid4()
                quizzes_db[obj.id] = obj
            elif isinstance(obj, Question):
                obj.id = obj.id or uuid.uuid4()
                questions_db[obj.id] = obj
                if hasattr(obj, "quiz_id") and obj.quiz_id in quizzes_db:
                    q_list = getattr(quizzes_db[obj.quiz_id], "questions", None)
                    if q_list is not None and obj not in q_list:
                        q_list.append(obj)
            elif isinstance(obj, QuestionOption):
                obj.id = obj.id or uuid.uuid4()
                options_db[obj.id] = obj
                if hasattr(obj, "question_id") and obj.question_id in questions_db:
                    opt_list = getattr(questions_db[obj.question_id], "options", None)
                    if opt_list is not None and obj not in opt_list:
                        opt_list.append(obj)

        async def flush(self) -> None:
            pass

        async def refresh(self, obj: Any, attrs: Any = None) -> None:
            if isinstance(obj, Question) and obj.id in questions_db:
                obj.options = [opt for opt in options_db.values() if opt.question_id == obj.id]

        async def delete(self, obj: Any) -> None:
            if isinstance(obj, Quiz) and obj.id in quizzes_db:
                del quizzes_db[obj.id]

    async def fake_get_session_override():
        yield MockDbSession()

    # Monkeypatch quiz_repository
    async def mock_get_by_id(session: Any, qid: uuid.UUID, **kwargs: Any) -> Quiz | None:
        quiz = quizzes_db.get(qid)
        if quiz:
            q_list = [q for q in questions_db.values() if q.quiz_id == qid]
            for q in q_list:
                q.options = [opt for opt in options_db.values() if opt.question_id == q.id]
            quiz.questions = q_list
        return quiz

    async def mock_get_question(
        session: Any, qid: uuid.UUID, quiz_id: uuid.UUID | None = None
    ) -> Question | None:
        q = questions_db.get(qid)
        if q and (quiz_id is None or q.quiz_id == quiz_id):
            q.options = [opt for opt in options_db.values() if opt.question_id == q.id]
            return q
        return None

    async def mock_list_public(
        session: Any,
        *,
        skip: int = 0,
        limit: int = 50,
        tag: str | None = None,
        search: str | None = None,
    ) -> list[Quiz]:
        results = []
        for q in quizzes_db.values():
            if q.visibility == "public" and q.status == "published":
                if tag and tag not in (q.tags or []):
                    continue
                if search and search.lower() not in q.title.lower():
                    continue
                q.questions = [quest for quest in questions_db.values() if quest.quiz_id == q.id]
                results.append(q)
        return results[skip : skip + limit]

    async def mock_replace_options(
        session: Any, question_id: uuid.UUID, options: list[QuestionOption]
    ):
        for opt_id, opt in list(options_db.items()):
            if opt.question_id == question_id:
                del options_db[opt_id]
        for opt in options:
            opt.id = opt.id or uuid.uuid4()
            opt.question_id = question_id
            options_db[opt.id] = opt
        if question_id in questions_db:
            questions_db[question_id].options = [
                opt for opt in options_db.values() if opt.question_id == question_id
            ]

    monkeypatch.setattr(quiz_repository, "get_by_id", mock_get_by_id)
    monkeypatch.setattr(quiz_repository, "get_question", mock_get_question)
    monkeypatch.setattr(quiz_repository, "list_public", mock_list_public)
    monkeypatch.setattr(quiz_repository, "replace_question_options", mock_replace_options)
    monkeypatch.setattr(gemini_rate_limiter, "acquire_gemini_permit", AsyncMock(return_value=True))

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = fake_get_session_override
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_gemini_gateway] = lambda: fake_gateway

    yield {
        "quizzes_db": quizzes_db,
        "questions_db": questions_db,
        "options_db": options_db,
        "fake_gateway": fake_gateway,
    }

    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)


def test_list_public_quizzes_filtering(setup_test_isolation: dict[str, Any]) -> None:
    """Kiểm tra GET /quizzes/public chỉ trả về quiz public & published, hỗ trợ tìm kiếm và tag."""
    client = TestClient(app)
    quizzes = setup_test_isolation["quizzes_db"]

    # Tạo 3 quiz với các trạng thái khác nhau
    q_public = Quiz(
        id=uuid.uuid4(),
        owner_id=HOST_A_ID,
        title="Lịch sử Việt Nam đại cương",
        visibility="public",
        status="published",
        tags=["lich-su", "dai-cuong"],
    )
    q_private = Quiz(
        id=uuid.uuid4(),
        owner_id=HOST_A_ID,
        title="Đề thi riêng tư",
        visibility="private",
        status="published",
        tags=["lich-su"],
    )
    q_draft = Quiz(
        id=uuid.uuid4(),
        owner_id=HOST_A_ID,
        title="Đề nháp chưa xuất bản",
        visibility="public",
        status="draft",
        tags=["lich-su"],
    )
    quizzes[q_public.id] = q_public
    quizzes[q_private.id] = q_private
    quizzes[q_draft.id] = q_draft

    # 1. Truy vấn danh sách public
    resp = client.get("/api/v1/quizzes/public")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Lịch sử Việt Nam đại cương"

    # 2. Tìm kiếm theo từ khóa
    resp_search = client.get("/api/v1/quizzes/public?search=Việt Nam")
    assert resp_search.status_code == 200
    assert len(resp_search.json()) == 1

    resp_search_empty = client.get("/api/v1/quizzes/public?search=Toán học")
    assert resp_search_empty.status_code == 200
    assert len(resp_search_empty.json()) == 0

    # 3. Lọc theo tag
    resp_tag = client.get("/api/v1/quizzes/public?tag=lich-su")
    assert resp_tag.status_code == 200
    assert len(resp_tag.json()) == 1


def test_clone_quiz_success_and_bola_404(setup_test_isolation: dict[str, Any]) -> None:
    """Kiểm tra clone quiz của chính mình, clone quiz public của người khác, và BOLA 404 khi clone quiz private."""
    client = TestClient(app)
    quizzes = setup_test_isolation["quizzes_db"]
    questions = setup_test_isolation["questions_db"]
    options = setup_test_isolation["options_db"]

    token_a = make_token(HOST_A_ID)
    token_b = make_token(HOST_B_ID)

    # Tạo Quiz A (public & published) có 1 câu hỏi
    quiz_a = Quiz(
        id=uuid.uuid4(),
        owner_id=HOST_A_ID,
        title="Quiz Gốc của Host A",
        description="Mô tả gốc",
        visibility="public",
        status="published",
        tags=["python", "test"],
    )
    quizzes[quiz_a.id] = quiz_a

    q1 = Question(
        id=uuid.uuid4(),
        quiz_id=quiz_a.id,
        position=1,
        type="single",
        content="Python là gì?",
        points=1000,
        time_limit_s=20,
    )
    questions[q1.id] = q1

    opt1 = QuestionOption(
        id=uuid.uuid4(),
        question_id=q1.id,
        position=1,
        content="Ngôn ngữ lập trình",
        is_correct=True,
    )
    opt2 = QuestionOption(
        id=uuid.uuid4(), question_id=q1.id, position=2, content="Một loài rắn", is_correct=False
    )
    options[opt1.id] = opt1
    options[opt2.id] = opt2

    # 1. Host B clone quiz public của Host A -> Thành công, tạo bản sao draft thuộc Host B
    resp_clone = client.post(
        f"/api/v1/quizzes/{quiz_a.id}/clone",
        json={"title": "Quiz nhân bản của Host B"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_clone.status_code == 201
    data_cloned = resp_clone.json()
    assert data_cloned["title"] == "Quiz nhân bản của Host B"
    assert data_cloned["owner_id"] == str(HOST_B_ID)
    assert data_cloned["status"] == "draft"
    assert data_cloned["visibility"] == "private"
    assert len(data_cloned["questions"]) == 1
    assert data_cloned["questions"][0]["content"] == "Python là gì?"

    # 2. Tạo Quiz Private của Host A
    quiz_private = Quiz(
        id=uuid.uuid4(),
        owner_id=HOST_A_ID,
        title="Quiz Private của Host A",
        visibility="private",
        status="draft",
    )
    quizzes[quiz_private.id] = quiz_private

    # Host B cố clone quiz private của Host A -> Bị từ chối 404 (BOLA)
    resp_bola = client.post(
        f"/api/v1/quizzes/{quiz_private.id}/clone",
        json={},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_bola.status_code == 404

    # 3. Host A clone chính quiz private của mình -> Thành công
    resp_owner_clone = client.post(
        f"/api/v1/quizzes/{quiz_private.id}/clone",
        json={"title": "Quiz A bản sao"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_owner_clone.status_code == 201
    assert resp_owner_clone.json()["title"] == "Quiz A bản sao"


def test_regenerate_question_flow(setup_test_isolation: dict[str, Any]) -> None:
    """Tiêu chí FR-AIGEN-09: Host yêu cầu sinh lại 1 câu hỏi cụ thể trong Quiz."""
    client = TestClient(app)
    quizzes = setup_test_isolation["quizzes_db"]
    questions = setup_test_isolation["questions_db"]
    options = setup_test_isolation["options_db"]

    token_a = make_token(HOST_A_ID)
    token_b = make_token(HOST_B_ID)

    quiz = Quiz(
        id=uuid.uuid4(),
        owner_id=HOST_A_ID,
        title="Quiz cần sửa",
        visibility="private",
        status="draft",
    )
    quizzes[quiz.id] = quiz

    q_old = Question(
        id=uuid.uuid4(),
        quiz_id=quiz.id,
        position=1,
        type="single",
        content="Câu hỏi cũ cần thay thế",
        points=1000,
        time_limit_s=20,
    )
    questions[q_old.id] = q_old

    opt1 = QuestionOption(
        id=uuid.uuid4(), question_id=q_old.id, position=1, content="A cũ", is_correct=True
    )
    opt2 = QuestionOption(
        id=uuid.uuid4(), question_id=q_old.id, position=2, content="B cũ", is_correct=False
    )
    options[opt1.id] = opt1
    options[opt2.id] = opt2

    # 1. Host A yêu cầu sinh lại câu hỏi với độ khó hard và notes
    resp_regen = client.post(
        f"/api/v1/quizzes/{quiz.id}/questions/{q_old.id}/regenerate",
        json={
            "notes": "Làm câu hỏi nâng cao hơn",
            "difficulty": "hard",
            "type": "single",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_regen.status_code == 200
    data_regen = resp_regen.json()
    assert "Câu hỏi sinh lại" in data_regen["content"]
    assert data_regen["ai_generated"] is True
    assert len(data_regen["options"]) == 4

    # 2. Host B cố tình sinh lại câu hỏi của Host A -> 404 BOLA
    resp_bola = client.post(
        f"/api/v1/quizzes/{quiz.id}/questions/{q_old.id}/regenerate",
        json={},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_bola.status_code == 404
