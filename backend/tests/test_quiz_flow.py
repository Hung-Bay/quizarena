"""Kiểm thử toàn diện luồng CRUD Quiz, Phân quyền Ownership (Host B -> 404), Xuất bản và Sắp xếp thứ tự.

Tuân thủ nghiêm ngặt tiêu chí Thứ Ba (Image 4 & SRS):
- Host B truy cập quiz của Host A -> 404 (đọc, sửa, xóa, xuất bản, thêm câu, sửa câu, xóa câu, reorder) (FR-AUTHZ-03)
- Xuất bản quiz 2 câu -> 422 (FR-QUIZ-05, QZ-3)
- Xuất bản quiz >= 3 câu hợp lệ -> 200, status="published"
- Thêm câu hỏi vi phạm luật QZ-2 -> 422
- Sắp xếp lại thứ tự câu hỏi (reorder) -> vị trí và danh sách câu hỏi cập nhật đúng thứ tự mới (FR-QUIZ-04)
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_redis, get_session
from app.core import security
from app.core.rbac import ROLE_PERMISSIONS, RoleName
from app.main_api import app
from app.models.quiz import Question, QuestionOption, Quiz

# =========================================================================
# In-memory Repository & Fake DB
# =========================================================================

quizzes_db: dict[uuid.UUID, Quiz] = {}
questions_db: dict[uuid.UUID, Question] = {}


class FakeSession:
    """Giả lập AsyncSession của SQLAlchemy với async with session.begin()."""

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

    async def delete(self, obj: Any) -> None:
        pass

    def add(self, obj: Any) -> None:
        pass

    async def execute(self, stmt: Any) -> Any:
        pass


async def fake_get_session():
    yield FakeSession()


fake_redis = AsyncMock()
fake_redis.exists.return_value = False

client = TestClient(app)


@pytest.fixture(autouse=True)
def _setup_mock_repo(monkeypatch):
    """Giả lập các hàm repository bằng in-memory dict và cô lập dependency_overrides."""
    quizzes_db.clear()
    questions_db.clear()

    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = fake_get_session
    app.dependency_overrides[get_redis] = lambda: fake_redis

    from app.services import quiz_service

    async def _create(session, quiz: Quiz) -> Quiz:
        from datetime import UTC, datetime

        quiz.id = quiz.id or uuid.uuid4()
        quiz.created_at = quiz.created_at or datetime.now(UTC)
        quiz.updated_at = quiz.updated_at or datetime.now(UTC)
        quiz.questions = []
        quizzes_db[quiz.id] = quiz
        return quiz

    async def _get_by_id(
        session, quiz_id: uuid.UUID, include_questions: bool = True
    ) -> Quiz | None:
        return quizzes_db.get(quiz_id)

    async def _list_by_owner(session, owner_id: uuid.UUID, skip: int = 0, limit: int = 50):
        return [q for q in quizzes_db.values() if q.owner_id == owner_id]

    async def _update(session, quiz: Quiz) -> Quiz:
        quizzes_db[quiz.id] = quiz
        return quiz

    async def _delete_quiz(session, quiz: Quiz) -> None:
        quizzes_db.pop(quiz.id, None)

    async def _get_max_question_position(session, quiz_id: uuid.UUID) -> int:
        quiz = quizzes_db.get(quiz_id)
        if not quiz or not quiz.questions:
            return 0
        return max(q.position for q in quiz.questions)

    async def _get_question(
        session, question_id: uuid.UUID, quiz_id: uuid.UUID | None = None
    ) -> Question | None:
        return questions_db.get(question_id)

    async def _add_question(session, question: Question) -> Question:
        from datetime import UTC, datetime

        question.id = question.id or uuid.uuid4()
        question.created_at = question.created_at or datetime.now(UTC)
        for opt in question.options:
            opt.id = opt.id or uuid.uuid4()
            opt.question_id = question.id
        questions_db[question.id] = question
        quiz = quizzes_db.get(question.quiz_id)
        if quiz:
            quiz.questions.append(question)
        return question

    async def _delete_question(session, question: Question) -> None:
        questions_db.pop(question.id, None)
        quiz = quizzes_db.get(question.quiz_id)
        if quiz and question in quiz.questions:
            quiz.questions.remove(question)
            for idx, q in enumerate(quiz.questions, start=1):
                q.position = idx

    async def _reorder_questions(
        session, quiz_id: uuid.UUID, question_ids: list[uuid.UUID]
    ) -> None:
        quiz = quizzes_db.get(quiz_id)
        if quiz:
            q_map = {q.id: q for q in quiz.questions}
            new_ordered = []
            for pos, qid in enumerate(question_ids, start=1):
                if qid in q_map:
                    q_map[qid].position = pos
                    new_ordered.append(q_map[qid])
            quiz.questions = new_ordered

    async def _replace_question_options(
        session, question_id: uuid.UUID, options: list[QuestionOption]
    ) -> None:
        q = questions_db.get(question_id)
        if q:
            for opt in options:
                opt.id = opt.id or uuid.uuid4()
            q.options = options

    monkeypatch.setattr(quiz_service.repo, "create", _create)
    monkeypatch.setattr(quiz_service.repo, "get_by_id", _get_by_id)
    monkeypatch.setattr(quiz_service.repo, "list_by_owner", _list_by_owner)
    monkeypatch.setattr(quiz_service.repo, "update", _update)
    monkeypatch.setattr(quiz_service.repo, "delete_quiz", _delete_quiz)
    monkeypatch.setattr(quiz_service.repo, "get_max_question_position", _get_max_question_position)
    monkeypatch.setattr(quiz_service.repo, "get_question", _get_question)
    monkeypatch.setattr(quiz_service.repo, "add_question", _add_question)
    monkeypatch.setattr(quiz_service.repo, "delete_question", _delete_question)
    monkeypatch.setattr(quiz_service.repo, "reorder_questions", _reorder_questions)
    monkeypatch.setattr(quiz_service.repo, "replace_question_options", _replace_question_options)

    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)


def make_host_token(user_id: uuid.UUID) -> str:
    perms = [p.value for p in ROLE_PERMISSIONS[RoleName.HOST]]
    return security.create_access_token(
        user_id=user_id,
        roles=[RoleName.HOST.value],
        permissions=perms,
        token_version=0,
    )


# =========================================================================
# TEST CASES
# =========================================================================


def test_crud_quiz_va_cau_hoi():
    host_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_host_token(host_id)}"}

    # 1. Tạo Quiz
    create_payload = {
        "title": "Bộ đề Python nâng cao",
        "description": "Kiểm tra kiến thức FastAPI & SQLAlchemy",
        "visibility": "private",
        "tags": ["python", "fastapi"],
    }
    r = client.post("/api/v1/quizzes", json=create_payload, headers=headers)
    assert r.status_code == 201
    quiz_data = r.json()
    quiz_id = quiz_data["id"]
    assert quiz_data["title"] == "Bộ đề Python nâng cao"
    assert quiz_data["status"] == "draft"
    assert quiz_data["version"] == 1
    assert quiz_data["question_count"] == 0

    # 2. Thêm câu hỏi 1 (single)
    q1_payload = {
        "type": "single",
        "content": "Decorators trong Python là gì?",
        "time_limit_s": 20,
        "points": 1000,
        "explanation": "Decorators dùng để bọc hàm",
        "topic": "Python Core",
        "options": [
            {"content": "Hàm bậc cao nhận hàm khác làm tham số", "is_correct": True},
            {"content": "Một kiểu dữ liệu danh sách", "is_correct": False},
            {"content": "Một package quản lý thư viện", "is_correct": False},
        ],
    }
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q1_payload, headers=headers)
    assert r.status_code == 201
    q1_data = r.json()
    q1_id = q1_data["id"]
    assert q1_data["position"] == 1
    assert len(q1_data["options"]) == 3

    # 3. Thêm câu hỏi 2 (multiple)
    q2_payload = {
        "type": "multiple",
        "content": "Những kiểu dữ liệu nào sau đây là bất biến (immutable)?",
        "time_limit_s": 30,
        "points": 2000,
        "options": [
            {"content": "tuple", "is_correct": True},
            {"content": "frozenset", "is_correct": True},
            {"content": "list", "is_correct": False},
        ],
    }
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q2_payload, headers=headers)
    assert r.status_code == 201
    q2_data = r.json()
    q2_id = q2_data["id"]
    assert q2_data["position"] == 2

    # 4. Thêm câu hỏi 3 (true_false)
    q3_payload = {
        "type": "true_false",
        "content": "Python hỗ trợ lập trình bất đồng bộ qua asyncio?",
        "time_limit_s": 15,
        "points": 500,
        "options": [
            {"content": "Đúng", "is_correct": True},
            {"content": "Sai", "is_correct": False},
        ],
    }
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q3_payload, headers=headers)
    assert r.status_code == 201
    q3_data = r.json()
    q3_id = q3_data["id"]
    assert q3_data["position"] == 3

    # 5. Xem chi tiết Quiz
    r = client.get(f"/api/v1/quizzes/{quiz_id}", headers=headers)
    assert r.status_code == 200
    detail = r.json()
    assert detail["question_count"] == 3
    assert len(detail["questions"]) == 3

    # 6. Sắp xếp lại thứ tự câu hỏi (reorder) từ [1, 2, 3] thành [3, 1, 2]
    reorder_payload = {"question_ids": [q3_id, q1_id, q2_id]}
    r = client.put(
        f"/api/v1/quizzes/{quiz_id}/questions/reorder", json=reorder_payload, headers=headers
    )
    assert r.status_code == 200
    reordered_questions = r.json()["questions"]
    assert reordered_questions[0]["id"] == q3_id
    assert reordered_questions[0]["position"] == 1
    assert reordered_questions[1]["id"] == q1_id
    assert reordered_questions[1]["position"] == 2
    assert reordered_questions[2]["id"] == q2_id
    assert reordered_questions[2]["position"] == 3

    # 7. Sửa một câu hỏi
    update_q_payload = {
        "content": "Decorators trong Python là gì? (Đã cập nhật)",
        "points": 2000,
    }
    r = client.put(
        f"/api/v1/quizzes/{quiz_id}/questions/{q1_id}", json=update_q_payload, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["content"] == "Decorators trong Python là gì? (Đã cập nhật)"
    assert r.json()["points"] == 2000

    # 8. Xóa một câu hỏi
    r = client.delete(f"/api/v1/quizzes/{quiz_id}/questions/{q1_id}", headers=headers)
    assert r.status_code == 204

    # Kiểm tra lại số lượng câu hỏi sau khi xóa
    r = client.get(f"/api/v1/quizzes/{quiz_id}", headers=headers)
    assert r.json()["question_count"] == 2


def test_host_b_truy_cap_quiz_host_a_tra_ve_404():
    """FR-AUTHZ-03: Host B truy cập bất kỳ endpoint nào thuộc quiz của Host A phải nhận 404 NOT FOUND."""
    host_a_id = uuid.uuid4()
    host_b_id = uuid.uuid4()

    token_a = make_host_token(host_a_id)
    token_b = make_host_token(host_b_id)

    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # Host A tạo quiz và 1 câu hỏi
    r = client.post("/api/v1/quizzes", json={"title": "Quiz bí mật của A"}, headers=headers_a)
    assert r.status_code == 201
    quiz_id = r.json()["id"]

    q_payload = {
        "type": "single",
        "content": "Câu hỏi của A?",
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": False},
        ],
    }
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q_payload, headers=headers_a)
    assert r.status_code == 201
    qid = r.json()["id"]

    # --- HOST B THỰC HIỆN CÁC THAO TÁC -> TẤT CẢ PHẢI 404 ---

    # 1. Host B xem quiz của A -> 404
    r = client.get(f"/api/v1/quizzes/{quiz_id}", headers=headers_b)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "QUIZ_NOT_FOUND"

    # 2. Host B sửa quiz của A -> 404
    r = client.patch(
        f"/api/v1/quizzes/{quiz_id}", json={"title": "Hacked Title"}, headers=headers_b
    )
    assert r.status_code == 404

    # 3. Host B xóa quiz của A -> 404
    r = client.delete(f"/api/v1/quizzes/{quiz_id}", headers=headers_b)
    assert r.status_code == 404

    # 4. Host B xuất bản quiz của A -> 404
    r = client.post(f"/api/v1/quizzes/{quiz_id}/publish", headers=headers_b)
    assert r.status_code == 404

    # 5. Host B thêm câu hỏi vào quiz của A -> 404
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q_payload, headers=headers_b)
    assert r.status_code == 404

    # 6. Host B xem câu hỏi của A -> 404
    r = client.get(f"/api/v1/quizzes/{quiz_id}/questions/{qid}", headers=headers_b)
    assert r.status_code == 404

    # 7. Host B sửa câu hỏi của A -> 404
    r = client.put(
        f"/api/v1/quizzes/{quiz_id}/questions/{qid}", json={"content": "Đổi"}, headers=headers_b
    )
    assert r.status_code == 404

    # 8. Host B xóa câu hỏi của A -> 404
    r = client.delete(f"/api/v1/quizzes/{quiz_id}/questions/{qid}", headers=headers_b)
    assert r.status_code == 404

    # 9. Host B reorder câu hỏi của A -> 404
    r = client.put(
        f"/api/v1/quizzes/{quiz_id}/questions/reorder",
        json={"question_ids": [qid]},
        headers=headers_b,
    )
    assert r.status_code == 404


def test_xuat_ban_quiz_duoi_3_cau_bi_422():
    """FR-QUIZ-05: Quiz chỉ có 2 câu hỏi khi xuất bản phải trả về 422 PUBLISH_VALIDATION_FAILED."""
    host_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_host_token(host_id)}"}

    r = client.post("/api/v1/quizzes", json={"title": "Quiz 2 câu"}, headers=headers)
    quiz_id = r.json()["id"]

    q_payload = {
        "type": "single",
        "content": "Câu hỏi?",
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": False},
        ],
    }
    # Thêm 2 câu
    client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q_payload, headers=headers)
    client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q_payload, headers=headers)

    # Thử xuất bản -> Phải bị 422
    r = client.post(f"/api/v1/quizzes/{quiz_id}/publish", headers=headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "PUBLISH_VALIDATION_FAILED"
    assert "ít nhất 3 câu hỏi" in r.json()["error"]["message"]


def test_xuat_ban_quiz_3_cau_hop_le_thanh_cong():
    """FR-QUIZ-05: Quiz có đủ 3 câu hỏi hợp lệ xuất bản thành công -> status 'published'."""
    host_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_host_token(host_id)}"}

    r = client.post("/api/v1/quizzes", json={"title": "Quiz đủ điều kiện"}, headers=headers)
    quiz_id = r.json()["id"]

    q1 = {
        "type": "single",
        "content": "Câu 1?",
        "options": [{"content": "A", "is_correct": True}, {"content": "B", "is_correct": False}],
    }
    q2 = {
        "type": "multiple",
        "content": "Câu 2?",
        "options": [{"content": "A", "is_correct": True}, {"content": "B", "is_correct": True}],
    }
    q3 = {
        "type": "true_false",
        "content": "Câu 3?",
        "options": [
            {"content": "Đúng", "is_correct": True},
            {"content": "Sai", "is_correct": False},
        ],
    }

    client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q1, headers=headers)
    client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q2, headers=headers)
    client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q3, headers=headers)

    # Xuất bản thành công
    r = client.post(f"/api/v1/quizzes/{quiz_id}/publish", headers=headers)
    assert r.status_code == 200
    res_data = r.json()
    assert res_data["status"] == "published"


def test_them_cau_hoi_sai_luat_bi_422():
    """QZ-2: Các vi phạm quy tắc câu hỏi khi thêm/sửa phải trả về 422 VALIDATION_ERROR."""
    host_id = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_host_token(host_id)}"}

    r = client.post("/api/v1/quizzes", json={"title": "Quiz test lỗi"}, headers=headers)
    quiz_id = r.json()["id"]

    # 1. Single nhưng có 2 đáp án đúng
    q_invalid_single = {
        "type": "single",
        "content": "Câu hỏi lỗi?",
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": True},
        ],
    }
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q_invalid_single, headers=headers)
    assert r.status_code == 422
    assert "duy nhất 1 đáp án đúng" in r.json()["error"]["message"]

    # 2. Thời gian trả lời sai (70s > 60s)
    q_invalid_time = {
        "type": "single",
        "content": "Thời gian quá dài?",
        "time_limit_s": 70,
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": False},
        ],
    }
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q_invalid_time, headers=headers)
    assert r.status_code == 422

    # 3. Điểm thưởng không hợp lệ (700)
    q_invalid_points = {
        "type": "single",
        "content": "Điểm lạ?",
        "points": 700,
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": False},
        ],
    }
    r = client.post(f"/api/v1/quizzes/{quiz_id}/questions", json=q_invalid_points, headers=headers)
    assert r.status_code == 422
    assert "Điểm thưởng" in r.json()["error"]["message"]

    # 4. Lựa chọn trùng lặp
    q_duplicate_options = {
        "type": "single",
        "content": "Trùng lặp?",
        "options": [
            {"content": "Hà Nội", "is_correct": True},
            {"content": "hà nội", "is_correct": False},
        ],
    }
    r = client.post(
        f"/api/v1/quizzes/{quiz_id}/questions", json=q_duplicate_options, headers=headers
    )
    assert r.status_code == 422
    assert "trùng lặp" in r.json()["error"]["message"]
