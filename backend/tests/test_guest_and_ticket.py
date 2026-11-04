"""Test Guest token (POST /games/join) và WebSocket ticket (POST /realtime/ws-ticket) (AU-9, AU-10, AU-11)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_redis, get_session, require_permissions
from app.api.v1.realtime import redeem_ws_ticket
from app.core import security
from app.core.rbac import PermissionCode
from app.main_api import app

# Sử dụng in-memory dict để giả lập Redis phục vụ test độc lập
redis_store: dict[str, Any] = {}


class FakeRedis:
    def __init__(self) -> None:
        self.store = redis_store

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.store

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


async def fake_get_session() -> AsyncIterator[AsyncMock]:
    yield AsyncMock()


fake_redis = FakeRedis()


# Thêm endpoint test bảo vệ bằng permission để kiểm tra guest token gọi API khác bị 403
@app.get("/api/v1/test-host-only")
async def _host_only_endpoint(
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.QUIZ_WRITE_OWN))],
) -> dict[str, str]:
    return {"status": "ok"}


client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_dependencies():
    redis_store.clear()
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_session] = fake_get_session
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)


# ------------------------------------------------------------------ AU-9, AU-10: POST /games/join


def test_join_game_pin_khong_ton_tai_tra_404() -> None:
    r = client.post("/api/v1/games/join", json={"pin": "999999", "nickname": "Player1"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "GAME_NOT_FOUND"


def test_join_game_guest_tao_guest_token_dung_3_gio() -> None:
    game_id = uuid.uuid4()
    redis_store["pin:123456"] = str(game_id)

    r = client.post("/api/v1/games/join", json={"pin": "123456", "nickname": "Khach1"})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "guest"
    assert body["game_id"] == str(game_id)
    assert body["nickname"] == "Khach1"

    token = body["token"]
    claims = security.decode_token(token, expected_type="guest")
    assert claims["type"] == "guest"
    assert claims["game_id"] == str(game_id)
    assert claims["permissions"] == ["game:play"]
    # AU-9: sống tối đa 3 giờ
    assert claims["exp"] - claims["iat"] <= 3 * 3600


def test_join_game_da_dang_nhap_giu_access_token_player() -> None:
    game_id = uuid.uuid4()
    redis_store["pin:123456"] = str(game_id)

    user_id = uuid.uuid4()
    player_token = security.create_access_token(
        user_id=user_id,
        roles=["PLAYER"],
        permissions=["game:play", "history:read_own", "quiz:read_public"],
        token_version=0,
    )

    r = client.post(
        "/api/v1/games/join",
        json={"pin": "123456", "nickname": "AnNguyen"},
        headers={"Authorization": f"Bearer {player_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "player"
    assert body["token"] == player_token
    assert body["game_id"] == str(game_id)


# ------------------------------------------------------------------ Guest token phạm vi & hạn mức


def test_guest_token_sai_tran_bi_tu_choi_khi_lay_ticket() -> None:
    game_a = uuid.uuid4()
    game_b = uuid.uuid4()

    guest_token_a = security.create_guest_token(game_id=game_a)

    # Dùng guest token của game A để xin ticket vào game B -> 403
    r = client.post(
        "/api/v1/realtime/ws-ticket",
        json={"game_id": str(game_b)},
        headers={"Authorization": f"Bearer {guest_token_a}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    assert "Guest token không hợp lệ cho trận đấu này" in r.json()["error"]["message"]


def test_guest_token_het_han_bi_tu_choi() -> None:
    game_id = uuid.uuid4()
    old = datetime.now(UTC) - timedelta(hours=4)  # Đã quá 3 giờ
    expired_token = security.create_guest_token(game_id=game_id, now=old)

    r = client.post(
        "/api/v1/realtime/ws-ticket",
        json={"game_id": str(game_id)},
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_guest_token_goi_api_khac_bi_403() -> None:
    # Guest token chỉ có quyền game:play, gọi API khác yêu cầu quyền khác bị 403
    game_id = uuid.uuid4()
    guest_token = security.create_guest_token(game_id=game_id)

    r = client.get("/api/v1/test-host-only", headers={"Authorization": f"Bearer {guest_token}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


# ------------------------------------------------------------------ AU-11: ws-ticket & GETDEL


@pytest.mark.asyncio
async def test_ws_ticket_thanh_cong_va_doi_bang_getdel_dung_lan_2_that_bai() -> None:
    game_id = uuid.uuid4()
    guest_token = security.create_guest_token(game_id=game_id)

    # 1. Gọi POST /realtime/ws-ticket
    r = client.post(
        "/api/v1/realtime/ws-ticket",
        json={"game_id": str(game_id)},
        headers={"Authorization": f"Bearer {guest_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "ticket" in body
    assert body["expires_in"] == 30
    ticket = body["ticket"]

    # 2. Server đổi ticket lấy danh tính lần 1 bằng GETDEL -> thành công
    data = await redeem_ws_ticket(fake_redis, ticket)  # type: ignore[arg-type]
    assert data is not None
    assert data["game_id"] == str(game_id)
    assert data["type"] == "guest"

    # 3. Đổi lần 2 -> thất bại vì ticket đã bị xóa ngay (GETDEL)
    data_second = await redeem_ws_ticket(fake_redis, ticket)  # type: ignore[arg-type]
    assert data_second is None


@pytest.mark.asyncio
async def test_ws_ticket_khong_ton_tai_hoac_qua_30_giay_that_bai() -> None:
    # Ticket không có trong Redis (hoặc đã hết hạn sau 30 giây)
    data = await redeem_ws_ticket(fake_redis, "ticket-khong-ton-tai")  # type: ignore[arg-type]
    assert data is None
