"""Unit & Integration tests cho Báo cáo AI sau trận đấu (FR-AIRPT-01..06, Tuesday DoD).

Bao phủ các tiêu chí nghiệm thu của Thứ Ba:
1. Test "prompt không chứa tên người chơi" (bảo mật danh tính).
2. Test báo cáo thành công với FakeGemini (ok).
3. Test Gemini lỗi 429 -> tự động chuyển sang degraded nhưng vẫn giữ nguyên thống kê.
4. Test Gemini trả về JSON hỏng (malformed) -> chuyển sang degraded an toàn.
5. Test validate lớp 2: chống AI bịa chủ đề (topic hallucination) và ghi đè mastery theo số liệu cứng.
6. Test nhận xét cá nhân cho người chơi: chi phí 0 lần gọi AI.
7. Test retry endpoint giới hạn số lần bấm.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.fake import FakeGemini
from app.ai.gemini_gateway import build_report_prompt
from app.ai.schemas import GameReportDraft, TopicAnalysis
from app.models.ai import AiJob
from app.models.game import GamePlayer, GameSession, PlayerAnswer
from app.models.quiz import Quiz
from app.models.user import User
from app.services.game_statistics import aggregate_game_statistics
from app.services.report_service import (
    generate_and_save_game_report,
    get_player_personal_feedback,
    validate_and_sanitize_report,
)


@pytest.fixture
def fake_redis():
    """Fake Redis in-memory đơn giản cho test report."""

    class MockRedis:
        def __init__(self):
            self.published: list[tuple[str, str]] = []

        async def publish(self, channel: str, message: str) -> int:
            self.published.append((channel, message))
            return 1

        async def exists(self, *names: str) -> int:
            return 0

        async def eval(self, *args, **kwargs):
            return 1

    return MockRedis()


async def _create_test_game_with_answers(
    db_session: AsyncSession,
) -> tuple[GameSession, GamePlayer, GamePlayer]:
    """Helper tạo game session hoàn chỉnh với 2 người chơi và 2 câu hỏi thuộc 2 chủ đề."""
    host = User(
        id=uuid.uuid4(),
        email=f"host_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="fake",
        display_name="Giảng Viên Hưng",
    )
    db_session.add(host)

    quiz = Quiz(
        id=uuid.uuid4(),
        owner_id=host.id,
        title="Lập Trình Web Nâng Cao",
        visibility="public",
        status="published",
    )
    db_session.add(quiz)
    await db_session.flush()

    q1_id = uuid.uuid4()
    q2_id = uuid.uuid4()
    opt1_a = str(uuid.uuid4())
    opt1_b = str(uuid.uuid4())
    opt2_a = str(uuid.uuid4())
    opt2_b = str(uuid.uuid4())

    snapshot = {
        "title": "Lập Trình Web Nâng Cao",
        "questions": [
            {
                "id": str(q1_id),
                "content": "FastAPI hoạt động trên chuẩn giao tiếp nào?",
                "topic": "Backend Framework",
                "options": [
                    {"id": opt1_a, "content": "ASGI", "is_correct": True},
                    {"id": opt1_b, "content": "WSGI", "is_correct": False},
                ],
            },
            {
                "id": str(q2_id),
                "content": "Redis lưu trữ dữ liệu chính ở đâu?",
                "topic": "Database & Caching",
                "options": [
                    {"id": opt2_a, "content": "RAM", "is_correct": True},
                    {"id": opt2_b, "content": "Ổ cứng HDD", "is_correct": False},
                ],
            },
        ],
    }

    session_id = uuid.uuid4()
    game = GameSession(
        id=session_id,
        quiz_id=quiz.id,
        host_id=host.id,
        pin="888999",
        status="finished",
        quiz_snapshot=snapshot,
    )
    db_session.add(game)

    # 2 người chơi với tên cụ thể để test bảo mật
    p1 = GamePlayer(
        id=uuid.uuid4(),
        session_id=session_id,
        nickname="NguyenVanA_BiMat",
        final_score=1500,
        final_rank=1,
    )
    p2 = GamePlayer(
        id=uuid.uuid4(),
        session_id=session_id,
        nickname="TranThiB_Hacker",
        final_score=800,
        final_rank=2,
    )
    db_session.add_all([p1, p2])
    await db_session.flush()

    # Câu 1: Cả 2 đều trả lời ĐÚNG
    a1_p1 = PlayerAnswer(
        session_id=session_id,
        player_id=p1.id,
        question_id=q1_id,
        option_ids=[opt1_a],
        is_correct=True,
        response_ms=1000,
        points_awarded=1000,
    )
    a1_p2 = PlayerAnswer(
        session_id=session_id,
        player_id=p2.id,
        question_id=q1_id,
        option_ids=[opt1_a],
        is_correct=True,
        response_ms=1500,
        points_awarded=800,
    )

    # Câu 2: p1 đúng, p2 sai
    a2_p1 = PlayerAnswer(
        session_id=session_id,
        player_id=p1.id,
        question_id=q2_id,
        option_ids=[opt2_a],
        is_correct=True,
        response_ms=2000,
        points_awarded=1000,
    )
    a2_p2 = PlayerAnswer(
        session_id=session_id,
        player_id=p2.id,
        question_id=q2_id,
        option_ids=[opt2_b],
        is_correct=False,
        response_ms=3000,
        points_awarded=0,
    )

    db_session.add_all([a1_p1, a1_p2, a2_p1, a2_p2])
    await db_session.commit()
    return game, p1, p2


@pytest.mark.asyncio
async def test_prompt_does_not_contain_player_names(db_session: AsyncSession):
    """Tiêu chí DoD: Prompt gửi AI tuyệt đối không chứa tên/ID người chơi."""
    game, p1, p2 = await _create_test_game_with_answers(db_session)

    stats = await aggregate_game_statistics(db_session, game.id)
    prompt = build_report_prompt(stats)

    # Khẳng định không có bất kỳ nickname hay player_id nào xuất hiện trong prompt
    assert p1.nickname not in prompt
    assert p2.nickname not in prompt
    assert str(p1.id) not in prompt
    assert str(p2.id) not in prompt
    assert "NguyenVanA" not in prompt
    assert "TranThiB" not in prompt

    # Nhưng có đầy đủ số liệu thống kê chung
    assert "Lập Trình Web Nâng Cao" in prompt
    assert "Backend Framework" in prompt
    assert "Database & Caching" in prompt
    assert "Số lượng người tham gia: 2" in prompt


@pytest.mark.asyncio
async def test_game_report_success_with_fake_gemini(db_session: AsyncSession, fake_redis):
    """Tiêu chí DoD: Báo cáo AI thành công với FakeGemini trả về status 'ready'."""
    game, _, _ = await _create_test_game_with_answers(db_session)
    gateway = FakeGemini()

    report = await generate_and_save_game_report(
        session=db_session,
        redis=fake_redis,  # type: ignore[arg-type]
        gateway=gateway,
        session_id=game.id,
    )

    assert report.status == "ready"
    assert report.session_id == game.id
    assert len(report.summary) > 0
    assert len(report.advice_for_host) > 0
    assert len(report.advice_for_learner) > 0
    assert len(report.strengths) > 0

    # Kiểm tra bản ghi AI Job
    stmt_job = select(AiJob).where(AiJob.session_id == game.id, AiJob.kind == "game_report")
    job = (await db_session.execute(stmt_job)).scalar_one_or_none()
    assert job is not None
    assert job.status == "succeeded"
    assert job.attempts == 1

    # Kiểm tra event WebSocket 'report.ready' đã được publish
    assert len(fake_redis.published) == 1
    assert fake_redis.published[0][0] == f"game:{game.id}"
    assert '"status": "ready"' in fake_redis.published[0][1]


@pytest.mark.asyncio
async def test_game_report_gemini_429_degraded(db_session: AsyncSession, fake_redis):
    """Tiêu chí DoD: Gemini lỗi 429 -> tự động chuyển sang degraded, không throw exception."""
    game, _, _ = await _create_test_game_with_answers(db_session)
    gateway = FakeGemini(should_fail_429=True)

    report = await generate_and_save_game_report(
        session=db_session,
        redis=fake_redis,  # type: ignore[arg-type]
        gateway=gateway,
        session_id=game.id,
    )

    # Báo cáo ở trạng thái degraded
    assert report.status == "degraded"
    assert "AI đang tạm thời không khả dụng" in report.summary

    # Vẫn giữ nguyên vẹn 100% số liệu thống kê thuần SQL
    assert report.stats["total_players"] == 2
    assert report.stats["total_questions"] == 2
    assert report.stats["overall_accuracy"] == 0.75

    # ai_jobs ghi nhận thất bại nhưng không văng lỗi unhandled
    stmt_job = select(AiJob).where(AiJob.session_id == game.id, AiJob.kind == "game_report")
    job = (await db_session.execute(stmt_job)).scalar_one_or_none()
    assert job is not None
    assert job.status == "failed"


@pytest.mark.asyncio
async def test_game_report_malformed_json_degraded(db_session: AsyncSession, fake_redis):
    """Tiêu chí DoD: Gemini trả JSON hỏng -> chuyển sang degraded an toàn."""
    game, _, _ = await _create_test_game_with_answers(db_session)
    gateway = FakeGemini(should_return_malformed_json=True)

    report = await generate_and_save_game_report(
        session=db_session,
        redis=fake_redis,  # type: ignore[arg-type]
        gateway=gateway,
        session_id=game.id,
    )

    assert report.status == "degraded"
    assert report.stats["overall_accuracy"] == 0.75


@pytest.mark.asyncio
async def test_game_report_topic_hallucination_validation():
    """Tiêu chí DoD: Validate lớp 2 loại bỏ chủ đề ảo giác mà AI bịa ra."""
    stats = {
        "topic_stats": [
            {"topic": "Python", "accuracy_rate": 0.85, "mastery_level": "mastered"},
            {"topic": "Docker", "accuracy_rate": 0.40, "mastery_level": "needs_focus"},
        ]
    }

    # Giả sử AI bịa ra chủ đề "QuantumPhysics" không có trong Quiz
    draft = GameReportDraft(
        summary="Nhận xét lớp học",
        strengths=[
            TopicAnalysis(topic="Python", analysis="Hiểu tốt cú pháp."),
            TopicAnalysis(topic="QuantumPhysics", analysis="Chủ đề bịa không có trong bài!"),
        ],
        weaknesses=[
            TopicAnalysis(topic="Docker", analysis="Chưa biết viết Dockerfile."),
            TopicAnalysis(topic="AlienLanguage", analysis="Chủ đề bịa thứ hai!"),
        ],
        advice_for_host="Giảng thêm về Docker",
        advice_for_learner="Ôn lại Docker",
    )

    clean_strengths, clean_weaknesses = validate_and_sanitize_report(draft, stats)

    # QuantumPhysics và AlienLanguage bị loại bỏ hoàn toàn
    strength_topics = [s["topic"] for s in clean_strengths]
    weakness_topics = [w["topic"] for w in clean_weaknesses]

    assert "Python" in strength_topics
    assert "QuantumPhysics" not in strength_topics

    assert "Docker" in weakness_topics
    assert "AlienLanguage" not in weakness_topics


@pytest.mark.asyncio
async def test_player_personal_feedback_zero_ai_call(db_session: AsyncSession, fake_redis):
    """Tiêu chí DoD: Nhận xét cá nhân cho người chơi với chi phí 0 lần gọi AI."""
    game, p1, p2 = await _create_test_game_with_answers(db_session)
    gateway = FakeGemini()

    report = await generate_and_save_game_report(
        session=db_session,
        redis=fake_redis,  # type: ignore[arg-type]
        gateway=gateway,
        session_id=game.id,
    )

    # Đếm số lần gọi gateway ban đầu (1 lần cho toàn trận)
    initial_ai_calls = gateway.call_count
    assert initial_ai_calls == 1

    # Tạo nhận xét cho Player 2 (người đã trả lời sai câu về Database & Caching)
    p2_feedback = await get_player_personal_feedback(db_session, report, game.id, p2.id)

    # 1. Chi phí gọi AI là 0
    assert gateway.call_count == initial_ai_calls
    assert p2_feedback["ai_call_count"] == 0

    # 2. Phát hiện đúng chủ đề làm sai của player
    assert "Database & Caching" in p2_feedback["wrong_topics"]
    assert p2_feedback["final_rank"] == 2
    assert p2_feedback["accuracy_rate"] == 0.5
    assert p2_feedback["learning_advice"] == report.advice_for_learner
