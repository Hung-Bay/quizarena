"""Kiểm thử cơ chế Graceful Drain cho WebSocket Realtime Service (Phase 5 - Thứ Ba).

Các trường hợp kiểm thử:
1. Endpoint /readyz:
   - Bình thường trả về HTTP 200 {"status": "ready"}.
   - Khi đang drain trả về HTTP 503 {"status": "draining"}.
2. Từ chối kết nối mới bằng mã 1013:
   - Khi replica đang drain, client mở kết nối mới bị từ chối với mã 1013 (Try Again Later) và reason "SERVER_DRAINING".
3. Phát server.draining và đóng kết nối 1001:
   - Khi kích hoạt drain_and_close_all, toàn bộ kết nối đang mở nhận được envelope "server.draining" kèm retry_after_ms,
     sau đó được đóng êm với mã 1001 (Going Away).
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from starlette.websockets import WebSocketDisconnect

from app.main_realtime import app as realtime_app
from app.realtime.connection_manager import ConnectionManager

REDIS_TEST_URL = "redis://:AK.Gamer1@localhost:6380/0"


class MockWebSocket:
    """Mock WebSocket hỗ trợ kiểm tra thông điệp nhận và mã đóng."""

    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.closed_code: int | None = None
        self.closed_reason: str | None = None

    async def send_text(self, text: str) -> None:
        self.sent_messages.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code
        self.closed_reason = reason


def test_readyz_status_transition():
    """Kiểm tra /readyz chuyển từ 200 sang 503 khi bật cờ drain."""
    with TestClient(realtime_app) as client:
        # 1. Trạng thái bình thường
        realtime_app.state.is_draining = False
        r_normal = client.get("/readyz")
        assert r_normal.status_code == 200
        assert r_normal.json()["status"] == "ready"

        # 2. Trạng thái Draining
        realtime_app.state.is_draining = True
        r_draining = client.get("/readyz")
        assert r_draining.status_code == 503
        assert r_draining.json()["status"] == "draining"

        # Khôi phục trạng thái
        realtime_app.state.is_draining = False


def test_ws_connect_rejected_with_1013_when_draining():
    """Khi server đang drain, kết nối mới bị từ chối với mã 1013 (Try Again Later)."""
    with TestClient(realtime_app) as client:
        realtime_app.state.is_draining = True
        try:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/game?ticket=valid_ticket_123"):
                    pass
            assert exc_info.value.code == 1013
        finally:
            realtime_app.state.is_draining = False


@pytest.mark.asyncio
async def test_drain_and_close_all_notifies_and_closes():
    """drain_and_close_all gửi server.draining kèm retry_after_ms rồi đóng 1001."""
    # Khởi tạo mock Redis
    redis_mock = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await redis_mock.ping()
    except Exception:
        pytest.skip("Cần Redis chạy trên localhost:6380 để test")

    cm = ConnectionManager(redis_mock)
    game_id = f"drain-game-{uuid.uuid4().hex[:6]}"

    ws1 = MockWebSocket()
    ws2 = MockWebSocket()

    try:
        await cm.connect(
            ws=ws1,  # type: ignore[arg-type]
            game_id=game_id,
            client_id="p1",
            nickname="Player1",
            is_host=False,
        )
        await cm.connect(
            ws=ws2,  # type: ignore[arg-type]
            game_id=game_id,
            client_id="p2",
            nickname="Player2",
            is_host=False,
        )

        # Kích hoạt drain
        await cm.drain_and_close_all(reason="SCALE_DOWN")

        # 1. Kiểm tra cả 2 client đều nhận được tin nhắn server.draining
        assert len(ws1.sent_messages) >= 1
        assert len(ws2.sent_messages) >= 1

        msg1 = json.loads(ws1.sent_messages[0])
        msg2 = json.loads(ws2.sent_messages[0])

        assert msg1["type"] == "server.draining"
        assert msg2["type"] == "server.draining"
        assert "retry_after_ms" in msg1["data"]
        assert 200 <= msg1["data"]["retry_after_ms"] <= 800

        # 2. Kiểm tra cả 2 client đều bị đóng với mã 1001 (Going Away)
        assert ws1.closed_code == 1001
        assert ws2.closed_code == 1001

        # 3. Kiểm tra active_connections đã được dọn sạch
        assert len(cm.active_connections) == 0

    finally:
        await cm.close_all()
        await redis_mock.aclose()
