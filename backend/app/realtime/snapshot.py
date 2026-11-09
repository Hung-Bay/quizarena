"""Snapshot builder cho Live Game WebSocket (FR-GAME-17, FR-GAME-08).

Đặc tả:
- Ngay khi client kết nối hoặc kết nối lại, server gửi ngay một snapshot chứa:
  + Trạng thái hiện tại của trận đấu (lobby / question_active / question_result / finished).
  + Thứ tự câu hỏi hiện tại và tổng số câu hỏi.
  + Nếu đang trong câu hỏi (question_active): thông tin câu hỏi, các lựa chọn, deadline,
    thời gian server hiện tại (để client đồng bộ đồng hồ đếm ngược).
    TUYỆT ĐỐI KHÔNG chứa trường `is_correct` (FR-GAME-08).
  + Điểm hiện tại, chuỗi streak và thứ hạng cá nhân của người chơi (nếu là player).
  + Cờ `is_host`.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis

from app.game.schemas import GameSnapshotData, QuestionOptionPublic, QuestionStartData

log = logging.getLogger("app.realtime.snapshot")


async def build_game_snapshot(
    redis: Redis,
    *,
    game_id: str,
    client_id: str,
    is_host: bool,
) -> dict[str, Any]:
    """Xây dựng Game Snapshot an toàn (không lộ đáp án) khi client kết nối/kết nối lại."""
    # 1. Đọc trạng thái trận từ Redis
    state = await redis.hgetall(f"game:{game_id}:state")  # type: ignore[misc]
    status = state.get("status", "lobby")
    q_index = int(state.get("q_index", 0))
    total_questions = int(state.get("total_questions", 0))
    deadline = int(state.get("deadline", 0))

    # 2. Lấy thời gian server hiện tại theo nguồn giờ Redis TIME
    time_res = await redis.time()
    sec, usec = time_res
    server_time_ms = (sec * 1000) + (usec // 1000)

    # 3. Lấy thông tin câu hỏi hiện tại (nếu đang trong câu hỏi active)
    current_question: QuestionStartData | None = None
    if status == "question_active" and q_index > 0:
        raw_quiz = await redis.get(f"game:{game_id}:quiz")
        if raw_quiz:
            try:
                quiz_snapshot = json.loads(raw_quiz)
                questions = quiz_snapshot.get("questions", [])
                if 1 <= q_index <= len(questions):
                    q_data = questions[q_index - 1]

                    # Bóc tách options tuyệt đối KHÔNG có is_correct (FR-GAME-08)
                    public_options: list[QuestionOptionPublic] = []
                    for opt in q_data.get("options", []):
                        public_options.append(
                            QuestionOptionPublic(
                                id=uuid.UUID(opt["id"]),
                                position=int(opt["position"]),
                                content=str(opt["content"]),
                            )
                        )

                    current_question = QuestionStartData(
                        q_index=q_index,
                        total_questions=total_questions,
                        question_id=uuid.UUID(q_data["id"]),
                        content=str(q_data["content"]),
                        type=str(q_data.get("type", "single")),
                        points=int(q_data.get("points", 1000)),
                        time_limit_s=int(q_data.get("time_limit_s", 15)),
                        server_time=server_time_ms,
                        deadline=deadline,
                        options=public_options,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("Không thể parse quiz snapshot cho game %s: %s", game_id, exc)

    # 4. Lấy điểm, streak, hạng cá nhân của người chơi
    player_score = 0
    player_streak = 0
    player_rank: int | None = None

    if not is_host and client_id:
        try:
            score_val = await redis.zscore(f"game:{game_id}:lb", client_id)
            if score_val is not None:
                player_score = int(score_val)

            streak_val = await redis.hget(f"game:{game_id}:streaks", client_id)  # type: ignore[misc]
            if streak_val is not None:
                player_streak = int(streak_val)

            rank_val = await redis.zrevrank(f"game:{game_id}:lb", client_id)
            if rank_val is not None:
                player_rank = rank_val + 1
        except Exception as exc:  # noqa: BLE001
            log.debug("Lỗi khi đọc điểm cá nhân của %s: %s", client_id, exc)

    snapshot_obj = GameSnapshotData(
        game_id=uuid.UUID(game_id),
        status=status,
        q_index=q_index,
        total_questions=total_questions,
        current_question=current_question,
        current_result=None,
        player_score=player_score,
        player_streak=player_streak,
        player_rank=player_rank,
        is_host=is_host,
    )

    return {
        "type": "snapshot",
        "v": 1,
        "data": snapshot_obj.model_dump(mode="json"),
    }
