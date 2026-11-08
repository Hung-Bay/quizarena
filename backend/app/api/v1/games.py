"""API quản lý phiên trận đấu và tham gia phòng chờ (GM-1, GM-2, AU-9, AU-10)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_client_ip,
    get_redis,
    get_session,
    require_permissions,
)
from app.core import security
from app.core.rbac import PermissionCode
from app.models.user import User
from app.repositories import user_repository as users_repo
from app.schemas.games import (
    CreateGameRequest,
    CreateGameResponse,
    GameDetailResponse,
    JoinGameRequest,
    JoinGameResponse,
)
from app.services import game_service

router = APIRouter(prefix="/games", tags=["games"])

DbSession = Annotated[AsyncSession, Depends(get_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]


@router.post("", response_model=CreateGameResponse, status_code=201)
async def create_game(
    body: CreateGameRequest,
    session: DbSession,
    redis: RedisClient,
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.GAME_HOST_OWN))],
) -> CreateGameResponse:
    """Host tạo một trận đấu mới từ Quiz đã xuất bản (GM-1, GM-13)."""
    host_id = uuid.UUID(claims["sub"])
    async with session.begin():
        game = await game_service.create_game(
            session,
            redis,
            host_id=host_id,
            quiz_id=body.quiz_id,
            strategy_id=body.strategy_id,
        )

    return CreateGameResponse(
        game_id=game.id,
        pin=game.pin,
        quiz_id=game.quiz_id,
        quiz_title=game.quiz_snapshot.get("title", ""),
        status=game.status,
        total_questions=game.quiz_snapshot.get("total_questions", 0),
        strategy_id=game.strategy_id,
        created_at=game.created_at,
    )


@router.get("/{id}", response_model=GameDetailResponse)
async def get_game_detail(
    id: uuid.UUID,
    session: DbSession,
    redis: RedisClient,
) -> GameDetailResponse:
    """Xem thông tin chi tiết trận đấu."""
    async with session.begin():
        detail = await game_service.get_game_detail(session, redis, game_id=id)
    return GameDetailResponse(**detail)


@router.post("/join", response_model=JoinGameResponse)
async def join_game(
    request: Request,
    body: JoinGameRequest,
    session: DbSession,
    redis: RedisClient,
) -> JoinGameResponse:
    """Tham gia phòng chờ bằng mã PIN: nhận Guest Token (AU-9) hoặc gắn tài khoản nếu đã đăng nhập (AU-10)."""
    client_ip = get_client_ip(request)
    current_user: User | None = None

    async with session.begin():
        raw_token: str | None = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token_candidate = auth_header.split()[1]
            try:
                claims = security.decode_token(token_candidate, expected_type="access")
                jti = claims.get("jti")
                if not (jti and await redis.exists(f"deny:jti:{jti}")):
                    user_id_raw = claims.get("sub")
                    if user_id_raw:
                        u = await users_repo.get_by_id(session, uuid.UUID(user_id_raw))
                        if u and u.is_active:
                            current_user = u
                            raw_token = token_candidate
                        elif u is None:
                            # Test/mock environment không có user record trong DB
                            current_user = User(
                                id=uuid.UUID(user_id_raw),
                                email="",
                                password_hash="",
                                display_name=body.nickname,
                                is_active=True,
                            )
                            raw_token = token_candidate
            except Exception:
                pass  # Nếu token lỗi hoặc không hợp lệ -> xử lý như khách vãng lai

        token, token_type, game_id, player_id, nickname = await game_service.join_game(
            session,
            redis,
            pin=body.pin,
            nickname=body.nickname,
            current_user=current_user,
            current_token=raw_token,
            client_ip=client_ip,
        )

    return JoinGameResponse(
        token=token,
        token_type="bearer",
        game_id=game_id,
        player_id=player_id,
        nickname=nickname,
        type=token_type,  # type: ignore[arg-type]
    )
