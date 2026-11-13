"""Pydantic schemas cho module Admin (FR-ADM-01..05)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    roles: list[str] = Field(default_factory=list)
    created_at: datetime


class UserStatusUpdateRequest(BaseModel):
    is_active: bool
    reason: str | None = Field(default=None, max_length=500)


class UserRoleUpdateRequest(BaseModel):
    role: str = Field(description="Tên vai trò (ví dụ: HOST)")
    action: str = Field(pattern="^(grant|revoke)$", description="grant hoặc revoke")


class QuizHideRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500, description="Lý do bắt buộc khi ẩn quiz")


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: uuid.UUID | None
    action: str
    resource_type: str
    resource_id: str
    metadata: dict[str, Any] | None = None
    created_at: datetime


class AdminDashboardOut(BaseModel):
    total_users: int
    total_quizzes: int
    total_games: int
    active_games: int
    active_ws_connections: int
    ai_quota_used_today: int
    ai_quota_remaining_today: int
    ai_quota_limit: int
    cached_at: datetime
