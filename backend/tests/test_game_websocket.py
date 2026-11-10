"""Bộ kiểm thử toàn diện WebSocket cho Live Game Engine (Thứ Tư).

Tuân thủ nghiêm ngặt các tiêu chí nghiệm thu Thứ Tư & SRS:
1. Đổi ticket (GETDEL atomic trên Redis - AU-11):
   - Ticket rỗng/sai/hết hạn -> đóng kết nối với mã 4001 (TICKET_INVALID).
2. Kiểm tra trạng thái trận:
   - Trận không tồn tại hoặc đã finished -> đóng 4003 (GAME_ENDED).
3. Kiểm tra kick-ban:
   - Bị host kick cấm 10 phút -> đóng 4002 (KICKED).
4. Phân quyền danh tính:
   - Host: nhận snapshot `is_host=True`, được gửi lệnh host (game.start, game.next, game.end, player.kick).
   - Player: nhận snapshot `is_host=False`, gửi lệnh host bị từ chối bằng error FORBIDDEN.
   - Host nộp bài bị từ chối bằng error FORBIDDEN.
5. Giới hạn tốc độ (FR-GAME-21):
   - Tối đa 5 thông điệp/giây mỗi kết nối. Vượt quá trả error RATE_LIMITED mà không rớt kết nối.
6. Schema an toàn - TUYỆT ĐỐI KHÔNG LỘ ĐÁP ÁN (FR-GAME-08):
   - question.start và snapshot TUYỆT ĐỐI KHÔNG chứa trường `is_correct` ở bất kỳ đâu.
7. Phản hồi nộp bài (FR-GAME-07, 09):
   - answer.ack trả ngay `accepted` và `reason` (nếu lỗi), KHÔNG nói đúng/sai hoặc điểm.
   - Nộp bài lần hai trả `accepted=False`, `reason='ALREADY_ANSWERED'`.
8. Kick người chơi (FR-GAME-05):
   - Host kick -> Player bị đóng kết nối với mã 4002, cấm vào lại 10 phút.
9. Reconnect nhận snapshot (FR-GAME-17):
   - Reconnect giữa câu nhận đúng câu đang chạy và thời gian còn lại (không lộ đáp án).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from starlette.websockets import WebSocketDisconnect

from app.game.enums import WSMessageType
from app.main_realtime import app

REDIS_TEST_URL = "redis://localhost:6379/0"


@pytest_asyncio.fixture
async def redis_client():
    """Fixture Redis kết nối trực tiếp localhost:6379."""
    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Cần Redis server đang chạy trên localhost:6379: {exc}")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def setup_test_game_session(
    redis: Redis,
    *,
    game_id: str,
    host_id: str,
    pin: str = "123456",
    total_questions: int = 2,
    player_id: str | None = None,
    player_nickname: str = "PlayerOne",
) -> dict:
    """Tạo dữ liệu trận đấu mẫu trên Redis."""
    state_key = f"game:{game_id}:state"
    await redis.hset(  # type: ignore[misc]
        state_key,
        mapping={
            "game_id": game_id,
            "pin": pin,
            "host_id": host_id,
            "status": "lobby",
            "q_index": "0",
            "total_questions": str(total_questions),
            "deadline": "0",
            "deadline_version": "0",
        },
    )

    # Tạo Quiz Snapshot với 2 câu hỏi mẫu
    opt1_id = str(uuid.uuid4())
    opt2_id = str(uuid.uuid4())
    opt3_id = str(uuid.uuid4())
    opt4_id = str(uuid.uuid4())

    quiz_snapshot = {
        "title": "Test Quiz Live",
        "questions": [
            {
                "id": str(uuid.uuid4()),
                "position": 1,
                "content": "Thủ đô của Việt Nam là gì?",
                "type": "single",
                "points": 1000,
                "time_limit_s": 15,
                "options": [
                    {"id": opt1_id, "position": 1, "content": "Hà Nội", "is_correct": True},
                    {"id": opt2_id, "position": 2, "content": "TP.HCM", "is_correct": False},
                ],
            },
            {
                "id": str(uuid.uuid4()),
                "position": 2,
                "content": "Python ra đời năm nào?",
                "type": "single",
                "points": 1000,
                "time_limit_s": 20,
                "options": [
                    {"id": opt3_id, "position": 1, "content": "1991", "is_correct": True},
                    {"id": opt4_id, "position": 2, "content": "2000", "is_correct": False},
                ],
            },
        ],
    }
    await redis.set(f"game:{game_id}:quiz", json.dumps(quiz_snapshot))

    # Bóc tách metadata và đáp án đúng vào Redis
    for idx, q_any in enumerate(quiz_snapshot["questions"], start=1):
        q: dict[str, Any] = q_any  # type: ignore[assignment]
        correct_opts = [opt["id"] for opt in q["options"] if opt["is_correct"]]
        await redis.sadd(f"game:{game_id}:q:{idx}:correct_keys", *correct_opts)  # type: ignore[misc]
        await redis.hset(  # type: ignore[misc]
            f"game:{game_id}:q:{idx}:meta",
            mapping={
                "id": q["id"],
                "points": str(q["points"]),
                "time_limit_s": str(q["time_limit_s"]),
                "type": q["type"],
            },
        )

    # Thêm player nếu có
    if player_id:
        p_data = {
            "id": player_id,
            "nickname": player_nickname,
            "online": True,
            "streak": 0,
            "score": 0,
        }
        await redis.hset(f"game:{game_id}:players", player_nickname.lower(), json.dumps(p_data))  # type: ignore[misc]
        await redis.hset(f"game:{game_id}:nicknames", player_id, player_nickname)  # type: ignore[misc]
        await redis.zadd(f"game:{game_id}:lb", {player_id: 0})

    return {
        "opt1_correct": opt1_id,
        "opt2_wrong": opt2_id,
        "opt3_correct": opt3_id,
        "opt4_wrong": opt4_id,
    }


async def create_test_ticket(
    redis: Redis,
    *,
    game_id: str,
    sub: str,
    is_host: bool = False,
    token_type: str = "guest",
) -> str:
    """Tạo ticket trong Redis mô phỏng POST /api/v1/realtime/ws-ticket."""
    ticket = f"ticket_{uuid.uuid4().hex}"
    perms = ["game:host_own"] if is_host else ["game:play"]
    roles = ["host"] if is_host else ["guest"]
    data = {
        "sub": sub if is_host else f"guest:{sub}",
        "game_id": game_id,
        "roles": roles,
        "permissions": perms,
        "type": token_type,
    }
    await redis.set(f"ws:ticket:{ticket}", json.dumps(data), ex=30)
    return ticket


# =========================================================================
# 1. TEST KẾT NỐI VÀ XÁC THỰC VÉ (TICKET VALIDATION)
# =========================================================================


def test_ws_connect_no_ticket_rejected_4001(redis_client):
    """Kết nối không có tham số ticket -> bị đóng với mã 4001."""
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/game"):
                pass
        assert exc_info.value.code == 4001


@pytest.mark.asyncio
async def test_ws_connect_invalid_or_expired_ticket_rejected_4001(redis_client):
    """Kết nối bằng ticket không tồn tại hoặc đã hết hạn -> bị đóng 4001."""
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/game?ticket=non_existent_ticket_123"):
                pass
        assert exc_info.value.code == 4001


@pytest.mark.asyncio
async def test_ws_connect_game_not_found_or_finished_closes_4003(redis_client):
    """Kết nối tới trận đấu đã kết thúc (hoặc không tồn tại) -> đóng 4003."""
    gid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    # Tạo ticket trỏ tới gid chưa tạo trong Redis
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/game?ticket={ticket}"):
                pass
        assert exc_info.value.code == 4003


@pytest.mark.asyncio
async def test_ws_kicked_player_rejected_4002(redis_client):
    """Người chơi bị kick cấm 10 phút kết nối lại -> đóng 4002 (KICKED)."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    await setup_test_game_session(
        redis_client, game_id=gid, host_id=hid, player_id=pid, player_nickname="BannedPlayer"
    )

    # Đặt cấm kick 10 phút
    await redis_client.set(f"game:{gid}:kicked_ids:{pid}", "1", ex=600)
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid)

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/game?ticket={ticket}"):
                pass
        assert exc_info.value.code == 4002


# =========================================================================
# 2. TEST SNAPSHOT KHI KẾT NỐI (HOST VS PLAYER)
# =========================================================================


@pytest.mark.asyncio
async def test_ws_host_connect_receives_host_snapshot(redis_client):
    """Host kết nối thành công: Nhận snapshot ban đầu với is_host=True."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    await setup_test_game_session(redis_client, game_id=gid, host_id=hid)
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=hid, is_host=True)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/game?ticket={ticket}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "snapshot"
            assert msg["v"] == 1
            data = msg["data"]
            assert data["is_host"] is True
            assert data["game_id"] == gid
            assert data["status"] == "lobby"
            assert data["q_index"] == 0
            assert data["total_questions"] == 2


@pytest.mark.asyncio
async def test_ws_player_connect_receives_player_snapshot(redis_client):
    """Player kết nối thành công: Nhận snapshot với is_host=False, score=0."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    await setup_test_game_session(
        redis_client, game_id=gid, host_id=hid, player_id=pid, player_nickname="Alice"
    )
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid, is_host=False)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/game?ticket={ticket}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "snapshot"
            data = msg["data"]
            assert data["is_host"] is False
            assert data["player_score"] == 0
            assert data["player_streak"] == 0


# =========================================================================
# 3. TEST GIỚI HẠN TỐC ĐỘ (RATE LIMIT 5 MSG/S - FR-GAME-21)
# =========================================================================


@pytest.mark.asyncio
async def test_ws_rate_limit_exceeded_returns_error(redis_client):
    """Gửi liên tiếp > 5 thông điệp trong 1s -> server trả error RATE_LIMITED."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    await setup_test_game_session(redis_client, game_id=gid, host_id=hid, player_id=pid)
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/game?ticket={ticket}") as ws:
            # Nhận snapshot ban đầu
            _ = ws.receive_json()

            # Gửi liên tiếp 7 heartbeat messages (5 gói đầu được chấp nhận, 2 gói sau vượt hạn mức)
            for _ in range(7):
                ws.send_json({"type": "heartbeat", "v": 1, "data": {}})

            # Đọc gói tin lỗi trả về do rate limit
            resp = ws.receive_json()
            assert resp.get("type") == "error"
            assert resp["data"].get("code") == "RATE_LIMITED"


# =========================================================================
# 4. TEST PHÂN QUYỀN (RBAC) GIỮA HOST VÀ PLAYER
# =========================================================================


@pytest.mark.asyncio
async def test_ws_player_forbidden_host_commands(redis_client):
    """Người chơi gửi lệnh host (game.start, game.next, game.end, player.kick) bị từ chối FORBIDDEN."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    await setup_test_game_session(redis_client, game_id=gid, host_id=hid, player_id=pid)
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid, is_host=False)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/game?ticket={ticket}") as ws:
            _ = ws.receive_json()  # Bỏ qua snapshot

            # 1. Thử gửi game.start
            ws.send_json({"type": "game.start", "v": 1, "data": {}})
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["data"]["code"] == "FORBIDDEN"

            # 2. Thử gửi game.next
            ws.send_json({"type": "game.next", "v": 1, "data": {}})
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["data"]["code"] == "FORBIDDEN"

            # 3. Thử gửi game.end
            ws.send_json({"type": "game.end", "v": 1, "data": {}})
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["data"]["code"] == "FORBIDDEN"

            # 4. Thử gửi player.kick
            ws.send_json({"type": "player.kick", "v": 1, "data": {"player_id": str(uuid.uuid4())}})
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["data"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_ws_host_forbidden_answer_submit(redis_client):
    """Host gửi lệnh answer.submit bị từ chối FORBIDDEN."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    await setup_test_game_session(redis_client, game_id=gid, host_id=hid)
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=hid, is_host=True)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/game?ticket={ticket}") as ws:
            _ = ws.receive_json()  # Bỏ qua snapshot

            ws.send_json(
                {
                    "type": "answer.submit",
                    "v": 1,
                    "data": {"q_index": 1, "selected_option_ids": [str(uuid.uuid4())]},
                }
            )
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["data"]["code"] == "FORBIDDEN"


# =========================================================================
# 5. TEST HOST START GAME VÀ KHÔNG LỘ ĐÁP ÁN (FR-GAME-08)
# =========================================================================


@pytest.mark.asyncio
async def test_ws_host_start_game_question_start_no_correct_answer(redis_client):
    """Host bấm game.start -> Broadcast question.start. TUYỆT ĐỐI KHÔNG CHỨA is_correct."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    _ = await setup_test_game_session(redis_client, game_id=gid, host_id=hid, player_id=pid)

    host_ticket = await create_test_ticket(redis_client, game_id=gid, sub=hid, is_host=True)
    player_ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid, is_host=False)

    with TestClient(app) as client:
        # Host và Player cùng mở kết nối
        with (
            client.websocket_connect(f"/ws/game?ticket={host_ticket}") as host_ws,
            client.websocket_connect(f"/ws/game?ticket={player_ticket}") as player_ws,
        ):
            # Nhận snapshot ban đầu
            _ = host_ws.receive_json()
            _ = player_ws.receive_json()

            # Host gửi lệnh game.start
            host_ws.send_json({"type": "game.start", "v": 1, "data": {}})

            # Cả Host và Player đều phải nhận được question.start
            q_start_player = player_ws.receive_json()
            assert q_start_player["type"] == WSMessageType.QUESTION_START.value
            assert q_start_player["v"] == 1
            assert q_start_player.get("seq") is not None

            q_data = q_start_player["data"]
            assert q_data["q_index"] == 1
            assert q_data["total_questions"] == 2
            assert q_data["content"] == "Thủ đô của Việt Nam là gì?"
            assert q_data["time_limit_s"] == 15
            assert q_data["server_time"] > 0
            assert q_data["deadline"] > q_data["server_time"]

            # =====================================================================
            # KIỂM TRA BẢO MẬT TUYỆT ĐỐI (FR-GAME-08): KHÔNG LỘ ĐÁP ÁN
            # =====================================================================
            raw_json = json.dumps(q_start_player)
            assert "is_correct" not in raw_json, (
                "question.start TUYỆT ĐỐI KHÔNG ĐƯỢC CHỨA 'is_correct'!"
            )
            assert "correct" not in raw_json.lower() or "correct_keys" in raw_json

            # Kiểm tra từng option
            options = q_data["options"]
            assert len(options) == 2
            for opt in options:
                assert "is_correct" not in opt
                assert "id" in opt
                assert "content" in opt
                assert "position" in opt


# =========================================================================
# 6. TEST NỘP BÀI VÀ ANSWER.ACK (FR-GAME-07, FR-GAME-09)
# =========================================================================


@pytest.mark.asyncio
async def test_ws_player_submit_answer_ack_and_idempotency(redis_client):
    """Nộp bài hợp lệ -> answer.ack accepted=True (không lộ đúng/sai). Nộp lại -> ALREADY_ANSWERED."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    opts = await setup_test_game_session(redis_client, game_id=gid, host_id=hid, player_id=pid)
    # Thêm player 2 vào phòng để không kích hoạt đóng sớm ngay khi player 1 nộp bài
    other_pid = str(uuid.uuid4())
    await redis_client.hset(
        f"game:{gid}:players",
        "player_two",
        json.dumps({"id": other_pid, "nickname": "PlayerTwo", "online": True}),
    )

    host_ticket = await create_test_ticket(redis_client, game_id=gid, sub=hid, is_host=True)
    player_ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid, is_host=False)

    with TestClient(app) as client:
        with (
            client.websocket_connect(f"/ws/game?ticket={host_ticket}") as host_ws,
            client.websocket_connect(f"/ws/game?ticket={player_ticket}") as player_ws,
        ):
            _ = host_ws.receive_json()
            _ = player_ws.receive_json()

            # Host bắt đầu câu 1
            host_ws.send_json({"type": "game.start", "v": 1, "data": {}})
            _ = player_ws.receive_json()  # Nhận question.start

            # Player nộp đáp án câu 1
            player_ws.send_json(
                {
                    "type": "answer.submit",
                    "v": 1,
                    "data": {
                        "q_index": 1,
                        "selected_option_ids": [opts["opt1_correct"]],
                    },
                }
            )

            ack_msg = player_ws.receive_json()
            assert ack_msg["type"] == WSMessageType.ANSWER_ACK.value
            ack_data = ack_msg["data"]
            assert ack_data["accepted"] is True
            assert ack_data["q_index"] == 1
            assert ack_data.get("reason") is None

            # TUYỆT ĐỐI KHÔNG BÁO ĐÚNG/SAI Ở BƯỚC NÀY
            raw_ack = json.dumps(ack_msg)
            assert "is_correct" not in raw_ack
            assert "points_awarded" not in raw_ack

            # Nộp lại lần 2 -> bị từ chối với lý do ALREADY_ANSWERED (idempotent)
            player_ws.send_json(
                {
                    "type": "answer.submit",
                    "v": 1,
                    "data": {
                        "q_index": 1,
                        "selected_option_ids": [opts["opt2_wrong"]],
                    },
                }
            )
            ack_2 = player_ws.receive_json()
            assert ack_2["type"] == WSMessageType.ANSWER_ACK.value
            assert ack_2["data"]["accepted"] is False
            assert ack_2["data"]["reason"] == "ALREADY_ANSWERED"


# =========================================================================
# 7. TEST HOST KICK NGƯỜI CHƠI (FR-GAME-05)
# =========================================================================


@pytest.mark.asyncio
async def test_ws_host_kick_player_disconnects_with_4002(redis_client):
    """Host gửi lệnh player.kick -> WebSocket người bị kick lập tức đóng với mã 4002."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    await setup_test_game_session(
        redis_client, game_id=gid, host_id=hid, player_id=pid, player_nickname="Bob"
    )

    host_ticket = await create_test_ticket(redis_client, game_id=gid, sub=hid, is_host=True)
    player_ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid, is_host=False)

    with TestClient(app) as client:
        with (
            client.websocket_connect(f"/ws/game?ticket={host_ticket}") as host_ws,
            client.websocket_connect(f"/ws/game?ticket={player_ticket}") as player_ws,
        ):
            _ = host_ws.receive_json()
            _ = player_ws.receive_json()

            # Host gửi lệnh kick Bob
            host_ws.send_json(
                {
                    "type": "player.kick",
                    "v": 1,
                    "data": {"player_id": pid},
                }
            )

            # Chờ player_ws bị đóng bởi server với mã 4002
            with pytest.raises(WebSocketDisconnect) as exc_info:
                # Đọc tin tiếp theo trên socket player -> socket bị đóng
                player_ws.receive_json()
            assert exc_info.value.code == 4002

            # Host nhận được lobby.update
            host_lobby = host_ws.receive_json()
            assert host_lobby["type"] == WSMessageType.LOBBY_UPDATE.value


# =========================================================================
# 8. TEST RECONNECT GIỮA TRẬN (FR-GAME-17)
# =========================================================================


@pytest.mark.asyncio
async def test_ws_reconnect_during_active_question_receives_snapshot(redis_client):
    """Người chơi kết nối lại giữa câu hỏi nhận đúng snapshot có câu hỏi hiện tại."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    _ = await setup_test_game_session(redis_client, game_id=gid, host_id=hid, player_id=pid)

    # Đưa trận vào trạng thái question_active câu 1
    now_res = await redis_client.time()
    now_ms = (now_res[0] * 1000) + (now_res[1] // 1000)
    deadline = now_ms + 15000

    await redis_client.hset(
        f"game:{gid}:state",
        mapping={
            "status": "question_active",
            "q_index": "1",
            "deadline": str(deadline),
        },
    )
    # Giả lập điểm trước đó
    await redis_client.zadd(f"game:{gid}:lb", {pid: 750})
    await redis_client.hset(f"game:{gid}:streaks", pid, "2")

    ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid, is_host=False)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/game?ticket={ticket}") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot"
            data = snap["data"]
            assert data["status"] == "question_active"
            assert data["q_index"] == 1
            assert data["player_score"] == 750
            assert data["player_streak"] == 2
            assert data["player_rank"] == 1

            # Câu hỏi hiện tại
            cur_q = data["current_question"]
            assert cur_q is not None
            assert cur_q["q_index"] == 1
            assert cur_q["content"] == "Thủ đô của Việt Nam là gì?"

            # TUYỆT ĐỐI KHÔNG CHỨA is_correct
            raw_snap = json.dumps(snap)
            assert "is_correct" not in raw_snap


# =========================================================================
# 9. TEST HOST GAME NEXT VÀ CHỐNG BẤM ĐÚP (GM-14)
# =========================================================================


@pytest.mark.asyncio
async def test_ws_host_game_next_and_double_click_protection(redis_client):
    """Host gọi game.next chuyển sang câu 2; gọi trùng expected_q_index bị chặn VERSION_MISMATCH."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    await setup_test_game_session(redis_client, game_id=gid, host_id=hid, player_id=pid)

    host_ticket = await create_test_ticket(redis_client, game_id=gid, sub=hid, is_host=True)
    player_ticket = await create_test_ticket(redis_client, game_id=gid, sub=pid, is_host=False)

    with TestClient(app) as client:
        with (
            client.websocket_connect(f"/ws/game?ticket={host_ticket}") as host_ws,
            client.websocket_connect(f"/ws/game?ticket={player_ticket}") as player_ws,
        ):
            _ = host_ws.receive_json()
            _ = player_ws.receive_json()

            # Host start câu 1
            host_ws.send_json({"type": "game.start", "v": 1, "data": {}})
            _ = host_ws.receive_json()  # Host nhận broadcast question.start câu 1
            _ = player_ws.receive_json()  # Player nhận broadcast question.start câu 1

            # Đóng câu 1 (giả lập hết giờ chuyển sang question_result)
            await redis_client.hset(f"game:{gid}:state", "status", "question_result")

            # Host bấm game.next với expected_q_index = 2
            host_ws.send_json({"type": "game.next", "v": 1, "data": {"expected_q_index": 2}})

            # Host và Player đều nhận question.start câu 2
            _ = host_ws.receive_json()
            q2_player = player_ws.receive_json()
            assert q2_player["type"] == WSMessageType.QUESTION_START.value
            assert q2_player["data"]["q_index"] == 2
            assert q2_player["data"]["content"] == "Python ra đời năm nào?"
            assert "is_correct" not in json.dumps(q2_player)

            # Thử gọi lại game.next với expected_q_index cũ hoặc khi đang question_active -> báo lỗi
            host_ws.send_json({"type": "game.next", "v": 1, "data": {"expected_q_index": 2}})
            err = host_ws.receive_json()
            assert err["type"] == "error"
            assert err["data"]["code"] in ("INVALID_STATE", "VERSION_MISMATCH")


# =========================================================================
# 10. TEST HOST GAME END VÀ BAD MESSAGE VALIDATION
# =========================================================================


@pytest.mark.asyncio
async def test_ws_host_game_end_and_bad_message(redis_client):
    """Host kết thúc trận đấu và test validate thông điệp sai quy chuẩn."""
    gid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    await setup_test_game_session(redis_client, game_id=gid, host_id=hid)
    ticket = await create_test_ticket(redis_client, game_id=gid, sub=hid, is_host=True)

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/game?ticket={ticket}") as ws:
            _ = ws.receive_json()  # Snapshot

            # 1. Gửi chuỗi không phải JSON
            ws.send_text("Not a json at all")
            err1 = ws.receive_json()
            assert err1["type"] == "error"
            assert err1["data"]["code"] == "BAD_MESSAGE"

            # 2. Gửi phiên bản sai v=99
            ws.send_json({"type": "heartbeat", "v": 99, "data": {}})
            err2 = ws.receive_json()
            assert err2["type"] == "error"
            assert err2["data"]["code"] == "UNSUPPORTED_VERSION"

            # 3. Host kết thúc trận đấu
            ws.send_json({"type": "game.end", "v": 1, "data": {}})
            end_msg = ws.receive_json()
            assert end_msg["type"] == WSMessageType.GAME_FINISHED.value


# =========================================================================
# 11. TEST DEMO PAGE ENDPOINT
# =========================================================================


def test_demo_page_endpoint():
    """Kiểm tra trang HTML demo (/demo) phản hồi HTTP 200."""
    with TestClient(app) as client:
        resp = client.get("/demo")
        assert resp.status_code == 200
        assert "QuizArena Live Engine" in resp.text
        assert "Host Điều Khiển" in resp.text
        assert "Người Chơi (Player)" in resp.text
