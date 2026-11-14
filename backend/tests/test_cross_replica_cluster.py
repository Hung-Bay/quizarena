"""Kiểm thử điều khiển và broadcast xuyên replica qua Redis Pub/Sub (Phase 5 - Thứ Hai).

Kiểm tra:
1. Lệnh kick người chơi xuyên replica:
   - Host kết nối ở Replica A (ConnectionManager A).
   - Player kết nối ở Replica B (ConnectionManager B).
   - Host phát lệnh kick -> Replica B nhận message qua Pub/Sub và ngắt kết nối Player với code 4002 (KICKED).
2. Broadcast thông điệp xuyên replica:
   - Replica A phát thông điệp -> Client ở Replica B nhận được đầy đủ với seq tăng dần.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.realtime.connection_manager import ConnectionManager
from app.realtime.handlers import MessageHandler

REDIS_TEST_URL = os.environ.get(
    "REDIS_TEST_URL",
    os.environ.get("REDIS_URL", "redis://:AK.Gamer1@localhost:6380/0"),
)


@pytest_asyncio.fixture
async def redis_client():
    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Cần Redis server đang chạy trên localhost:6379: {exc}")
    yield client
    await client.aclose()


class FakeWebSocket:
    """Mock WebSocket để hứng các lệnh gửi và đóng."""

    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.closed_code: int | None = None
        self.closed_reason: str | None = None

    async def send_text(self, text: str) -> None:
        self.sent_messages.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code
        self.closed_reason = reason


@pytest.mark.asyncio
async def test_cross_replica_kick_disconnects_player(redis_client: Redis) -> None:
    """Host ở Replica A kick người chơi ở Replica B -> Player bị đóng kết nối với code 4002."""
    game_id = f"test-game-{uuid.uuid4().hex[:8]}"
    host_id = f"host-{uuid.uuid4().hex[:6]}"
    player_id = f"player-{uuid.uuid4().hex[:6]}"

    # Thiết lập dữ liệu game cơ bản trên Redis
    await redis_client.hset(
        f"game:{game_id}:state",
        mapping={"status": "lobby", "host_id": host_id, "game_id": game_id},
    )
    await redis_client.hset(f"game:{game_id}:nicknames", player_id, "BraveWarrior")
    await redis_client.hset(f"game:{game_id}:players", "bravewarrior", "{}")

    # Giả lập 2 replica độc lập dùng chung Redis
    redis_a = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    redis_b = Redis.from_url(REDIS_TEST_URL, decode_responses=True)

    cm_a = ConnectionManager(redis_a)
    cm_b = ConnectionManager(redis_b)

    ws_host = FakeWebSocket()
    ws_player = FakeWebSocket()

    try:
        # Host kết nối vào Replica A
        conn_host = await cm_a.connect(
            ws=ws_host,  # type: ignore[arg-type]
            game_id=game_id,
            client_id=host_id,
            nickname="Host",
            is_host=True,
        )

        # Player kết nối vào Replica B
        await cm_b.connect(
            ws=ws_player,  # type: ignore[arg-type]
            game_id=game_id,
            client_id=player_id,
            nickname="BraveWarrior",
            is_host=False,
        )

        # Chờ 50ms để các Pub/Sub listener trên 2 replica đăng ký hoàn tất
        await asyncio.sleep(0.05)

        # Handler trên Replica A nhận lệnh kick từ Host
        handler_a = MessageHandler(cm_a, redis_a)
        kick_message = {
            "type": "player.kick",
            "v": 1,
            "data": {"player_id": player_id},
        }
        await handler_a.handle_message(conn_host, json.dumps(kick_message))

        # Chờ thông điệp lan truyền qua Redis Pub/Sub đến Replica B
        for _ in range(20):
            if ws_player.closed_code is not None:
                break
            await asyncio.sleep(0.05)

        # Kiểm chứng Player trên Replica B đã bị ngắt kết nối với mã 4002 (KICKED)
        assert ws_player.closed_code == 4002
        assert ws_player.closed_reason == "KICKED"

        # Kiểm chứng Redis đã lưu cấm 10 phút
        assert await redis_client.exists(f"game:{game_id}:kicked_ids:{player_id}") == 1
        assert await redis_client.exists(f"game:{game_id}:kicked:bravewarrior") == 1

    finally:
        await cm_a.close_all()
        await cm_b.close_all()
        await redis_a.aclose()
        await redis_b.aclose()
        # Dọn dẹp Redis
        keys = await redis_client.keys(f"game:{game_id}:*")
        if keys:
            await redis_client.delete(*keys)


@pytest.mark.asyncio
async def test_cross_replica_broadcast_delivery(redis_client: Redis) -> None:
    """Thông điệp phát từ Replica A được phân phối tới client trên Replica B."""
    game_id = f"test-game-{uuid.uuid4().hex[:8]}"

    redis_a = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    redis_b = Redis.from_url(REDIS_TEST_URL, decode_responses=True)

    cm_a = ConnectionManager(redis_a)
    cm_b = ConnectionManager(redis_b)

    ws_client_a = FakeWebSocket()
    ws_client_b = FakeWebSocket()

    try:
        await cm_a.connect(
            ws=ws_client_a,  # type: ignore[arg-type]
            game_id=game_id,
            client_id="client-a",
            nickname="PlayerA",
            is_host=False,
        )

        await cm_b.connect(
            ws=ws_client_b,  # type: ignore[arg-type]
            game_id=game_id,
            client_id="client-b",
            nickname="PlayerB",
            is_host=False,
        )

        await asyncio.sleep(0.05)

        # Replica A broadcast một thông điệp
        test_payload = {
            "type": "question.start",
            "v": 1,
            "data": {"q_index": 1, "deadline": 1700000000000},
        }
        await cm_a.broadcast_game(game_id, test_payload)

        # Chờ thông điệp đến cả 2 replica
        for _ in range(20):
            if ws_client_a.sent_messages and ws_client_b.sent_messages:
                break
            await asyncio.sleep(0.05)

        # Cả 2 client đều nhận được thông điệp với seq tự tăng giống nhau
        assert len(ws_client_a.sent_messages) >= 1
        assert len(ws_client_b.sent_messages) >= 1

        msg_a = json.loads(ws_client_a.sent_messages[-1])
        msg_b = json.loads(ws_client_b.sent_messages[-1])

        assert msg_a["type"] == "question.start"
        assert msg_b["type"] == "question.start"
        assert msg_a["seq"] == msg_b["seq"]
        assert msg_a["data"]["q_index"] == 1

    finally:
        await cm_a.close_all()
        await cm_b.close_all()
        await redis_a.aclose()
        await redis_b.aclose()
        keys = await redis_client.keys(f"game:{game_id}:*")
        if keys:
            await redis_client.delete(*keys)
