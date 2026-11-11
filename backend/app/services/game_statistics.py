"""Dịch vụ tổng hợp thống kê trận đấu (SQL / Python) cho Báo cáo AI (FR-AIRPT-01..06).

Thực hiện:
- Tổng hợp tỉ lệ đúng, thời gian phản hồi, phân bổ phương án chọn.
- Phân loại mức độ thuần thục (Mastery) theo chủ đề bằng ngưỡng số liệu cứng:
    * accuracy >= 0.8: 'mastered' (vững)
    * 0.5 <= accuracy < 0.8: 'developing' (đang phát triển)
    * accuracy < 0.5: 'needs_focus' (cần cải thiện)
- Xác định câu hỏi khó nhất và dễ nhất.
- Bảng xếp hạng người chơi tổng thể.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.game import GameSession, PlayerAnswer


class QuestionStat(TypedDict):
    question_id: str
    q_index: int
    content: str
    topic: str
    total_answers: int
    correct_answers: int
    accuracy_rate: float
    average_response_ms: float
    option_distribution: dict[str, int]


class TopicStat(TypedDict):
    topic: str
    total_answers: int
    correct_answers: int
    accuracy_rate: float
    mastery_level: str


def calculate_mastery(accuracy: float) -> str:
    """Xác định mức độ thành thạo theo tỉ lệ đúng."""
    if accuracy >= 0.8:
        return "mastered"
    if accuracy >= 0.5:
        return "developing"
    return "needs_focus"


async def aggregate_game_statistics(
    session: AsyncSession,
    session_id: uuid.UUID,
) -> dict[str, Any]:
    """Tính toán thống kê gộp thuần túy từ cơ sở dữ liệu cho một phiên trận đấu."""
    # 1. Truy vấn phiên trận kèm players và answers
    stmt = (
        select(GameSession)
        .where(GameSession.id == session_id)
        .options(
            selectinload(GameSession.players),
            selectinload(GameSession.answers),
        )
    )
    result = await session.execute(stmt)
    game = result.scalar_one_or_none()
    if not game:
        raise ValueError(f"GameSession {session_id} không tồn tại")

    snapshot = game.quiz_snapshot or {}
    questions_list = snapshot.get("questions", [])

    players = game.players or []
    answers = game.answers or []

    total_players = len(players)
    total_questions = len(questions_list)
    total_answers = len(answers)

    correct_answers_count = sum(1 for a in answers if a.is_correct)
    overall_accuracy = round(correct_answers_count / total_answers, 4) if total_answers > 0 else 0.0
    avg_response_ms = (
        round(sum(a.response_ms for a in answers) / total_answers, 2) if total_answers > 0 else 0.0
    )

    # 2. Thống kê theo từng câu hỏi
    # Gom answers theo question_id (dạng str)
    answers_by_q: dict[str, list[PlayerAnswer]] = {}
    for a in answers:
        qid_str = str(a.question_id)
        answers_by_q.setdefault(qid_str, []).append(a)

    question_stats_list: list[QuestionStat] = []
    for idx, q in enumerate(questions_list, start=1):
        qid_str = str(q.get("id"))
        q_answers = answers_by_q.get(qid_str, [])
        q_total = len(q_answers)
        q_correct = sum(1 for a in q_answers if a.is_correct)
        q_accuracy = round(q_correct / q_total, 4) if q_total > 0 else 0.0
        q_avg_ms = round(sum(a.response_ms for a in q_answers) / q_total, 2) if q_total > 0 else 0.0

        # Phân bổ lựa chọn
        option_dist: dict[str, int] = {}
        for opt in q.get("options", []):
            option_dist[str(opt.get("id"))] = 0
        for a in q_answers:
            for opt_id in a.option_ids:
                opt_str = str(opt_id)
                option_dist[opt_str] = option_dist.get(opt_str, 0) + 1

        question_stats_list.append(
            {
                "question_id": qid_str,
                "q_index": idx,
                "content": q.get("content", ""),
                "topic": q.get("topic") or "Chung",
                "total_answers": q_total,
                "correct_answers": q_correct,
                "accuracy_rate": q_accuracy,
                "average_response_ms": q_avg_ms,
                "option_distribution": option_dist,
            }
        )

    # 3. Thống kê theo chủ đề (topic)
    topic_groups: dict[str, dict[str, int]] = {}
    for qs in question_stats_list:
        top = qs["topic"]
        if top not in topic_groups:
            topic_groups[top] = {"total_answers": 0, "correct_answers": 0}
        topic_groups[top]["total_answers"] += qs["total_answers"]
        topic_groups[top]["correct_answers"] += qs["correct_answers"]

    topic_stats_list: list[TopicStat] = []
    for topic_name, data in topic_groups.items():
        t_total = data["total_answers"]
        t_correct = data["correct_answers"]
        t_acc = round(t_correct / t_total, 4) if t_total > 0 else 0.0
        topic_stats_list.append(
            {
                "topic": topic_name,
                "total_answers": t_total,
                "correct_answers": t_correct,
                "accuracy_rate": t_acc,
                "mastery_level": calculate_mastery(t_acc),
            }
        )
    # Sắp xếp theo độ thuần thục giảm dần
    topic_stats_list.sort(key=lambda t: t["accuracy_rate"], reverse=True)

    # 4. Câu hỏi khó nhất và dễ nhất
    hardest_q = None
    easiest_q = None
    if question_stats_list:
        valid_qs = [qs for qs in question_stats_list if qs["total_answers"] > 0]
        if valid_qs:
            hardest_q = min(
                valid_qs, key=lambda qs: (qs["accuracy_rate"], -qs["average_response_ms"])
            )
            easiest_q = max(
                valid_qs, key=lambda qs: (qs["accuracy_rate"], -qs["average_response_ms"])
            )

    # 5. Leaderboard tóm tắt
    sorted_players = sorted(
        players,
        key=lambda p: (p.final_score, p.final_rank if p.final_rank else 999999),
        reverse=True,
    )
    leaderboard = [
        {
            "player_id": str(p.id),
            "nickname": p.nickname,
            "final_score": p.final_score,
            "final_rank": idx,
        }
        for idx, p in enumerate(sorted_players, start=1)
    ]

    return {
        "session_id": str(session_id),
        "quiz_id": str(game.quiz_id),
        "quiz_title": snapshot.get("title", ""),
        "total_players": total_players,
        "total_questions": total_questions,
        "total_answers": total_answers,
        "overall_accuracy": overall_accuracy,
        "average_response_ms": avg_response_ms,
        "question_stats": question_stats_list,
        "topic_stats": topic_stats_list,
        "hardest_question": hardest_q,
        "easiest_question": easiest_q,
        "leaderboard": leaderboard,
    }
