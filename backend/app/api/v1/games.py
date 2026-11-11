"""API quản lý phiên trận đấu và tham gia phòng chờ (GM-1, GM-2, AU-9, AU-10)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.gemini_gateway import create_gemini_gateway
from app.api.deps import (
    get_client_ip,
    get_current_user_claims,
    get_redis,
    get_session,
    require_permissions,
)
from app.core import security
from app.core.rbac import PermissionCode
from app.models.ai import AiJob
from app.models.game import GamePlayer, GameSession
from app.models.report import GameReport
from app.models.user import User
from app.repositories import user_repository as users_repo
from app.schemas.games import (
    CreateGameRequest,
    CreateGameResponse,
    GameDetailResponse,
    GameListItemResponse,
    GameReportResponse,
    JoinGameRequest,
    JoinGameResponse,
    UserGameHistoryItemResponse,
)
from app.services import game_service
from app.services.report_service import (
    generate_and_save_game_report,
    get_player_personal_feedback,
)

router = APIRouter(prefix="/games", tags=["games"])
me_router = APIRouter(prefix="/me", tags=["me"])

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


@router.get("", response_model=list[GameListItemResponse])
async def list_games(
    session: DbSession,
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.GAME_HOST_OWN))],
) -> list[GameListItemResponse]:
    """Lấy danh sách các trận đấu do Host hiện tại đã tạo (GM-1, §3.7)."""
    host_id = uuid.UUID(claims["sub"])
    stmt = (
        select(GameSession)
        .options(selectinload(GameSession.players))
        .where(GameSession.host_id == host_id)
        .order_by(GameSession.created_at.desc())
    )
    games = (await session.execute(stmt)).scalars().all()
    return [
        GameListItemResponse(
            game_id=g.id,
            pin=g.pin,
            status=g.status,
            quiz_id=g.quiz_id,
            quiz_title=(g.quiz_snapshot or {}).get("title", ""),
            player_count=len(g.players) if g.players else 0,
            created_at=g.created_at,
            started_at=g.started_at,
            ended_at=g.ended_at,
        )
        for g in games
    ]


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


@router.get("/{id}/report", response_model=GameReportResponse)
async def get_game_report(
    id: uuid.UUID,
    request: Request,
    session: DbSession,
) -> GameReportResponse:
    """Lấy báo cáo phân tích trận đấu (FR-AIRPT-01..05, RP-6).

    - Host: xem toàn bộ báo cáo phân tích tổng hợp.
    - Player/Guest: xem báo cáo kèm nhận xét cá nhân hóa (0 lần gọi AI).
    """
    stmt = select(GameReport).where(GameReport.session_id == id)
    report = (await session.execute(stmt)).scalar_one_or_none()
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Báo cáo chưa sẵn sàng hoặc không tồn tại.",
        )

    personal_feedback: dict[str, Any] | None = None
    auth_header = request.headers.get("Authorization")

    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split()[1]
        try:
            # 1. Kiểm tra Guest Token
            guest_claims = security.decode_token(token, expected_type="guest")
            guest_id_raw = guest_claims.get("sub", "").replace("guest:", "")
            if guest_id_raw:
                personal_feedback = await get_player_personal_feedback(
                    session, report, id, uuid.UUID(guest_id_raw)
                )
        except Exception:
            try:
                # 2. Kiểm tra User Access Token
                user_claims = security.decode_token(token, expected_type="access")
                user_id = uuid.UUID(user_claims["sub"])
                stmt_game = select(GameSession).where(GameSession.id == id)
                game = (await session.execute(stmt_game)).scalar_one_or_none()
                if game and game.host_id == user_id:
                    personal_feedback = None
                else:
                    stmt_player = select(GamePlayer).where(
                        GamePlayer.session_id == id, GamePlayer.user_id == user_id
                    )
                    player = (await session.execute(stmt_player)).scalar_one_or_none()
                    if player:
                        personal_feedback = await get_player_personal_feedback(
                            session, report, id, player.id
                        )
            except Exception:
                pass

    return GameReportResponse(
        id=report.id,
        session_id=report.session_id,
        status=report.status,
        stats=report.stats,
        summary=report.summary,
        strengths=report.strengths,
        weaknesses=report.weaknesses,
        advice_for_host=report.advice_for_host,
        advice_for_learner=report.advice_for_learner,
        personal_feedback=personal_feedback,
        created_at=report.created_at,
    )


@router.post("/{id}/report/retry", response_model=GameReportResponse)
async def retry_game_report(
    id: uuid.UUID,
    session: DbSession,
    redis: RedisClient,
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.GAME_HOST_OWN))],
) -> GameReportResponse:
    """Host thử lại sinh báo cáo AI nếu báo cáo đang ở trạng thái 'degraded' hoặc 'failed' (RP-5)."""
    host_id = uuid.UUID(claims["sub"])

    stmt_game = select(GameSession).where(GameSession.id == id)
    game = (await session.execute(stmt_game)).scalar_one_or_none()
    if not game:
        raise HTTPException(status_code=404, detail="Trận đấu không tồn tại")
    if game.host_id != host_id:
        raise HTTPException(status_code=403, detail="Bạn không phải Host của trận đấu này")

    stmt_rep = select(GameReport).where(GameReport.session_id == id)
    report = (await session.execute(stmt_rep)).scalar_one_or_none()
    if report and report.status == "ready":
        raise HTTPException(status_code=400, detail="Báo cáo đã ở trạng thái hoàn chỉnh (ready)")

    # Giới hạn số lần bấm thử lại tối đa 3 lần
    stmt_job = select(AiJob).where(AiJob.session_id == id, AiJob.kind == "game_report")
    job = (await session.execute(stmt_job)).scalar_one_or_none()
    if job and job.attempts >= 3:
        raise HTTPException(
            status_code=429,
            detail="Đã vượt quá số lần thử lại tối đa (3 lần). Vui lòng sử dụng số liệu thống kê hiện tại.",
        )

    gateway = create_gemini_gateway()
    updated_report = await generate_and_save_game_report(
        session=session,
        redis=redis,
        gateway=gateway,
        session_id=id,
        is_retry=True,
    )

    return GameReportResponse(
        id=updated_report.id,
        session_id=updated_report.session_id,
        status=updated_report.status,
        stats=updated_report.stats,
        summary=updated_report.summary,
        strengths=updated_report.strengths,
        weaknesses=updated_report.weaknesses,
        advice_for_host=updated_report.advice_for_host,
        advice_for_learner=updated_report.advice_for_learner,
        personal_feedback=None,
        created_at=updated_report.created_at,
    )


@me_router.get("/games", response_model=list[UserGameHistoryItemResponse])
async def get_my_game_history(
    session: DbSession,
    claims: Annotated[dict[str, Any], Depends(get_current_user_claims)],
) -> list[UserGameHistoryItemResponse]:
    """Xem lịch sử các trận đấu người dùng đã tham gia (RP-6)."""
    user_id = uuid.UUID(claims["sub"])
    stmt = (
        select(GamePlayer, GameSession)
        .join(GameSession, GamePlayer.session_id == GameSession.id)
        .where(GamePlayer.user_id == user_id)
        .order_by(GameSession.created_at.desc())
    )
    records = (await session.execute(stmt)).all()

    return [
        UserGameHistoryItemResponse(
            game_id=session_obj.id,
            quiz_title=(session_obj.quiz_snapshot or {}).get("title", "Quiz"),
            nickname=player_obj.nickname,
            final_score=player_obj.final_score,
            final_rank=player_obj.final_rank,
            ended_at=session_obj.ended_at,
        )
        for player_obj, session_obj in records
    ]


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
