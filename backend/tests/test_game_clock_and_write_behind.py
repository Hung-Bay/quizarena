"""Bộ kiểm thử Game Clock, Đóng sớm, Cá nhân hóa và Write-behind (Thứ Năm).

Tuân thủ nghiêm ngặt các tiêu chí nghiệm thu Thứ Năm & SRS:
1. Game clock (FR-GAME-15, ADR-003):
   - Tự động đóng câu hỏi khi hết giờ kể cả khi Host tắt tab / ngắt kết nối.
   - Đóng 2 lần vẫn Idempotent (không đổi trạng thái, không lỗi).
2. Đóng sớm (Early close - FR-GAME-15):
   - Khi 100% người chơi trong phòng đã nộp bài (all_answered), câu hỏi tự động đóng ngay lập tức.
3. question.result cá nhân hóa & Leaderboard (FR-GAME-16):
   - question.result phát phân bố (dist), đáp án đúng, Top 10 Leaderboard.
   - Từng người chơi nhận được thông tin cá nhân hóa (điểm câu, tổng điểm, streak, thứ hạng).
   - Host nhận thông tin chung không có trường cá nhân.
4. Write-behind Worker & Idempotent (FR-GAME-19, GM-11, GM-12):
   - Đọc Redis Stream game:{id}:answers và ghi theo lô vào player_answers.
   - Chống trùng lặp tuyệt đối qua ON CONFLICT (session_id, player_id, question_id) DO NOTHING.
5. Đối soát khi FINISHED (Reconciliation - FR-GAME-19):
   - Đảm bảo số đáp án trong Postgres = số đáp án trong Redis Stream.
   - Cập nhật final_score, final_rank vào game_players và đánh dấu trận đấu FINISHED.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy.dialects.postgresql import Insert

from app.game import clock, lua_engine, write_behind
from app.game.enums import WSMessageType
from app.realtime.connection_manager import ClientConnection, ConnectionManager
from app.worker import close_question_task, flush_game_answers_task

REDIS_TEST_URL = "redis://localhost:6379/0"


@pytest_asyncio.fixture
async def redis_client():
    """Fixture cung cấp Redis client kết nối trực tiếp localhost:6379."""
    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Cần Redis server đang chạy trên localhost:6379: {exc}")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def setup_test_game_clock_session(
    redis: Redis,
    *,
    game_id: str,
    host_id: str,
    total_questions: int = 2,
    player_count: int = 2,
) -> dict[str, str]:
    """Tạo dữ liệu trận đấu mẫu trên Redis phục vụ kiểm thử Game Clock."""
    state_key = f"game:{game_id}:state"
    await redis.hset(  # type: ignore[misc]
        state_key,
        mapping={
            "game_id": game_id,
            "pin": "654321",
            "host_id": host_id,
            "status": "lobby",
            "q_index": "0",
            "total_questions": str(total_questions),
            "deadline": "0",
            "deadline_version": "0",
            "strategy_id": "standard",
        },
    )

    opt1_id = str(uuid.uuid4())
    opt2_id = str(uuid.uuid4())
    q1_id = str(uuid.uuid4())

    quiz_snapshot = {
        "title": "Clock Test Quiz",
        "questions": [
            {
                "id": q1_id,
                "position": 1,
                "content": "2 + 2 = ?",
                "type": "single",
                "points": 1000,
                "time_limit_s": 15,
                "explanation": "2 + 2 = 4 theo số học cơ bản",
                "options": [
                    {"id": opt1_id, "position": 1, "content": "4", "is_correct": True},
                    {"id": opt2_id, "position": 2, "content": "5", "is_correct": False},
                ],
            }
        ],
    }
    await redis.set(f"game:{game_id}:quiz", json.dumps(quiz_snapshot))

    # Bóc tách metadata câu 1
    await redis.sadd(f"game:{game_id}:q:1:correct_keys", opt1_id)  # type: ignore[misc]
    await redis.hset(  # type: ignore[misc]
        f"game:{game_id}:q:1:meta",
        mapping={
            "id": q1_id,
            "points": "1000",
            "time_limit_s": "15",
            "type": "single",
        },
    )

    # Thêm người chơi
    player_ids: list[str] = []
    for i in range(1, player_count + 1):
        pid = str(uuid.uuid4())
        nick = f"Player_{i}"
        player_ids.append(pid)
        p_data = {"id": pid, "nickname": nick, "online": True}
        await redis.hset(f"game:{game_id}:players", nick.lower(), json.dumps(p_data))  # type: ignore[misc]
        await redis.hset(f"game:{game_id}:nicknames", pid, nick)  # type: ignore[misc]
        await redis.zadd(f"game:{game_id}:lb", {pid: 0})

    return {
        "q1_id": q1_id,
        "opt1_correct": opt1_id,
        "opt2_wrong": opt2_id,
        "player_1": player_ids[0] if player_ids else "",
        "player_2": player_ids[1] if len(player_ids) > 1 else "",
    }


# =========================================================================
# 1. TEST GAME CLOCK TỰ ĐỘNG ĐÓNG CÂU VÀ TÍNH IDEMPOTENT (FR-GAME-15)
# =========================================================================


@pytest.mark.asyncio
async def test_game_clock_auto_close_on_deadline(redis_client):
    """Câu hỏi tự động đóng khi hết giờ deadline dù Host không thao tác (FR-GAME-15)."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    await setup_test_game_clock_session(redis_client, game_id=gid, host_id=hid)

    # Bắt đầu câu 1
    res_start = await lua_engine.execute_transition(
        redis_client, game_id=gid, action="start", time_limit_s=15
    )
    assert res_start.ok is True
    assert res_start.status == "question_active"

    # Lập lịch timer ngắn (50ms) để test tự động đóng
    now_res = await redis_client.time()
    now_ms = (now_res[0] * 1000) + (now_res[1] // 1000)
    fake_deadline = now_ms + 50

    await clock.schedule_question_deadline_timer(
        redis_client, game_id=gid, q_index=1, deadline_ms=fake_deadline
    )

    # Đợi 100ms để timer kích hoạt
    await asyncio.sleep(0.1)

    # Kiểm tra trạng thái đã tự động chuyển sang question_result
    state = await redis_client.hgetall(f"game:{gid}:state")
    assert state.get("status") == "question_result"
    assert state.get("deadline") == "0"


@pytest.mark.asyncio
async def test_game_clock_close_is_idempotent(redis_client):
    """Đóng câu hỏi 2 lần liên tiếp vẫn an toàn và Idempotent (FR-GAME-15)."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    await setup_test_game_clock_session(redis_client, game_id=gid, host_id=hid)

    await lua_engine.execute_transition(redis_client, game_id=gid, action="start", time_limit_s=15)

    # Lần đóng 1
    ok1 = await clock.close_and_broadcast_question(redis_client, game_id=gid, q_index=1)
    assert ok1 is True

    # Lần đóng 2 (idempotent hit)
    ok2 = await clock.close_and_broadcast_question(redis_client, game_id=gid, q_index=1)
    assert ok2 is True

    state = await redis_client.hgetall(f"game:{gid}:state")
    assert state.get("status") == "question_result"


# =========================================================================
# 2. TEST ĐÓNG SỚM KHI 100% NGƯỜI CHƠI ĐÃ TRẢ LỜI (FR-GAME-15)
# =========================================================================


@pytest.mark.asyncio
async def test_early_close_when_all_answered(redis_client):
    """Khi tất cả người chơi trong phòng đã nộp bài, all_answered bật cờ True."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    info = await setup_test_game_clock_session(
        redis_client, game_id=gid, host_id=hid, player_count=2
    )

    p1 = info["player_1"]
    p2 = info["player_2"]
    opt_correct = info["opt1_correct"]

    # Bắt đầu câu 1
    await lua_engine.execute_transition(redis_client, game_id=gid, action="start", time_limit_s=15)

    # Người chơi 1 nộp bài -> chưa xong hết
    sub1 = await lua_engine.execute_submit_answer(
        redis_client,
        game_id=gid,
        player_id=p1,
        q_index=1,
        selected_option_ids=[opt_correct],
    )
    assert sub1.accepted is True
    assert sub1.all_answered is False

    # Người chơi 2 nộp bài -> 2/2 người đã nộp -> all_answered = True
    sub2 = await lua_engine.execute_submit_answer(
        redis_client,
        game_id=gid,
        player_id=p2,
        q_index=1,
        selected_option_ids=[opt_correct],
    )
    assert sub2.accepted is True
    assert sub2.all_answered is True


# =========================================================================
# 3. TEST QUESTION.RESULT VÀ CÁ NHÂN HÓA (FR-GAME-16)
# =========================================================================


@pytest.mark.asyncio
async def test_build_question_result_payload(redis_client):
    """Tổng hợp question.result: đủ đáp án đúng, phân bố và Top 10 Leaderboard."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    info = await setup_test_game_clock_session(
        redis_client, game_id=gid, host_id=hid, player_count=2
    )
    p1 = info["player_1"]
    p2 = info["player_2"]
    opt_correct = info["opt1_correct"]
    opt_wrong = info["opt2_wrong"]

    # Start câu 1
    await lua_engine.execute_transition(redis_client, game_id=gid, action="start", time_limit_s=15)

    # P1 chọn đúng, P2 chọn sai
    await lua_engine.execute_submit_answer(
        redis_client, game_id=gid, player_id=p1, q_index=1, selected_option_ids=[opt_correct]
    )
    await lua_engine.execute_submit_answer(
        redis_client, game_id=gid, player_id=p2, q_index=1, selected_option_ids=[opt_wrong]
    )

    # Đóng câu hỏi
    await clock.close_and_broadcast_question(redis_client, game_id=gid, q_index=1)

    result_payload = await clock.build_question_result_payload(redis_client, game_id=gid, q_index=1)

    assert result_payload["q_index"] == 1
    assert str(info["opt1_correct"]) in [str(x) for x in result_payload["correct_option_ids"]]
    assert result_payload["explanation"] == "2 + 2 = 4 theo số học cơ bản"

    # Phân bố lựa chọn
    dist = result_payload["distribution"]
    assert dist.get(opt_correct) == 1
    assert dist.get(opt_wrong) == 1

    # Leaderboard Top 10
    lb = result_payload["leaderboard"]
    assert len(lb) == 2
    assert lb[0]["player_id"] == p1  # P1 đúng nên đứng đầu
    assert lb[0]["score"] > 0
    assert lb[1]["player_id"] == p2  # P2 sai nên 0 điểm


@pytest.mark.asyncio
async def test_personalized_broadcast_results(redis_client):
    """Replica tự pipeline cá nhân hóa question.result cho từng kết nối cục bộ."""
    gid = str(uuid.uuid4())
    p1 = str(uuid.uuid4())

    manager = ConnectionManager(redis_client)

    ws_mock_player = AsyncMock()
    ws_mock_host = AsyncMock()

    conn_player = ClientConnection(
        ws=ws_mock_player,
        client_id=p1,
        nickname="PlayerAlice",
        is_host=False,
        game_id=gid,
    )
    conn_host = ClientConnection(
        ws=ws_mock_host,
        client_id="host_uuid",
        nickname="Host",
        is_host=True,
        game_id=gid,
    )

    manager.active_connections[gid] = {
        p1: conn_player,
        "host_uuid": conn_host,
    }

    # Giả lập dữ liệu điểm câu 1 trên Redis
    await redis_client.hset(f"game:{gid}:q:1:pts", p1, "950")  # type: ignore[misc]
    await redis_client.zadd(f"game:{gid}:lb", {p1: 950})
    await redis_client.hset(f"game:{gid}:streaks", p1, "3")  # type: ignore[misc]

    envelope = {
        "type": WSMessageType.QUESTION_RESULT.value,
        "v": 1,
        "seq": 10,
        "data": {
            "q_index": 1,
            "correct_option_ids": [str(uuid.uuid4())],
            "distribution": {},
            "leaderboard": [],
        },
    }

    await manager.broadcast_personalized(gid, envelope)

    # 1. Kiểm tra tin gửi cho Player: ĐƯỢC cá nhân hóa
    assert ws_mock_player.send_text.called
    player_sent_json = json.loads(ws_mock_player.send_text.call_args[0][0])
    p_data = player_sent_json["data"]
    assert p_data["player_points_awarded"] == 950
    assert p_data["player_total_score"] == 950
    assert p_data["player_rank"] == 1
    assert p_data["player_streak"] == 3

    # 2. Kiểm tra tin gửi cho Host: bản gốc không có player_*
    assert ws_mock_host.send_text.called
    host_sent_json = json.loads(ws_mock_host.send_text.call_args[0][0])
    h_data = host_sent_json["data"]
    assert "player_points_awarded" not in h_data or h_data.get("player_points_awarded") is None


# =========================================================================
# 4. TEST WRITE-BEHIND & ĐỐI SOÁT POSTGRES == STREAM (FR-GAME-19)
# =========================================================================


@pytest.mark.asyncio
async def test_write_behind_flush_to_db_idempotent(redis_client):
    """Write-behind đọc Stream và chèn PostgreSQL với ON CONFLICT DO NOTHING (FR-GAME-19)."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    info = await setup_test_game_clock_session(redis_client, game_id=gid, host_id=hid)
    p1 = info["player_1"]
    opt_correct = info["opt1_correct"]

    await lua_engine.execute_transition(redis_client, game_id=gid, action="start", time_limit_s=15)
    # Nộp bài để đẩy vào Redis Stream game:{gid}:answers
    await lua_engine.execute_submit_answer(
        redis_client, game_id=gid, player_id=p1, q_index=1, selected_option_ids=[opt_correct]
    )

    # Mock Session PostgreSQL
    mock_session = AsyncMock()
    executed_statements = []

    async def _fake_execute(stmt):
        executed_statements.append(stmt)
        return MagicMock()

    mock_session.execute = AsyncMock(side_effect=_fake_execute)
    mock_session.commit = AsyncMock()

    # Thực hiện flush lần 1
    count1 = await write_behind.flush_answers_to_db(mock_session, redis_client, game_id=gid)
    assert count1 == 1
    assert len(executed_statements) == 1

    # Kiểm tra statement là PostgreSQL Insert có on_conflict_do_nothing
    insert_stmt = executed_statements[0]
    assert isinstance(insert_stmt, Insert)
    # Kiểm tra mệnh đề on_conflict_do_nothing
    assert insert_stmt._post_values_clause is not None or hasattr(
        insert_stmt, "_on_conflict_clause"
    )

    # Thực hiện flush lần 2 (idempotent: vẫn thực thi ON CONFLICT DO NOTHING không lỗi)
    count2 = await write_behind.flush_answers_to_db(mock_session, redis_client, game_id=gid)
    assert count2 == 1
    assert mock_session.commit.call_count == 2


@pytest.mark.asyncio
async def test_finalize_game_session_and_reconciliation(redis_client):
    """Kết thúc trận đấu: Đối soát Postgres == Stream, tính Podium Top 3 và cập nhật DB."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    info = await setup_test_game_clock_session(
        redis_client, game_id=gid, host_id=hid, player_count=2
    )
    p1 = info["player_1"]
    p2 = info["player_2"]
    opt_correct = info["opt1_correct"]

    await lua_engine.execute_transition(redis_client, game_id=gid, action="start", time_limit_s=15)

    # Cả 2 người nộp bài
    await lua_engine.execute_submit_answer(
        redis_client, game_id=gid, player_id=p1, q_index=1, selected_option_ids=[opt_correct]
    )
    await lua_engine.execute_submit_answer(
        redis_client, game_id=gid, player_id=p2, q_index=1, selected_option_ids=[opt_correct]
    )

    # Mock Session trả về số lượng bản ghi DB = 2 (khớp số stream)
    mock_session = AsyncMock()
    mock_scalar = MagicMock()
    mock_scalar.scalar_one.return_value = 2
    mock_session.execute = AsyncMock(return_value=mock_scalar)
    mock_session.commit = AsyncMock()

    res = await write_behind.finalize_game_session(mock_session, redis_client, game_id=gid)

    assert res["game_id"] == gid
    assert res["reconciled"] is True
    assert res["postgres_answers_count"] == 2
    assert res["stream_answers_count"] == 2
    assert res["total_players"] == 2
    assert len(res["podium"]) == 2

    # Trạng thái trên Redis chuyển thành finished
    state = await redis_client.hgetall(f"game:{gid}:state")
    assert state.get("status") == "finished"


# =========================================================================
# 5. TEST ARQ WORKER TASKS (FR-GAME-15, FR-GAME-19)
# =========================================================================


@pytest.mark.asyncio
async def test_arq_close_question_task(redis_client):
    """Task ARQ close_question_task thực thi đóng câu hỏi thành công."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    await setup_test_game_clock_session(redis_client, game_id=gid, host_id=hid)

    await lua_engine.execute_transition(redis_client, game_id=gid, action="start", time_limit_s=15)

    ctx = {"redis": redis_client}
    res = await close_question_task(ctx, game_id=gid, q_index=1)
    assert res["status"] == "succeeded"
    assert res["q_index"] == 1

    # Kiểm tra trạng thái đã đóng
    state = await redis_client.hgetall(f"game:{gid}:state")
    assert state.get("status") == "question_result"


@pytest.mark.asyncio
async def test_arq_flush_game_answers_task(redis_client):
    """Task ARQ flush_game_answers_task flush stream vào PostgreSQL."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    info = await setup_test_game_clock_session(redis_client, game_id=gid, host_id=hid)
    p1 = info["player_1"]

    await lua_engine.execute_transition(redis_client, game_id=gid, action="start", time_limit_s=15)
    await lua_engine.execute_submit_answer(
        redis_client,
        game_id=gid,
        player_id=p1,
        q_index=1,
        selected_option_ids=[info["opt1_correct"]],
    )

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.commit = AsyncMock()

    class FakeSessionFactory:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self):
                    return mock_session

                async def __aexit__(self, *args):
                    pass

            return _Ctx()

    ctx = {"redis": redis_client, "session_factory": FakeSessionFactory()}
    res = await flush_game_answers_task(ctx, game_id=gid)
    assert res["status"] == "succeeded"
    assert res["flushed_count"] == 1
