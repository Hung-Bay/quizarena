"""Game Clock & Quản lý vòng đời câu hỏi thời gian thực (FR-GAME-15, FR-GAME-16, ADR-003).

Chức năng:
- Lập lịch tự động đóng câu hỏi khi hết giờ (deadline) kể cả khi Host tắt tab / ngắt mạng (GM-4, GM-15).
- Hỗ trợ đóng sớm (early close) ngay khi 100% người chơi đã nộp bài (all_answered).
- Xử lý đóng câu hỏi Idempotent: nhiều job đóng hoặc host bấm đóng cùng lúc không gây lỗi hay trùng lặp.
- Thu thập phân bố đáp án (dist), đáp án đúng (correct_keys) và Top 10 Bảng xếp hạng (leaderboard).
- Phát thông điệp `question.result` và `leaderboard` qua Redis Pub/Sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.game import lua_engine
from app.game.enums import WSMessageType
from app.game.schemas import LeaderboardEntry, QuestionResultData

log = logging.getLogger("app.game.clock")

# Quản lý các task đếm ngược nội bộ dự phòng nếu không dùng ARQ daemon
_ACTIVE_CLOCK_TASKS: dict[str, asyncio.Task[None]] = {}
_CLOCK_LOCK = asyncio.Lock()


async def build_question_result_payload(
    redis: Redis,
    *,
    game_id: str,
    q_index: int,
) -> dict[str, Any]:
    """Tổng hợp kết quả sau khi đóng câu hỏi (đáp án đúng, phân bố, Top 10 Leaderboard)."""
    # 1. Lấy danh sách đáp án đúng từ Set Redis
    correct_opts_raw = await redis.smembers(  # type: ignore[misc]
        f"game:{game_id}:q:{q_index}:correct_keys"
    )
    correct_option_ids = [uuid.UUID(oid) for oid in correct_opts_raw]

    # 2. Lấy phân bố lựa chọn (distribution) từ Hash Redis
    dist_raw = await redis.hgetall(f"game:{game_id}:q:{q_index}:dist")  # type: ignore[misc]
    distribution = {opt_id: int(count) for opt_id, count in dist_raw.items()}

    # 3. Lấy Top 10 từ ZSET Leaderboard (điểm cao nhất trước)
    top_players = await redis.zrevrange(  # type: ignore[misc]
        f"game:{game_id}:lb", 0, 9, withscores=True
    )

    leaderboard: list[LeaderboardEntry] = []
    for rank_idx, (pid_str, score_val) in enumerate(top_players, start=1):
        nick = (
            await redis.hget(f"game:{game_id}:nicknames", pid_str)  # type: ignore[misc]
            or "Người chơi"
        )
        streak_val = (
            await redis.hget(f"game:{game_id}:streaks", pid_str)  # type: ignore[misc]
            or 0
        )
        leaderboard.append(
            LeaderboardEntry(
                rank=rank_idx,
                player_id=uuid.UUID(pid_str),
                nickname=nick,
                score=int(score_val),
                streak=int(streak_val),
            )
        )

    # 4. Lấy giải thích đáp án (explanation) từ quiz snapshot nếu có
    explanation: str | None = None
    raw_quiz = await redis.get(f"game:{game_id}:quiz")
    if raw_quiz:
        try:
            quiz_data = json.loads(raw_quiz)
            questions = quiz_data.get("questions", [])
            if 1 <= q_index <= len(questions):
                explanation = questions[q_index - 1].get("explanation")
        except Exception:
            pass

    result_data = QuestionResultData(
        q_index=q_index,
        correct_option_ids=correct_option_ids,
        explanation=explanation,
        distribution=distribution,
        leaderboard=leaderboard,
    )

    return result_data.model_dump(mode="json")


async def close_and_broadcast_question(
    redis: Redis,
    *,
    game_id: str,
    q_index: int,
) -> bool:
    """Đóng câu hỏi atomic bằng Lua và phát kết quả qua Redis Pub/Sub (FR-GAME-15, 16).

    Trả về True nếu đóng thành công hoặc đã được đóng trước đó (idempotent).
    """
    res = await lua_engine.execute_transition(
        redis,
        game_id=game_id,
        action="close",
        expected_q_index=q_index,
    )

    if not res.ok:
        log.warning(
            "Không thể đóng câu hỏi %d trận %s: error=%s status=%s",
            q_index,
            game_id,
            res.error,
            res.status,
        )
        return False

    # Lấy payload kết quả câu hỏi
    result_payload = await build_question_result_payload(
        redis,
        game_id=game_id,
        q_index=q_index,
    )

    # Broadcast question.result qua Redis Pub/Sub kênh game:{game_id}
    seq = await redis.incr(f"game:{game_id}:seq")
    envelope = {
        "type": WSMessageType.QUESTION_RESULT.value,
        "v": 1,
        "seq": seq,
        "data": result_payload,
    }

    await redis.publish(f"game:{game_id}", json.dumps(envelope))
    log.info("Đã đóng câu hỏi %d trận %s và phát question.result", q_index, game_id)
    return True


async def schedule_question_deadline_timer(
    redis: Redis,
    *,
    game_id: str,
    q_index: int,
    deadline_ms: int,
    arq_redis_client: Any | None = None,
) -> None:
    """Lập lịch đóng câu hỏi khi đến hạn deadline (FR-GAME-15, ADR-003).

    Ưu tiên đẩy job vào ARQ nếu có arq_redis_client. Đồng thời duy trì một
    asyncio.Task nền nội bộ để đảm bảo hoạt động độc lập ngay cả khi không có ARQ daemon.
    """
    now_res = await redis.time()
    now_ms = (now_res[0] * 1000) + (now_res[1] // 1000)
    delay_s = max(0.0, (deadline_ms - now_ms) / 1000.0)

    # 1. Đẩy job vào ARQ với defer_until nếu có client
    if arq_redis_client is not None:
        try:
            defer_dt = datetime.fromtimestamp(deadline_ms / 1000.0, tz=UTC)
            await arq_redis_client.enqueue_job(
                "close_question_task",
                game_id,
                q_index,
                _defer_until=defer_dt,
                _job_id=f"close_{game_id}_{q_index}",
            )
            log.info(
                "Đã lập lịch ARQ job close_question_task cho game %s câu %d (hoãn %.2fs)",
                game_id,
                q_index,
                delay_s,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Không thể lập lịch qua ARQ, dùng task nền dự phòng: %s", exc)

    # 2. Task đếm ngược dự phòng nội bộ
    task_key = f"{game_id}:{q_index}"

    async def _timer_worker() -> None:
        try:
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            await close_and_broadcast_question(redis, game_id=game_id, q_index=q_index)
        except asyncio.CancelledError:
            pass
        finally:
            async with _CLOCK_LOCK:
                _ACTIVE_CLOCK_TASKS.pop(task_key, None)

    async with _CLOCK_LOCK:
        old_task = _ACTIVE_CLOCK_TASKS.get(task_key)
        if old_task and not old_task.done():
            old_task.cancel()

        task = asyncio.create_task(_timer_worker(), name=f"clock-timer-{task_key}")
        _ACTIVE_CLOCK_TASKS[task_key] = task


async def cancel_question_timer(game_id: str, q_index: int) -> None:
    """Hủy task hẹn giờ nội bộ khi câu hỏi được đóng sớm hoặc chuyển câu."""
    task_key = f"{game_id}:{q_index}"
    async with _CLOCK_LOCK:
        task = _ACTIVE_CLOCK_TASKS.pop(task_key, None)
        if task and not task.done():
            task.cancel()
