"""API xác thực WebSocket: /realtime/ws-ticket (AU-11)."""

import json
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from app.api.deps import get_redis, get_token_claims
from app.core.errors import AppError
from app.schemas.realtime import WsTicketRequest, WsTicketResponse

router = APIRouter(prefix="/realtime", tags=["realtime"])

RedisClient = Annotated[Redis, Depends(get_redis)]
Claims = Annotated[dict[str, Any], Depends(get_token_claims)]


@router.post("/ws-ticket", response_model=WsTicketResponse)
async def create_ws_ticket(
    body: WsTicketRequest,
    claims: Claims,
    redis: RedisClient,
) -> WsTicketResponse:
    """Tạo vé dùng 1 lần (TTL 30 giây) để kết nối WebSocket không cần truyền Authorization header (AU-11)."""
    # 1. Kiểm tra quyền game:play hoặc game:host_own
    permissions = set(claims.get("permissions", []))
    if not ({"game:play"} <= permissions or {"game:host_own"} <= permissions):
        raise AppError(
            "Không có quyền kết nối vào trận đấu",
            code="FORBIDDEN",
            status_code=403,
        )

    # 2. Nếu là guest token, chỉ cho phép tạo ticket cho đúng trận đã tham gia (AU-9)
    if claims.get("type") == "guest":
        token_game_id = claims.get("game_id")
        if token_game_id != str(body.game_id):
            raise AppError(
                "Guest token không hợp lệ cho trận đấu này",
                code="FORBIDDEN",
                status_code=403,
            )

    # 3. Sinh ticket ngẫu nhiên, lưu thông tin định danh vào Redis với TTL 30 giây
    ticket = secrets.token_urlsafe(32)
    ticket_data = {
        "sub": claims["sub"],
        "game_id": str(body.game_id),
        "roles": claims.get("roles", []),
        "permissions": claims.get("permissions", []),
        "type": claims.get("type", "access"),
    }
    await redis.set(f"ws:ticket:{ticket}", json.dumps(ticket_data), ex=30)

    return WsTicketResponse(ticket=ticket, expires_in=30)


async def redeem_ws_ticket(redis: Redis, ticket: str) -> dict[str, Any] | None:
    """Đổi ticket lấy danh tính người dùng và xóa ngay lập tức (GETDEL - AU-11)."""
    raw = await redis.getdel(f"ws:ticket:{ticket}")
    if not raw:
        return None
    return json.loads(raw)
