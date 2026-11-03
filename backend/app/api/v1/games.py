"""API tham gia trận đấu (AU-9, AU-10)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis

from app.api.deps import get_redis
from app.core import security
from app.core.errors import AppError
from app.schemas.games import JoinGameRequest, JoinGameResponse

router = APIRouter(prefix="/games", tags=["games"])

RedisClient = Annotated[Redis, Depends(get_redis)]


@router.post("/join", response_model=JoinGameResponse)
async def join_game(
    request: Request,
    body: JoinGameRequest,
    redis: RedisClient,
) -> JoinGameResponse:
    """Tham gia phòng chờ bằng mã PIN: trả về guest token (AU-9) hoặc giữ access token nếu đã đăng nhập (AU-10)."""
    # 1. Tra mã PIN trong Redis: pin:{pin} -> game_id (GM-1)
    game_id_raw = await redis.get(f"pin:{body.pin}")
    if not game_id_raw:
        raise AppError(
            "Mã PIN không tồn tại hoặc trận đấu đã kết thúc",
            code="GAME_NOT_FOUND",
            status_code=404,
        )

    game_id = uuid.UUID(game_id_raw)

    # 2. AU-10: Nếu đã đăng nhập, ưu tiên dùng access token của người chơi
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split()[1]
        try:
            claims = security.decode_token(token, expected_type="access")
            jti = claims.get("jti")
            if not (jti and await redis.exists(f"deny:jti:{jti}")):
                return JoinGameResponse(
                    token=token,
                    game_id=game_id,
                    nickname=body.nickname,
                    type="player",
                )
        except security.TokenError:
            pass  # Nếu token lỗi hoặc hết hạn thì chuyển sang cấp guest token

    # 3. AU-9: Tạo guest token sống tối đa 3 giờ chỉ dành riêng cho game_id này
    guest_token = security.create_guest_token(game_id=game_id)
    return JoinGameResponse(
        token=guest_token,
        game_id=game_id,
        nickname=body.nickname,
        type="guest",
    )
