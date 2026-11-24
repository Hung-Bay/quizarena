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

from app.game.schemas import (
    GameSnapshotData,
    QuestionOptionPublic,
    QuestionResultData,
    QuestionStartData,
)

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

    # 3. Lấy thông tin câu hỏi hiện tại (nếu đang trong câu hỏi active hoặc kết quả câu hỏi)
    current_question: QuestionStartData | None = None
    if status in ("question_active", "question_result") and q_index > 0:
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

    # 3.1. Nếu trạng thái là question_result, tổng hợp kết quả câu hỏi & phân bố câu trả lời
    current_result: QuestionResultData | None = None
    answers_distribution: dict[str, int] | None = None
    correct_option_ids: list[uuid.UUID] | None = None
    explanation: str | None = None
    podium: list[dict[str, Any]] | None = None

    if status == "question_result" and q_index > 0:
        try:
            from app.game.clock import build_question_result_payload

            result_dict = await build_question_result_payload(
                redis, game_id=game_id, q_index=q_index
            )
            current_result = QuestionResultData.model_validate(result_dict)
            answers_distribution = result_dict.get("distribution")
            raw_corr = result_dict.get("correct_option_ids", [])
            correct_option_ids = [uuid.UUID(str(oid)) for oid in raw_corr]
            explanation = result_dict.get("explanation")
            podium = result_dict.get("podium")
        except Exception as exc:  # noqa: BLE001
            log.warning("Không thể build question result cho snapshot %s: %s", game_id, exc)
    elif status == "finished":
        try:
            top_players = await redis.zrevrange(f"game:{game_id}:lb", 0, 2, withscores=True)  # type: ignore[misc]
            podium_list = []
            for rank_idx, (pid_str, score_val) in enumerate(top_players, start=1):
                nick = (
                    await redis.hget(f"game:{game_id}:nicknames", pid_str)  # type: ignore[misc]
                    or "Người chơi"
                )
                podium_list.append({"rank": rank_idx, "nickname": nick, "score": int(score_val)})
            podium = podium_list
        except Exception as exc:  # noqa: BLE001
            log.warning("Không thể build podium cho snapshot finished %s: %s", game_id, exc)

    # 4. Lấy điểm, streak, hạng cá nhân của người chơi và trạng thái đã nộp câu này chưa
    player_score = 0
    player_streak = 0
    player_rank: int | None = None
    has_answered = False

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

            if q_index > 0:
                is_ans = await redis.sismember(f"game:{game_id}:q:{q_index}:answered", client_id)  # type: ignore[misc]
                has_answered = bool(is_ans)
        except Exception as exc:  # noqa: BLE001
            log.debug("Lỗi khi đọc điểm cá nhân của %s: %s", client_id, exc)

    # 5. Lấy mã PIN và danh sách người chơi trong phòng (GM-10)
    pin = state.get("pin")
    players_data: list[dict[str, Any]] = []
    raw_players = await redis.hgetall(f"game:{game_id}:players")  # type: ignore[misc]
    if raw_players:
        for p_id, p_json in raw_players.items():
            try:
                p_dict = json.loads(p_json)
                players_data.append(
                    {
                        "id": p_dict.get("id", p_dict.get("client_id", p_id)),
                        "nickname": p_dict.get("nickname", "Ẩn danh"),
                    }
                )
            except Exception:  # noqa: BLE001
                pass

    personal_dict: dict[str, Any] | None = None
    if not is_host:
        personal_dict = {
            "score": player_score,
            "added_score": 0,
            "streak": player_streak,
            "rank": player_rank or 1,
        }
        if status == "question_result" and client_id and q_index > 0:
            try:
                pts_val = await redis.hget(f"game:{game_id}:q:{q_index}:pts", client_id)  # type: ignore[misc]
                corr_val = await redis.hget(f"game:{game_id}:q:{q_index}:pts:correct", client_id)  # type: ignore[misc]
                if pts_val is not None:
                    personal_dict["added_score"] = int(pts_val)
                if corr_val is not None:
                    personal_dict["is_correct"] = corr_val == "1"
            except Exception as exc:  # noqa: BLE001
                log.debug("Lỗi khi đọc kết quả câu hỏi cá nhân của %s: %s", client_id, exc)

    snapshot_obj = GameSnapshotData(
        game_id=uuid.UUID(game_id),
        status=status,
        q_index=q_index,
        total_questions=total_questions,
        pin=pin,
        player_count=len(players_data),
        players=players_data,
        has_answered=has_answered,
        current_question=current_question,
        current_result=current_result,
        answers_distribution=answers_distribution,
        correct_option_ids=correct_option_ids,
        explanation=explanation,
        podium=podium,
        player_score=player_score,
        player_streak=player_streak,
        player_rank=player_rank,
        is_host=is_host,
        personal=personal_dict,
    )

    return {
        "type": "snapshot",
        "v": 1,
        "data": snapshot_obj.model_dump(mode="json"),
    }
