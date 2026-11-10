"""Kiểm thử tính năng Phiên bản hóa Quiz (QZ-4, FR-QUIZ-06).

Đặc tả:
- FR-QUIZ-06: Hệ thống phải tạo phiên bản mới (tăng `version`) thay vì sửa trực tiếp
  khi quiz đã có trận chạy, và các trận cũ vẫn tham chiếu đúng nội dung lúc chơi.
- AC: Given quiz v1 đã có trận · When host sửa nội dung câu 1 · Then tạo v2, dữ liệu
  trận cũ vẫn hiển thị nội dung của v1.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.models.game import GameSession
from app.models.quiz import Question, QuestionOption, Quiz
from app.schemas.quiz import (
    QuestionOptionCreate,
    QuestionUpdate,
    QuizUpdate,
)
from app.services import game_service, quiz_service


class FakeAsyncSession:
    """Session giả lập cho unit tests."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: object, attrs: object = None) -> None:
        pass


@pytest.fixture
def fake_session() -> FakeAsyncSession:
    return FakeAsyncSession()


@pytest.fixture
def sample_quiz_with_questions() -> tuple[Quiz, list[Question]]:
    owner_id = uuid.uuid4()
    quiz_id = uuid.uuid4()
    quiz = Quiz(
        id=quiz_id,
        owner_id=owner_id,
        title="Lịch sử Việt Nam",
        description="Kiểm tra kiến thức",
        visibility="private",
        status="published",
        version=1,
        tags=["lich-su"],
    )

    q1_id = uuid.uuid4()
    q1 = Question(
        id=q1_id,
        quiz_id=quiz_id,
        position=1,
        type="single",
        content="Chiến thắng Điện Biên Phủ diễn ra năm nào?",
        time_limit_s=15,
        points=1000,
    )
    q1.options = [
        QuestionOption(question_id=q1_id, position=1, content="1954", is_correct=True),
        QuestionOption(question_id=q1_id, position=2, content="1975", is_correct=False),
        QuestionOption(question_id=q1_id, position=3, content="1945", is_correct=False),
        QuestionOption(question_id=q1_id, position=4, content="1968", is_correct=False),
    ]

    q2_id = uuid.uuid4()
    q2 = Question(
        id=q2_id,
        quiz_id=quiz_id,
        position=2,
        type="single",
        content="Thủ đô của Việt Nam là gì?",
        time_limit_s=15,
        points=1000,
    )
    q2.options = [
        QuestionOption(question_id=q2_id, position=1, content="Hà Nội", is_correct=True),
        QuestionOption(question_id=q2_id, position=2, content="TP.HCM", is_correct=False),
        QuestionOption(question_id=q2_id, position=3, content="Đà Nẵng", is_correct=False),
        QuestionOption(question_id=q2_id, position=4, content="Huế", is_correct=False),
    ]

    q3_id = uuid.uuid4()
    q3 = Question(
        id=q3_id,
        quiz_id=quiz_id,
        position=3,
        type="true_false",
        content="Việt Nam có 63 tỉnh thành?",
        time_limit_s=10,
        points=1000,
    )
    q3.options = [
        QuestionOption(question_id=q3_id, position=1, content="Đúng", is_correct=True),
        QuestionOption(question_id=q3_id, position=2, content="Sai", is_correct=False),
    ]

    quiz.questions = [q1, q2, q3]
    return quiz, [q1, q2, q3]


@pytest.mark.asyncio
async def test_quiz_version_khong_tang_khi_chua_tung_co_tran_dau(
    fake_session: FakeAsyncSession,
    sample_quiz_with_questions: tuple[Quiz, list[Question]],
) -> None:
    """Khi Quiz chưa từng có trận đấu nào chạy, sửa trực tiếp không tăng version."""
    quiz, questions = sample_quiz_with_questions
    assert quiz.version == 1

    with (
        patch(
            "app.repositories.quiz_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=quiz,
        ),
        patch(
            "app.repositories.quiz_repository.update",
            new_callable=AsyncMock,
            return_value=quiz,
        ),
        patch(
            "app.repositories.game_repository.count_sessions_by_quiz_id",
            new_callable=AsyncMock,
            return_value=0,
        ),
    ):
        updated = await quiz_service.update_quiz(
            fake_session,  # type: ignore[arg-type]
            quiz_id=quiz.id,
            user_id=quiz.owner_id,
            user_roles=["HOST"],
            data=QuizUpdate(title="Tiêu đề mới khi chưa có trận"),
        )
        assert updated.version == 1


@pytest.mark.asyncio
async def test_quiz_version_tang_khi_da_tung_co_tran_dau(
    fake_session: FakeAsyncSession,
    sample_quiz_with_questions: tuple[Quiz, list[Question]],
) -> None:
    """Khi Quiz đã có trận chạy, chỉnh sửa quiz/câu hỏi tự động tăng version (FR-QUIZ-06)."""
    quiz, questions = sample_quiz_with_questions
    assert quiz.version == 1
    q1 = questions[0]

    with (
        patch(
            "app.repositories.quiz_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=quiz,
        ),
        patch(
            "app.repositories.quiz_repository.update",
            new_callable=AsyncMock,
            return_value=quiz,
        ),
        patch(
            "app.repositories.quiz_repository.get_question",
            new_callable=AsyncMock,
            return_value=q1,
        ),
        patch(
            "app.repositories.quiz_repository.replace_question_options",
            new_callable=AsyncMock,
        ),
        patch(
            "app.repositories.game_repository.count_sessions_by_quiz_id",
            new_callable=AsyncMock,
            return_value=1,  # Đã có 1 trận chạy
        ),
    ):
        # 1. Sửa thông tin chung của Quiz -> version tăng lên 2
        await quiz_service.update_quiz(
            fake_session,  # type: ignore[arg-type]
            quiz_id=quiz.id,
            user_id=quiz.owner_id,
            user_roles=["HOST"],
            data=QuizUpdate(title="Lịch sử Việt Nam (Cập nhật v2)"),
        )
        assert quiz.version == 2

        # 2. Sửa nội dung câu hỏi 1 -> version tăng lên 3
        await quiz_service.update_question(
            fake_session,  # type: ignore[arg-type]
            quiz_id=quiz.id,
            question_id=q1.id,
            user_id=quiz.owner_id,
            user_roles=["HOST"],
            data=QuestionUpdate(
                content="Năm diễn ra chiến dịch Điện Biên Phủ?",
                options=[
                    QuestionOptionCreate(content="1954", is_correct=True),
                    QuestionOptionCreate(content="1975", is_correct=False),
                    QuestionOptionCreate(content="1945", is_correct=False),
                    QuestionOptionCreate(content="1968", is_correct=False),
                ],
            ),
        )
        assert quiz.version == 3


@pytest.mark.asyncio
async def test_quiz_snapshot_bat_bien_khi_quiz_bi_sua_sau_do(
    fake_session: FakeAsyncSession,
    sample_quiz_with_questions: tuple[Quiz, list[Question]],
) -> None:
    """AC kiểm chứng: Given quiz v1 đã có trận · When host sửa nội dung câu 1 · Then tạo v2,
    dữ liệu trận cũ vẫn hiển thị nội dung của v1 (QZ-4, FR-QUIZ-06).
    """
    quiz, questions = sample_quiz_with_questions
    assert quiz.version == 1
    q1 = questions[0]
    initial_q1_content = q1.content

    # Giả lập Host mở trận với quiz v1 -> snapshot được tạo
    v1_snapshot = game_service.build_quiz_snapshot(quiz)
    session_id = uuid.uuid4()
    game_session = GameSession(
        id=session_id,
        host_id=quiz.owner_id,
        quiz_id=quiz.id,
        pin="123456",
        status="finished",
        quiz_snapshot=v1_snapshot,
    )

    # Kiểm tra snapshot ban đầu của trận giữ đúng nội dung v1
    assert game_session.quiz_snapshot["version"] == 1
    assert game_session.quiz_snapshot["questions"][0]["content"] == initial_q1_content

    # Bây giờ Host sửa nội dung câu 1
    with (
        patch(
            "app.repositories.quiz_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=quiz,
        ),
        patch(
            "app.repositories.quiz_repository.get_question",
            new_callable=AsyncMock,
            return_value=q1,
        ),
        patch(
            "app.repositories.quiz_repository.replace_question_options",
            new_callable=AsyncMock,
        ),
        patch(
            "app.repositories.game_repository.count_sessions_by_quiz_id",
            new_callable=AsyncMock,
            return_value=1,  # Đã có trận game_session ở trên
        ),
    ):
        await quiz_service.update_question(
            fake_session,  # type: ignore[arg-type]
            quiz_id=quiz.id,
            question_id=q1.id,
            user_id=quiz.owner_id,
            user_roles=["HOST"],
            data=QuestionUpdate(content="Nội dung đã bị sửa hoàn toàn ở v2!"),
        )

    # 1. Quiz gốc đã được tăng version lên 2 và câu 1 mang nội dung mới
    assert quiz.version == 2
    assert q1.content == "Nội dung đã bị sửa hoàn toàn ở v2!"

    # 2. Nhưng dữ liệu trận cũ (game_session.quiz_snapshot) VẪN NGUYÊN VẸN nội dung v1!
    assert game_session.quiz_snapshot["version"] == 1
    assert game_session.quiz_snapshot["questions"][0]["content"] == initial_q1_content
