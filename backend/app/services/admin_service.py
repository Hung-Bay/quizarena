"""Nghiệp vụ quản trị hệ thống Admin (FR-ADM-01..05)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.audit import AuditLog
from app.models.game import GameSession
from app.models.quiz import Quiz
from app.models.rbac import Role, user_roles
from app.models.user import User
from app.schemas.admin import AdminDashboardOut, UserAdminOut
from app.services.gemini_rate_limiter import get_pacific_date_str

log = logging.getLogger("app.services.admin")


async def list_users(
    session: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> tuple[list[UserAdminOut], int]:
    """Lấy danh sách người dùng kèm vai trò và tổng số lượng."""
    base_query = select(User)
    if search:
        kw = f"%{search.strip()}%"
        base_query = base_query.where((User.email.ilike(kw)) | (User.display_name.ilike(kw)))

    # Đếm tổng
    count_stmt = select(func.count()).select_from(base_query.subquery())
    total_res = await session.execute(count_stmt)
    total = total_res.scalar_one()

    # Phân trang
    stmt = base_query.order_by(User.created_at.desc()).offset(skip).limit(limit)
    res = await session.execute(stmt)
    users = list(res.scalars().all())

    if not users:
        return [], total

    # Lấy vai trò cho từng user
    user_ids = [u.id for u in users]
    roles_stmt = (
        select(user_roles.c.user_id, Role.name)
        .select_from(user_roles.join(Role, user_roles.c.role_id == Role.id))
        .where(user_roles.c.user_id.in_(user_ids))
    )
    roles_res = await session.execute(roles_stmt)
    roles_map: dict[uuid.UUID, list[str]] = {uid: [] for uid in user_ids}
    for uid, rname in roles_res.all():
        roles_map[uid].append(rname)

    items = [
        UserAdminOut(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            is_active=u.is_active,
            roles=roles_map.get(u.id, []),
            created_at=u.created_at,
        )
        for u in users
    ]
    return items, total


async def set_user_status(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    is_active: bool,
    actor_id: uuid.UUID,
    reason: str | None = None,
) -> User:
    """Khóa hoặc mở khóa người dùng (FR-ADM-01).

    Nếu khóa: tăng token_version để vô hiệu hóa tức thì toàn bộ refresh token.
    Ghi nhật ký vào audit_logs.
    """
    user = await session.get(User, user_id)
    if not user:
        raise AppError("Không tìm thấy người dùng", code="USER_NOT_FOUND", status_code=404)

    user.is_active = is_active
    if not is_active:
        user.token_version += 1

    action = "user.unban" if is_active else "user.ban"
    audit = AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type="user",
        resource_id=str(user_id),
        metadata_={"reason": reason} if reason else None,
    )
    session.add(audit)
    await session.commit()
    await session.refresh(user)
    return user


async def manage_user_role(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    role_name: str,
    action: str,
    actor_id: uuid.UUID,
) -> list[str]:
    """Cấp hoặc thu hồi vai trò cho người dùng (FR-ADM-02)."""
    user = await session.get(User, user_id)
    if not user:
        raise AppError("Không tìm thấy người dùng", code="USER_NOT_FOUND", status_code=404)

    role_stmt = select(Role).where(Role.name == role_name.upper())
    role_res = await session.execute(role_stmt)
    target_role = role_res.scalar_one_or_none()
    if not target_role:
        raise AppError(
            f"Vai trò {role_name} không tồn tại trong hệ thống",
            code="ROLE_NOT_FOUND",
            status_code=400,
        )

    # Kiểm tra xem user đã có role này chưa
    check_stmt = select(user_roles).where(
        user_roles.c.user_id == user_id, user_roles.c.role_id == target_role.id
    )
    existing = (await session.execute(check_stmt)).first()

    if action == "grant":
        if not existing:
            await session.execute(
                insert(user_roles).values(user_id=user_id, role_id=target_role.id)
            )
    elif action == "revoke":
        if existing:
            await session.execute(
                delete(user_roles).where(
                    user_roles.c.user_id == user_id, user_roles.c.role_id == target_role.id
                )
            )

    audit = AuditLog(
        actor_id=actor_id,
        action=f"user.role.{action}",
        resource_type="user",
        resource_id=str(user_id),
        metadata_={"role": target_role.name},
    )
    session.add(audit)
    await session.commit()

    # Lấy danh sách role mới nhất
    roles_stmt = (
        select(Role.name)
        .select_from(user_roles.join(Role, user_roles.c.role_id == Role.id))
        .where(user_roles.c.user_id == user_id)
    )
    new_roles = list((await session.execute(roles_stmt)).scalars().all())
    return new_roles


async def hide_quiz(
    session: AsyncSession,
    *,
    quiz_id: uuid.UUID,
    reason: str,
    actor_id: uuid.UUID,
) -> Quiz:
    """Ẩn quiz công khai vi phạm kèm lý do bắt buộc (FR-ADM-03)."""
    quiz = await session.get(Quiz, quiz_id)
    if not quiz:
        raise AppError("Không tìm thấy quiz", code="QUIZ_NOT_FOUND", status_code=404)

    quiz.is_hidden = True
    quiz.hidden_reason = reason.strip()

    audit = AuditLog(
        actor_id=actor_id,
        action="quiz.hide",
        resource_type="quiz",
        resource_id=str(quiz_id),
        metadata_={"reason": quiz.hidden_reason},
    )
    session.add(audit)
    await session.commit()
    await session.refresh(quiz)
    return quiz


async def unhide_quiz(
    session: AsyncSession,
    *,
    quiz_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> Quiz:
    """Bỏ ẩn quiz (FR-ADM-03)."""
    quiz = await session.get(Quiz, quiz_id)
    if not quiz:
        raise AppError("Không tìm thấy quiz", code="QUIZ_NOT_FOUND", status_code=404)

    quiz.is_hidden = False
    quiz.hidden_reason = None

    audit = AuditLog(
        actor_id=actor_id,
        action="quiz.unhide",
        resource_type="quiz",
        resource_id=str(quiz_id),
        metadata_=None,
    )
    session.add(audit)
    await session.commit()
    await session.refresh(quiz)
    return quiz


async def get_dashboard_metrics(
    session: AsyncSession,
    redis: Redis,
) -> AdminDashboardOut:
    """Lấy số liệu bảng theo dõi hệ thống có cache Redis 30-60 giây (FR-ADM-04)."""
    cache_key = "admin:dashboard:metrics"
    cached = await redis.get(cache_key)
    if cached:
        try:
            data = json.loads(cached)
            return AdminDashboardOut(**data)
        except Exception:
            pass

    # 1. Đếm users
    total_users_res = await session.execute(select(func.count(User.id)))
    total_users = total_users_res.scalar_one()

    # 2. Đếm quizzes
    total_quizzes_res = await session.execute(select(func.count(Quiz.id)))
    total_quizzes = total_quizzes_res.scalar_one()

    # 3. Đếm games
    total_games_res = await session.execute(select(func.count(GameSession.id)))
    total_games = total_games_res.scalar_one()

    # 4. Trận đang chạy (active: lobby, question_active, question_result)
    active_games_stmt = select(func.count(GameSession.id)).where(
        GameSession.status.in_(["lobby", "question_active", "question_result"])
    )
    active_games_res = await session.execute(active_games_stmt)
    active_games = active_games_res.scalar_one()

    # 5. Active WS connections từ Redis
    ws_conns_str = await redis.get("realtime:active_connections")
    active_ws = int(ws_conns_str) if ws_conns_str else 0

    # 6. AI Quota hôm nay
    settings = get_settings()
    ai_limit = settings.gemini_rpd or 1500
    pacific_date = get_pacific_date_str()
    rpd_key = f"gemini:rpd:{pacific_date}"
    used_str = await redis.get(rpd_key)
    ai_used = int(used_str) if used_str else 0
    ai_remaining = max(0, ai_limit - ai_used)

    out = AdminDashboardOut(
        total_users=total_users,
        total_quizzes=total_quizzes,
        total_games=total_games,
        active_games=active_games,
        active_ws_connections=active_ws,
        ai_quota_used_today=ai_used,
        ai_quota_remaining_today=ai_remaining,
        ai_quota_limit=ai_limit,
        cached_at=datetime.now(UTC),
    )

    # Lưu cache 45 giây (nằm trong dải 30-60s của đề)
    try:
        await redis.set(cache_key, out.model_dump_json(), ex=45)
    except Exception as exc:
        log.warning("Không thể lưu cache dashboard metrics: %s", exc)

    return out


async def list_audit_logs(
    session: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[AuditLog], int]:
    """Lấy danh sách nhật ký kiểm toán phân trang (FR-ADM-05)."""
    count_res = await session.execute(select(func.count(AuditLog.id)))
    total = count_res.scalar_one()

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    res = await session.execute(stmt)
    logs = list(res.scalars().all())
    return logs, total
