"""Repository tương tác với game_sessions, game_players và player_answers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.game.enums import GameStatus
from app.models.game import GamePlayer, GameSession


async def create_session(session: AsyncSession, game: GameSession) -> GameSession:
    """Tạo mới phiên trận đấu trong PostgreSQL."""
    session.add(game)
    await session.flush()
    await session.refresh(game)
    return game


async def get_session_by_id(session: AsyncSession, session_id: uuid.UUID) -> GameSession | None:
    """Tra cứu phiên trận đấu theo ID."""
    stmt = select(GameSession).where(GameSession.id == session_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_session_by_pin(session: AsyncSession, pin: str) -> GameSession | None:
    """Tra cứu phiên trận đấu theo PIN."""
    stmt = select(GameSession).where(GameSession.pin == pin)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def count_active_sessions_by_host(session: AsyncSession, host_id: uuid.UUID) -> int:
    """Đếm số trận đang mở (chưa kết thúc/hủy) của một host."""
    active_statuses = (
        GameStatus.LOBBY.value,
        GameStatus.QUESTION_ACTIVE.value,
        GameStatus.QUESTION_RESULT.value,
    )
    stmt = (
        select(func.count())
        .select_from(GameSession)
        .where(GameSession.host_id == host_id, GameSession.status.in_(active_statuses))
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def create_player(session: AsyncSession, player: GamePlayer) -> GamePlayer:
    """Tạo mới người chơi trong phiên."""
    session.add(player)
    await session.flush()
    await session.refresh(player)
    return player


async def get_player_by_nickname(
    session: AsyncSession, session_id: uuid.UUID, nickname: str
) -> GamePlayer | None:
    """Tra cứu người chơi trong trận theo nickname."""
    stmt = select(GamePlayer).where(
        GamePlayer.session_id == session_id,
        func.lower(GamePlayer.nickname) == nickname.lower().strip(),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_player_by_id(session: AsyncSession, player_id: uuid.UUID) -> GamePlayer | None:
    """Tra cứu người chơi theo ID."""
    stmt = select(GamePlayer).where(GamePlayer.id == player_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def count_players_in_session(session: AsyncSession, session_id: uuid.UUID) -> int:
    """Đếm tổng số người chơi trong trận."""
    stmt = select(func.count()).select_from(GamePlayer).where(GamePlayer.session_id == session_id)
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def update_session_status(
    session: AsyncSession,
    session_id: uuid.UUID,
    status: str,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> None:
    """Cập nhật trạng thái trận đấu."""
    game = await get_session_by_id(session, session_id)
    if not game:
        return
    game.status = status
    if started_at is not None:
        game.started_at = started_at
    if ended_at is not None:
        game.ended_at = ended_at
    game.updated_at = datetime.now(UTC)
    await session.flush()


async def count_sessions_by_quiz_id(session: AsyncSession, quiz_id: uuid.UUID) -> int:
    """Đếm tổng số trận đấu đã từng được tạo cho bộ Quiz này (QZ-4, FR-QUIZ-06)."""
    if not hasattr(session, "execute"):
        return 0
    stmt = select(func.count()).select_from(GameSession).where(GameSession.quiz_id == quiz_id)
    result = await session.execute(stmt)
    if result is None:
        return 0
    return int(result.scalar_one() or 0)
