"""Write-behind Worker & Đối soát dữ liệu trận đấu (FR-GAME-19, GM-11, GM-12).

Chức năng:
- Đọc Redis Stream `game:{id}:answers` theo lô (batch) và ghi bền vào PostgreSQL `player_answers`.
- Đảm bảo Idempotent tuyệt đối qua mệnh đề `ON CONFLICT (session_id, player_id, question_id) DO NOTHING`.
- Đối soát khi trận kết thúc (Reconciliation): Đảm bảo số bản ghi trong bảng `player_answers` = số bản ghi trong Redis Stream.
- Cập nhật kết quả cuối cùng: `final_score`, `final_rank` vào bảng `game_players` và đánh dấu `game_sessions` là `finished`.
- Phát thông điệp `game.finished` kèm bục vinh danh Podium Top 3 qua Redis Pub/Sub.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.game import lua_engine
from app.game.enums import WSMessageType
from app.game.schemas import GameFinishedData, LeaderboardEntry
from app.models.game import GamePlayer, GameSession, PlayerAnswer

log = logging.getLogger("app.game.write_behind")


async def flush_answers_to_db(
    session: AsyncSession,
    redis: Redis,
    *,
    game_id: uuid.UUID | str,
    batch_size: int = 500,
) -> int:
    """Đọc dữ liệu từ Redis Stream game:{id}:answers và ghi lô vào bảng player_answers.

    Sử dụng ON CONFLICT (session_id, player_id, question_id) DO NOTHING để chống trùng lặp.
    Trả về số lượng bản ghi đã xử lý.
    """
    gid_str = str(game_id)
    gid_uuid = uuid.UUID(gid_str)
    stream_key = f"game:{gid_str}:answers"

    # Đọc tối đa batch_size bản ghi từ Stream
    stream_entries = await redis.xrange(stream_key, min="-", max="+", count=batch_size)
    if not stream_entries:
        return 0

    values_to_insert: list[dict[str, Any]] = []
    for _msg_id, fields in stream_entries:
        try:
            pid_str = fields.get("player_id")
            qid_str = fields.get("question_id")
            if not pid_str or not qid_str:
                continue

            opt_ids_csv = fields.get("option_ids", "")
            option_ids = [opt.strip() for opt in opt_ids_csv.split(",") if opt.strip()]

            answered_at_ms = int(fields.get("answered_at", 0))
            if answered_at_ms > 0:
                created_dt = datetime.fromtimestamp(answered_at_ms / 1000.0, tz=UTC)
            else:
                created_dt = datetime.now(UTC)

            values_to_insert.append(
                {
                    "session_id": gid_uuid,
                    "player_id": uuid.UUID(pid_str),
                    "question_id": uuid.UUID(qid_str),
                    "option_ids": option_ids,
                    "is_correct": fields.get("is_correct") == "1",
                    "response_ms": int(fields.get("response_ms", 0)),
                    "points_awarded": int(fields.get("points", 0)),
                    "created_at": created_dt,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Lỗi khi parse bản ghi stream answer: %s", exc)

    if not values_to_insert:
        return 0

    # Thực hiện bulk insert với ON CONFLICT DO NOTHING
    stmt = pg_insert(PlayerAnswer).values(values_to_insert)
    stmt = stmt.on_conflict_do_nothing(index_elements=["session_id", "player_id", "question_id"])
    await session.execute(stmt)
    await session.commit()

    log.info(
        "Flush thành công %d câu trả lời từ Stream game %s vào PostgreSQL",
        len(values_to_insert),
        gid_str,
    )
    return len(values_to_insert)


async def finalize_game_session(
    session: AsyncSession,
    redis: Redis,
    *,
    game_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Kết thúc trận đấu, đối soát số liệu và cập nhật bảng điểm vào PostgreSQL (FR-GAME-19)."""
    gid_str = str(game_id)
    gid_uuid = uuid.UUID(gid_str)

    # 1. Chuyển trạng thái trận đấu trên Redis sang FINISHED (Lua atomic)
    res = await lua_engine.execute_transition(redis, game_id=gid_str, action="end")
    if not res.ok and res.status != "finished":
        log.warning("Không thể chuyển trạng thái FINISHED cho game %s: %s", gid_str, res.error)

    # 2. Flush toàn bộ stream đáp án vào PostgreSQL
    await flush_answers_to_db(session, redis, game_id=gid_str, batch_size=1000)

    # 3. Đối soát số lượng: Số đáp án Postgres == Số bản ghi trong Redis Stream (FR-GAME-19)
    stream_count = await redis.xlen(f"game:{gid_str}:answers")
    pg_count_query = select(func.count(PlayerAnswer.id)).where(PlayerAnswer.session_id == gid_uuid)
    pg_count = (await session.execute(pg_count_query)).scalar_one()

    is_reconciled = pg_count == stream_count
    if not is_reconciled:
        log.warning(
            "CẢNH BÁO ĐỐI SOÁT: Game %s có Postgres (%d) != Stream (%d)",
            gid_str,
            pg_count,
            stream_count,
        )
    else:
        log.info(
            "ĐỐI SOÁT THÀNH CÔNG: Game %s có Postgres == Stream == %d",
            gid_str,
            pg_count,
        )

    # 4. Lấy toàn bộ Leaderboard từ Redis ZSET game:{id}:lb
    all_players = await redis.zrevrange(  # type: ignore[misc]
        f"game:{gid_str}:lb", 0, -1, withscores=True
    )

    podium_entries: list[LeaderboardEntry] = []
    now = datetime.now(UTC)

    # 5. Cập nhật final_score và final_rank cho từng người chơi vào PostgreSQL
    for rank_idx, (pid_str, score_val) in enumerate(all_players, start=1):
        pid_uuid = uuid.UUID(pid_str)
        nick = (
            await redis.hget(f"game:{gid_str}:nicknames", pid_str)  # type: ignore[misc]
            or "Người chơi"
        )
        streak_val = (
            await redis.hget(f"game:{gid_str}:streaks", pid_str)  # type: ignore[misc]
            or 0
        )

        entry = LeaderboardEntry(
            rank=rank_idx,
            player_id=pid_uuid,
            nickname=nick,
            score=int(score_val),
            streak=int(streak_val),
        )

        if rank_idx <= 3:
            podium_entries.append(entry)

        # Cập nhật PostgreSQL
        stmt_player = (
            update(GamePlayer)
            .where(GamePlayer.session_id == gid_uuid, GamePlayer.id == pid_uuid)
            .values(final_score=int(score_val), final_rank=rank_idx)
        )
        await session.execute(stmt_player)

    # 6. Cập nhật GameSession sang FINISHED trong PostgreSQL
    stmt_session = (
        update(GameSession)
        .where(GameSession.id == gid_uuid)
        .values(
            status="finished",
            ended_at=now,
        )
    )
    await session.execute(stmt_session)
    await session.commit()

    # 7. Broadcast game.finished envelope qua Redis Pub/Sub
    finished_data = GameFinishedData(
        game_id=gid_uuid,
        podium=podium_entries,
        total_players=len(all_players),
    )

    seq = await redis.incr(f"game:{gid_str}:seq")
    finished_envelope = {
        "type": WSMessageType.GAME_FINISHED.value,
        "v": 1,
        "seq": seq,
        "data": finished_data.model_dump(mode="json"),
    }
    await redis.publish(f"game:{gid_str}", json.dumps(finished_envelope))
    log.info("Đã hoàn tất trận đấu %s và phát game.finished", gid_str)

    return {
        "game_id": gid_str,
        "reconciled": is_reconciled,
        "postgres_answers_count": pg_count,
        "stream_answers_count": stream_count,
        "total_players": len(all_players),
        "podium": [p.model_dump(mode="json") for p in podium_entries],
    }
