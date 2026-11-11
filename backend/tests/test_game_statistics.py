"""Unit tests cho hàm gộp thống kê trận đấu (game_statistics.py)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game import GamePlayer, GameSession, PlayerAnswer
from app.models.quiz import Quiz
from app.models.user import User
from app.services.game_statistics import aggregate_game_statistics, calculate_mastery


def test_calculate_mastery_thresholds():
    """Kiểm tra các ngưỡng phân loại mức độ thành thạo."""
    assert calculate_mastery(1.0) == "mastered"
    assert calculate_mastery(0.8) == "mastered"
    assert calculate_mastery(0.799) == "developing"
    assert calculate_mastery(0.5) == "developing"
    assert calculate_mastery(0.499) == "needs_focus"
    assert calculate_mastery(0.0) == "needs_focus"


@pytest.mark.asyncio
async def test_aggregate_game_statistics_empty_game(db_session: AsyncSession):
    """Trận đấu chưa có ai trả lời -> trả về thống kê rỗng an toàn, không chia cho 0."""
    user = User(
        id=uuid.uuid4(),
        email=f"host_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="fake",
        display_name="Host",
    )
    db_session.add(user)

    quiz = Quiz(
        id=uuid.uuid4(),
        owner_id=user.id,
        title="Empty Quiz",
        visibility="public",
        status="published",
    )
    db_session.add(quiz)
    await db_session.flush()

    q1_id = str(uuid.uuid4())
    snapshot = {
        "title": "Empty Quiz",
        "questions": [
            {
                "id": q1_id,
                "content": "Câu hỏi số 1?",
                "topic": "Python",
                "options": [{"id": "opt1"}, {"id": "opt2"}],
            }
        ],
    }

    session_id = uuid.uuid4()
    game = GameSession(
        id=session_id,
        quiz_id=quiz.id,
        host_id=user.id,
        pin="123456",
        status="finished",
        quiz_snapshot=snapshot,
    )
    db_session.add(game)
    await db_session.commit()

    stats = await aggregate_game_statistics(db_session, session_id)
    assert stats["total_players"] == 0
    assert stats["total_questions"] == 1
    assert stats["total_answers"] == 0
    assert stats["overall_accuracy"] == 0.0
    assert stats["average_response_ms"] == 0.0
    assert stats["hardest_question"] is None
    assert stats["easiest_question"] is None
    assert len(stats["question_stats"]) == 1
    assert stats["question_stats"][0]["accuracy_rate"] == 0.0


@pytest.mark.asyncio
async def test_aggregate_game_statistics_with_data(db_session: AsyncSession):
    """Trận đấu có 2 người chơi, 3 câu hỏi thuộc 2 chủ đề khác nhau."""
    user = User(
        id=uuid.uuid4(),
        email=f"host_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="fake",
        display_name="Host",
    )
    db_session.add(user)

    quiz = Quiz(
        id=uuid.uuid4(),
        owner_id=user.id,
        title="Tech Quiz",
        visibility="public",
        status="published",
    )
    db_session.add(quiz)
    await db_session.flush()

    q1_id = uuid.uuid4()
    q2_id = uuid.uuid4()
    q3_id = uuid.uuid4()

    opt1_a = str(uuid.uuid4())
    opt1_b = str(uuid.uuid4())
    opt2_a = str(uuid.uuid4())
    opt2_b = str(uuid.uuid4())
    opt3_a = str(uuid.uuid4())
    opt3_b = str(uuid.uuid4())

    snapshot = {
        "title": "Tech Quiz",
        "questions": [
            {
                "id": str(q1_id),
                "content": "Python là ngôn ngữ gì?",
                "topic": "Python",
                "options": [{"id": opt1_a}, {"id": opt1_b}],
            },
            {
                "id": str(q2_id),
                "content": "Asyncio là gì?",
                "topic": "Python",
                "options": [{"id": opt2_a}, {"id": opt2_b}],
            },
            {
                "id": str(q3_id),
                "content": "Docker dùng làm gì?",
                "topic": "DevOps",
                "options": [{"id": opt3_a}, {"id": opt3_b}],
            },
        ],
    }

    session_id = uuid.uuid4()
    game = GameSession(
        id=session_id,
        quiz_id=quiz.id,
        host_id=user.id,
        pin="654321",
        status="finished",
        quiz_snapshot=snapshot,
    )
    db_session.add(game)

    # 2 người chơi
    p1 = GamePlayer(
        id=uuid.uuid4(),
        session_id=session_id,
        nickname="PlayerOne",
        final_score=2000,
        final_rank=1,
    )
    p2 = GamePlayer(
        id=uuid.uuid4(),
        session_id=session_id,
        nickname="PlayerTwo",
        final_score=800,
        final_rank=2,
    )
    db_session.add_all([p1, p2])

    # Câu 1 (Python): cả 2 trả lời ĐÚNG
    a1_p1 = PlayerAnswer(
        session_id=session_id,
        player_id=p1.id,
        question_id=q1_id,
        option_ids=[opt1_a],
        is_correct=True,
        response_ms=1200,
        points_awarded=1000,
    )
    a1_p2 = PlayerAnswer(
        session_id=session_id,
        player_id=p2.id,
        question_id=q1_id,
        option_ids=[opt1_a],
        is_correct=True,
        response_ms=2500,
        points_awarded=800,
    )

    # Câu 2 (Python): p1 đúng, p2 sai
    a2_p1 = PlayerAnswer(
        session_id=session_id,
        player_id=p1.id,
        question_id=q2_id,
        option_ids=[opt2_a],
        is_correct=True,
        response_ms=1500,
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

    # Câu 3 (DevOps): cả 2 đều SAI
    a3_p1 = PlayerAnswer(
        session_id=session_id,
        player_id=p1.id,
        question_id=q3_id,
        option_ids=[opt3_b],
        is_correct=False,
        response_ms=4000,
        points_awarded=0,
    )
    a3_p2 = PlayerAnswer(
        session_id=session_id,
        player_id=p2.id,
        question_id=q3_id,
        option_ids=[opt3_b],
        is_correct=False,
        response_ms=5000,
        points_awarded=0,
    )

    db_session.add_all([a1_p1, a1_p2, a2_p1, a2_p2, a3_p1, a3_p2])
    await db_session.commit()

    # Tính toán
    stats = await aggregate_game_statistics(db_session, session_id)

    assert stats["total_players"] == 2
    assert stats["total_questions"] == 3
    assert stats["total_answers"] == 6

    # 3 câu đúng trên tổng 6 câu trả lời = 50%
    assert stats["overall_accuracy"] == 0.5

    # Thời gian phản hồi trung bình: (1200+2500+1500+3000+4000+5000)/6 = 17200/6 = 2866.67
    assert stats["average_response_ms"] == 2866.67

    # Câu khó nhất là câu 3 (accuracy = 0.0), dễ nhất là câu 1 (accuracy = 1.0)
    assert stats["hardest_question"]["question_id"] == str(q3_id)
    assert stats["hardest_question"]["accuracy_rate"] == 0.0
    assert stats["easiest_question"]["question_id"] == str(q1_id)
    assert stats["easiest_question"]["accuracy_rate"] == 1.0

    # Phân bổ đáp án câu 1: opt1_a có 2 lượt chọn
    assert stats["question_stats"][0]["option_distribution"][opt1_a] == 2
    assert stats["question_stats"][0]["option_distribution"][opt1_b] == 0

    # Thống kê theo chủ đề:
    # Python: 4 câu trả lời, 3 đúng -> 3/4 = 0.75 -> 'developing'
    # DevOps: 2 câu trả lời, 0 đúng -> 0/2 = 0.0 -> 'needs_focus'
    topic_map = {t["topic"]: t for t in stats["topic_stats"]}
    assert topic_map["Python"]["accuracy_rate"] == 0.75
    assert topic_map["Python"]["mastery_level"] == "developing"
    assert topic_map["DevOps"]["accuracy_rate"] == 0.0
    assert topic_map["DevOps"]["mastery_level"] == "needs_focus"

    # Leaderboard
    assert len(stats["leaderboard"]) == 2
    assert stats["leaderboard"][0]["nickname"] == "PlayerOne"
    assert stats["leaderboard"][0]["final_score"] == 2000
    assert stats["leaderboard"][0]["final_rank"] == 1
