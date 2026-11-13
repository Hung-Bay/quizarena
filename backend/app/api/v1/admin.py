"""API router cho module Admin (FR-ADM-01..05)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis, get_session, require_permissions
from app.core.rbac import PermissionCode
from app.schemas.admin import (
    AdminDashboardOut,
    AuditLogOut,
    QuizHideRequest,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
)
from app.services import admin_service

router = APIRouter(prefix="/admin", tags=["admin"])

DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]


@router.get(
    "/users",
    response_model=dict[str, Any],
    dependencies=[Depends(require_permissions(PermissionCode.USERS_MANAGE))],
)
async def list_users(
    session: DatabaseSession,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None),
) -> dict[str, Any]:
    """Danh sách người dùng hệ thống có phân trang (FR-ADM-01)."""
    users, total = await admin_service.list_users(session, skip=skip, limit=limit, search=search)
    return {
        "items": [u.model_dump(mode="json") for u in users],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.patch(
    "/users/{user_id}/status",
    response_model=dict[str, Any],
)
async def update_user_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdateRequest,
    session: DatabaseSession,
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.USERS_MANAGE))],
) -> dict[str, Any]:
    """Khóa hoặc mở khóa tài khoản người dùng (FR-ADM-01)."""
    actor_id = uuid.UUID(claims["sub"])
    user = await admin_service.set_user_status(
        session,
        user_id=user_id,
        is_active=payload.is_active,
        actor_id=actor_id,
        reason=payload.reason,
    )
    return {
        "id": str(user.id),
        "is_active": user.is_active,
        "message": "Đã mở khóa tài khoản" if user.is_active else "Đã khóa tài khoản thành công",
    }


@router.patch(
    "/users/{user_id}/role",
    response_model=dict[str, Any],
)
async def update_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdateRequest,
    session: DatabaseSession,
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.USERS_MANAGE))],
) -> dict[str, Any]:
    """Cấp hoặc thu hồi vai trò (HOST) cho người dùng (FR-ADM-02)."""
    actor_id = uuid.UUID(claims["sub"])
    roles = await admin_service.manage_user_role(
        session,
        user_id=user_id,
        role_name=payload.role,
        action=payload.action,
        actor_id=actor_id,
    )
    return {
        "user_id": str(user_id),
        "roles": roles,
        "message": f"Đã {payload.action} vai trò {payload.role} thành công",
    }


@router.post(
    "/quizzes/{quiz_id}/hide",
    response_model=dict[str, Any],
)
async def hide_quiz(
    quiz_id: uuid.UUID,
    payload: QuizHideRequest,
    session: DatabaseSession,
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.QUIZ_MODERATE))],
) -> dict[str, Any]:
    """Ẩn quiz công khai vi phạm kèm lý do bắt buộc (FR-ADM-03)."""
    actor_id = uuid.UUID(claims["sub"])
    quiz = await admin_service.hide_quiz(
        session,
        quiz_id=quiz_id,
        reason=payload.reason,
        actor_id=actor_id,
    )
    return {
        "id": str(quiz.id),
        "is_hidden": quiz.is_hidden,
        "hidden_reason": quiz.hidden_reason,
        "message": "Đã ẩn quiz vi phạm thành công",
    }


@router.post(
    "/quizzes/{quiz_id}/unhide",
    response_model=dict[str, Any],
)
async def unhide_quiz(
    quiz_id: uuid.UUID,
    session: DatabaseSession,
    claims: Annotated[dict[str, Any], Depends(require_permissions(PermissionCode.QUIZ_MODERATE))],
) -> dict[str, Any]:
    """Bỏ ẩn quiz (FR-ADM-03)."""
    actor_id = uuid.UUID(claims["sub"])
    quiz = await admin_service.unhide_quiz(
        session,
        quiz_id=quiz_id,
        actor_id=actor_id,
    )
    return {
        "id": str(quiz.id),
        "is_hidden": quiz.is_hidden,
        "message": "Đã bỏ ẩn quiz thành công",
    }


@router.get(
    "/dashboard",
    response_model=AdminDashboardOut,
    dependencies=[Depends(require_permissions(PermissionCode.SYSTEM_READ))],
)
async def get_dashboard(
    session: DatabaseSession,
    redis: RedisClient,
) -> AdminDashboardOut:
    """Bảng theo dõi hệ thống có cache Redis 30-60 giây (FR-ADM-04)."""
    return await admin_service.get_dashboard_metrics(session, redis)


@router.get(
    "/audit-logs",
    response_model=dict[str, Any],
    dependencies=[Depends(require_permissions(PermissionCode.SYSTEM_READ))],
)
async def list_audit_logs(
    session: DatabaseSession,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Nhật ký kiểm toán hệ thống (FR-ADM-05)."""
    logs, total = await admin_service.list_audit_logs(session, skip=skip, limit=limit)
    return {
        "items": [
            AuditLogOut(
                id=log.id,
                actor_id=log.actor_id,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                metadata=log.metadata_,
                created_at=log.created_at,
            ).model_dump(mode="json")
            for log in logs
        ],
        "total": total,
        "skip": skip,
        "limit": limit,
    }
