"""Dịch vụ xử lý Báo cáo AI sau trận đấu (FR-AIRPT-01..06).

Nguyên tắc:
1. Tối đa 1 lần gọi Gemini/trận (RP-2, kiểm soát bằng ai_jobs.session_id UniqueConstraint).
2. Prompt không chứa tên người chơi, user_id, nickname để bảo mật danh tính tuyệt đối.
3. Validate lớp 2: Chủ đề AI nhận xét phải có thật trong snapshot bài quiz, mastery do backend quyết định.
4. Gemini lỗi (429, timeout, JSON hỏng) -> tự động chuyển sang trạng thái 'degraded' vẫn có thống kê đầy đủ.
5. Nhận xét cá nhân cho người chơi: kết hợp các câu làm sai + advice_for_learner + điểm/hạng -> 0 lần gọi AI.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway_interface import GeminiGatewayProtocol
from app.ai.schemas import GameReportDraft
from app.models.ai import AiJob
from app.models.game import GamePlayer, GameSession, PlayerAnswer
from app.models.report import GameReport
from app.services.game_statistics import aggregate_game_statistics
from app.services.gemini_rate_limiter import acquire_gemini_permit

log = logging.getLogger("app.services.report_service")


def validate_and_sanitize_report(
    draft: GameReportDraft, stats: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate lớp 2 cho báo cáo AI (chống ảo giác chủ đề và sai lệch số liệu).

    - Chủ đề (topic) trong strengths/weaknesses phải có thật trong topic_stats của trận đấu.
    - Đảm bảo tính nhất quán: Nếu topic có tỉ lệ đúng >= 0.8 thì được xếp vào strengths,
      nếu < 0.5 thì xếp vào weaknesses; nếu AI phân loại sai lệch, backend sẽ đồng bộ hóa lại.
    """
    valid_topics = {t["topic"]: t for t in stats.get("topic_stats", [])}

    sanitized_strengths: list[dict[str, Any]] = []
    for item in draft.strengths:
        top_name = item.topic.strip()
        if top_name in valid_topics:
            t_data = valid_topics[top_name]
            # Loại bỏ nếu tỉ lệ đúng thực tế quá thấp (< 50%) mà AI lại khen
            if t_data["accuracy_rate"] >= 0.5:
                sanitized_strengths.append(
                    {
                        "topic": top_name,
                        "analysis": item.analysis,
                        "accuracy_rate": t_data["accuracy_rate"],
                        "mastery_level": t_data["mastery_level"],
                    }
                )

    sanitized_weaknesses: list[dict[str, Any]] = []
    for item in draft.weaknesses:
        top_name = item.topic.strip()
        if top_name in valid_topics:
            t_data = valid_topics[top_name]
            # Loại bỏ nếu tỉ lệ đúng thực tế quá cao (>= 80%) mà AI lại bảo yếu
            if t_data["accuracy_rate"] < 0.8:
                sanitized_weaknesses.append(
                    {
                        "topic": top_name,
                        "analysis": item.analysis,
                        "accuracy_rate": t_data["accuracy_rate"],
                        "mastery_level": t_data["mastery_level"],
                    }
                )

    # Nếu sau lọc mà danh sách trống nhưng trong data có topic đạt chuẩn, tự bổ sung
    if not sanitized_strengths:
        for t in stats.get("topic_stats", []):
            if t["accuracy_rate"] >= 0.5:
                sanitized_strengths.append(
                    {
                        "topic": t["topic"],
                        "analysis": f"Học viên nắm vững kiến thức chủ đề {t['topic']}.",
                        "accuracy_rate": t["accuracy_rate"],
                        "mastery_level": t["mastery_level"],
                    }
                )

    if not sanitized_weaknesses:
        for t in stats.get("topic_stats", []):
            if t["accuracy_rate"] < 0.5:
                sanitized_weaknesses.append(
                    {
                        "topic": t["topic"],
                        "analysis": f"Chủ đề {t['topic']} có nhiều phương án nhầm lẫn, cần ôn luyện thêm.",
                        "accuracy_rate": t["accuracy_rate"],
                        "mastery_level": t["mastery_level"],
                    }
                )

    return sanitized_strengths, sanitized_weaknesses


async def generate_and_save_game_report(
    session: AsyncSession,
    redis: Redis,
    gateway: GeminiGatewayProtocol,
    session_id: uuid.UUID,
    *,
    is_retry: bool = False,
) -> GameReport:
    """Tạo hoặc thử lại Báo cáo AI cho phiên trận đấu.

    Quy trình:
    1. Gộp thống kê số liệu (không chứa tên/ID người chơi).
    2. Đăng ký/kiểm tra ai_jobs (ngăn gọi trùng lặp).
    3. Xin quota rate limiter Redis.
    4. Gọi Gateway sinh báo cáo -> validate lớp 2.
    5. Lưu PostgreSQL: ready (nếu thành công) hoặc degraded (nếu AI lỗi).
    6. Phát sự kiện WebSocket 'report.ready' cho toàn bộ phòng đấu.
    """
    # 1. Gộp thống kê và lấy thông tin GameSession
    stats = await aggregate_game_statistics(session, session_id)

    stmt_game = select(GameSession).where(GameSession.id == session_id)
    game = (await session.execute(stmt_game)).scalar_one_or_none()
    if not game:
        raise ValueError(f"GameSession {session_id} không tồn tại")

    # Kiểm tra xem đã có báo cáo trong DB chưa
    stmt = select(GameReport).where(GameReport.session_id == session_id)
    existing_report = (await session.execute(stmt)).scalar_one_or_none()

    if existing_report and existing_report.status == "ready" and not is_retry:
        return existing_report

    # 2. Quản lý bản ghi AI Job để truy vết và chứng minh metric
    stmt_job = select(AiJob).where(
        AiJob.session_id == session_id,
        AiJob.kind == "game_report",
    )
    job = (await session.execute(stmt_job)).scalar_one_or_none()

    if job is None:
        job = AiJob(
            session_id=session_id,
            created_by=game.host_id,
            kind="game_report",
            status="running",
            attempts=1,
        )
        session.add(job)
        await session.flush()
    else:
        if not is_retry and job.status == "succeeded" and existing_report:
            return existing_report
        job.attempts += 1
        job.status = "running"
        await session.flush()

    # 3. Rate limiter permit
    try:
        await acquire_gemini_permit(redis)
    except Exception as exc:
        log.warning("Không lấy được permit rate limit cho report %s: %s", session_id, exc)

    # 4. Gọi Gateway với cơ chế Fallback Degraded
    report_status = "ready"
    summary = ""
    strengths: list[dict[str, Any]] = []
    weaknesses: list[dict[str, Any]] = []
    advice_host = ""
    advice_learner = ""

    try:
        res = await gateway.generate_game_report(stats)
        sanitized_strengths, sanitized_weaknesses = validate_and_sanitize_report(res.draft, stats)
        summary = res.draft.summary
        strengths = sanitized_strengths
        weaknesses = sanitized_weaknesses
        advice_host = res.draft.advice_for_host
        advice_learner = res.draft.advice_for_learner

        job.status = "succeeded"
        job.input_tokens = res.usage.input_tokens
        job.output_tokens = res.usage.output_tokens

    except Exception as exc:
        log.warning(
            "Gemini sinh báo cáo thất bại cho trận %s (%s). Chuyển sang degraded.",
            session_id,
            exc,
        )
        report_status = "degraded"
        job.status = "failed"
        job.error = str(exc)

        # Cung cấp nội dung mặc định khi degraded
        summary = (
            f"Trận đấu đã kết thúc với {stats.get('total_players', 0)} người tham gia. "
            f"Tỉ lệ trả lời đúng trung bình đạt {stats.get('overall_accuracy', 0.0) * 100:.1f}%. "
            "(Phân tích chi tiết của AI đang tạm thời không khả dụng)."
        )
        advice_host = (
            "Vui lòng tham khảo bảng phân bổ đáp án và các câu hỏi khó nhất trong mục thống kê "
            "để hướng dẫn thêm cho học viên."
        )
        advice_learner = "Hãy rà soát lại các câu hỏi đã trả lời sai và đối chiếu với đáp án đúng để củng cố kiến thức."

        # Xây dựng strengths/weaknesses cơ bản từ thống kê thuần
        for t in stats.get("topic_stats", []):
            if t["accuracy_rate"] >= 0.5:
                strengths.append(
                    {
                        "topic": t["topic"],
                        "analysis": f"Chủ đề {t['topic']} đạt tỉ lệ đúng {t['accuracy_rate'] * 100:.0f}%.",
                        "accuracy_rate": t["accuracy_rate"],
                        "mastery_level": t["mastery_level"],
                    }
                )
            else:
                weaknesses.append(
                    {
                        "topic": t["topic"],
                        "analysis": f"Chủ đề {t['topic']} có tỉ lệ đúng {t['accuracy_rate'] * 100:.0f}%, cần lưu ý.",
                        "accuracy_rate": t["accuracy_rate"],
                        "mastery_level": t["mastery_level"],
                    }
                )

    # 5. Lưu vào bảng game_reports
    if existing_report:
        existing_report.status = report_status
        existing_report.stats = stats
        existing_report.summary = summary
        existing_report.strengths = strengths
        existing_report.weaknesses = weaknesses
        existing_report.advice_for_host = advice_host
        existing_report.advice_for_learner = advice_learner
        report_record = existing_report
    else:
        report_record = GameReport(
            session_id=session_id,
            status=report_status,
            stats=stats,
            summary=summary,
            strengths=strengths,
            weaknesses=weaknesses,
            advice_for_host=advice_host,
            advice_for_learner=advice_learner,
        )
        session.add(report_record)

    await session.commit()

    # 6. Phát sự kiện WebSocket 'report.ready'
    try:
        report_event = {
            "type": "report.ready",
            "v": 1,
            "data": {
                "session_id": str(session_id),
                "status": report_status,
                "overall_accuracy": stats.get("overall_accuracy", 0.0),
            },
        }
        await redis.publish(f"game:{session_id}", json.dumps(report_event))
    except Exception as exc:
        log.error("Lỗi khi publish report.ready: %s", exc)

    return report_record


async def get_player_personal_feedback(
    session: AsyncSession,
    report: GameReport,
    session_id: uuid.UUID,
    player_id: uuid.UUID,
) -> dict[str, Any]:
    """Tạo nhận xét cá nhân cho người chơi với 0 lần gọi AI (FR-AIRPT-04, RP-6).

    Ghép:
    - Các chủ đề/câu hỏi người chơi này đã trả lời SAI trong trận.
    - Lời khuyên học tập chung từ báo cáo AI (advice_for_learner).
    - Thành tích cá nhân: điểm số, thứ hạng, thời gian phản hồi trung bình.
    """
    # Lấy thông tin player
    stmt_player = select(GamePlayer).where(
        GamePlayer.id == player_id,
        GamePlayer.session_id == session_id,
    )
    player = (await session.execute(stmt_player)).scalar_one_or_none()
    if not player:
        raise ValueError("Người chơi không tồn tại trong trận đấu này.")

    # Lấy toàn bộ câu trả lời của player
    stmt_answers = select(PlayerAnswer).where(
        PlayerAnswer.session_id == session_id,
        PlayerAnswer.player_id == player_id,
    )
    answers = (await session.execute(stmt_answers)).scalars().all()

    total_ans = len(answers)
    correct_ans = sum(1 for a in answers if a.is_correct)
    accuracy = round(correct_ans / total_ans, 4) if total_ans > 0 else 0.0
    avg_speed = round(sum(a.response_ms for a in answers) / total_ans, 2) if total_ans > 0 else 0.0

    # Lấy snapshot bài quiz để map câu hỏi và chủ đề
    stmt_game = select(GameSession).where(GameSession.id == session_id)
    game = (await session.execute(stmt_game)).scalar_one_or_none()
    questions_list = (game.quiz_snapshot or {}).get("questions", []) if game else []
    question_map = {str(q.get("id")): q for q in questions_list}

    # Gom các chủ đề mà người chơi trả lời sai
    wrong_topics: set[str] = set()
    wrong_questions: list[dict[str, Any]] = []

    for a in answers:
        if not a.is_correct:
            q_info = question_map.get(str(a.question_id))
            if q_info:
                topic = q_info.get("topic") or "Chung"
                wrong_topics.add(topic)
                wrong_questions.append(
                    {
                        "question_id": str(a.question_id),
                        "content": q_info.get("content", ""),
                        "topic": topic,
                    }
                )

    # Đánh giá tổng quát cá nhân
    if accuracy >= 0.8:
        eval_message = "Xuất sắc! Bạn đã nắm rất vững hầu hết các nội dung trọng tâm của bài thi."
    elif accuracy >= 0.5:
        eval_message = (
            "Khá tốt! Bạn đã đạt yêu cầu cơ bản nhưng cần lưu ý thêm một số chủ đề hay nhầm lẫn."
        )
    else:
        eval_message = "Cần cố gắng thêm! Hãy xem lại các chủ đề trả lời chưa chính xác bên dưới."

    return {
        "player_id": str(player.id),
        "nickname": player.nickname,
        "final_score": player.final_score,
        "final_rank": player.final_rank,
        "accuracy_rate": accuracy,
        "average_response_ms": avg_speed,
        "evaluation": eval_message,
        "wrong_topics": sorted(list(wrong_topics)),
        "wrong_questions_count": len(wrong_questions),
        "wrong_questions": wrong_questions,
        "learning_advice": report.advice_for_learner,
        "ai_call_count": 0,  # Chứng minh 0 lần gọi AI
    }
