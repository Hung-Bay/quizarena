"""Unit và integration tests cho module Admin (FR-ADM-01..05)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import create_session_factory
from app.core.rbac import PermissionCode, RoleName
from app.core.security import create_access_token, hash_password
from app.main_api import app
from app.models.quiz import Quiz
from app.models.rbac import Role, user_roles
from app.models.user import User
from app.services.seed import seed_rbac


@pytest_asyncio.fixture(autouse=True)
async def ensure_rbac_seeded(db_engine: AsyncEngine) -> None:
    """Đảm bảo bảng roles và permissions luôn được seed trước khi chạy các test của admin."""
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session, session.begin():
        await seed_rbac(session)


@pytest_asyncio.fixture
async def admin_user(db_engine: AsyncEngine) -> AsyncGenerator[User, None]:
    """Tạo người dùng với vai trò ADMIN và commit vào DB."""
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session, session.begin():
        user = User(
            id=uuid.uuid4(),
            email=f"admin_{uuid.uuid4().hex}@quizarena.com",
            password_hash=hash_password("admin_pass_123"),
            display_name="Admin Test",
            is_active=True,
        )
        session.add(user)
        await session.flush()

        admin_role = (
            await session.execute(Role.__table__.select().where(Role.name == RoleName.ADMIN.value))
        ).first()
        if admin_role:
            await session.execute(
                user_roles.insert().values(user_id=user.id, role_id=admin_role.id)
            )
        admin_id = user.id

    yield user

    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM audit_logs WHERE actor_id = :uid"), {"uid": admin_id})
        await conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": admin_id})


@pytest.fixture
def admin_token(admin_user: User) -> str:
    """Access token cho ADMIN mang đầy đủ quyền."""
    return create_access_token(
        user_id=admin_user.id,
        permissions=[p.value for p in PermissionCode],
        roles=[RoleName.ADMIN.value],
        token_version=admin_user.token_version,
    )


@pytest.fixture
def player_token() -> str:
    """Access token cho PLAYER thông thường."""
    return create_access_token(
        user_id=uuid.uuid4(),
        permissions=[PermissionCode.GAME_PLAY.value, PermissionCode.QUIZ_READ_PUBLIC.value],
        roles=[RoleName.PLAYER.value],
        token_version=0,
    )


@pytest_asyncio.fixture
async def client() -> Any:
    """AsyncClient với đầy đủ lifespan context của FastAPI (app.state.redis, db...)."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_admin_list_users_and_rbac(
    client: AsyncClient,
    admin_token: str,
    player_token: str,
) -> None:
    """Kiểm tra quyền truy cập danh sách người dùng (Player bị 403, Admin thành công)."""
    # Player gọi -> 403 Forbidden
    res_player = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {player_token}"},
    )
    assert res_player.status_code == 403

    # Admin gọi -> 200 OK
    res_admin = await client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_admin.status_code == 200
    body = res_admin.json()
    assert "items" in body
    assert "total" in body
    assert body["total"] >= 1


@pytest.mark.asyncio
async def test_admin_ban_and_unban_user(
    client: AsyncClient,
    admin_token: str,
    db_engine: AsyncEngine,
) -> None:
    """Kiểm tra khóa và mở khóa tài khoản người dùng kèm ghi nhật ký (FR-ADM-01)."""
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session, session.begin():
        target = User(
            email=f"target_{uuid.uuid4().hex}@test.com",
            password_hash=hash_password("target_123"),
            display_name="Target User",
            is_active=True,
            token_version=0,
        )
        session.add(target)
        await session.flush()
        target_id = target.id

    try:
        # 1. Khóa tài khoản
        res_ban = await client.patch(
            f"/api/v1/admin/users/{target_id}/status",
            json={"is_active": False, "reason": "Vi phạm điều khoản cộng đồng"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_ban.status_code == 200
        assert res_ban.json()["is_active"] is False

        async with session_factory() as session:
            t_user = await session.get(User, target_id)
            assert t_user is not None
            assert t_user.is_active is False
            assert t_user.token_version == 1

        # 2. Mở khóa tài khoản
        res_unban = await client.patch(
            f"/api/v1/admin/users/{target_id}/status",
            json={"is_active": True},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_unban.status_code == 200
        assert res_unban.json()["is_active"] is True

        async with session_factory() as session:
            t_user = await session.get(User, target_id)
            assert t_user is not None
            assert t_user.is_active is True
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM audit_logs WHERE resource_id = :uid"), {"uid": str(target_id)}
            )
            await conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": target_id})


@pytest.mark.asyncio
async def test_admin_manage_user_role(
    client: AsyncClient,
    admin_token: str,
    db_engine: AsyncEngine,
) -> None:
    """Kiểm tra cấp và thu hồi quyền HOST (FR-ADM-02)."""
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session, session.begin():
        target = User(
            email=f"host_candidate_{uuid.uuid4().hex}@test.com",
            password_hash=hash_password("candidate_123"),
            display_name="Candidate",
            is_active=True,
        )
        session.add(target)
        await session.flush()
        target_id = target.id

    try:
        # 1. Cấp HOST
        res_grant = await client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "HOST", "action": "grant"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_grant.status_code == 200, (
            f"Cấp vai trò thất bại: {res_grant.status_code} - {res_grant.text}"
        )
        assert "HOST" in res_grant.json()["roles"]

        # 2. Thu hồi HOST
        res_revoke = await client.patch(
            f"/api/v1/admin/users/{target_id}/role",
            json={"role": "HOST", "action": "revoke"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_revoke.status_code == 200, (
            f"Thu hồi vai trò thất bại: {res_revoke.status_code} - {res_revoke.text}"
        )
        assert "HOST" not in res_revoke.json()["roles"]
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM audit_logs WHERE resource_id = :uid"), {"uid": str(target_id)}
            )
            await conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": target_id})


@pytest.mark.asyncio
async def test_admin_hide_and_unhide_quiz(
    client: AsyncClient,
    admin_token: str,
    admin_user: User,
    db_engine: AsyncEngine,
) -> None:
    """Kiểm tra ẩn quiz vi phạm kèm lý do bắt buộc và lọc khỏi public (FR-ADM-03)."""
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session, session.begin():
        quiz = Quiz(
            owner_id=admin_user.id,
            title=f"Vi phạm bản quyền {uuid.uuid4().hex}",
            visibility="public",
            status="published",
            is_hidden=False,
        )
        session.add(quiz)
        await session.flush()
        quiz_id = quiz.id

    try:
        # 1. Ẩn thiếu lý do -> 422
        res_fail = await client.post(
            f"/api/v1/admin/quizzes/{quiz_id}/hide",
            json={"reason": "a"},  # < 3 ký tự
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_fail.status_code == 422

        # 2. Ẩn hợp lệ
        res_hide = await client.post(
            f"/api/v1/admin/quizzes/{quiz_id}/hide",
            json={"reason": "Vi phạm bản quyền bài thi Olympic"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_hide.status_code == 200
        assert res_hide.json()["is_hidden"] is True
        assert res_hide.json()["hidden_reason"] == "Vi phạm bản quyền bài thi Olympic"

        # 3. Kiểm tra quiz không còn trong danh sách public
        res_public = await client.get("/api/v1/quizzes/public")
        assert res_public.status_code == 200
        public_list = res_public.json()
        public_ids = [q["id"] for q in public_list]
        assert str(quiz_id) not in public_ids

        # 4. Bỏ ẩn
        res_unhide = await client.post(
            f"/api/v1/admin/quizzes/{quiz_id}/unhide",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_unhide.status_code == 200
        assert res_unhide.json()["is_hidden"] is False
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM audit_logs WHERE resource_id = :qid"), {"qid": str(quiz_id)}
            )
            await conn.execute(text("DELETE FROM quizzes WHERE id = :qid"), {"qid": quiz_id})


@pytest.mark.asyncio
async def test_admin_dashboard_and_audit_logs(
    client: AsyncClient,
    admin_token: str,
) -> None:
    """Kiểm tra dashboard metrics (FR-ADM-04) và nhật ký kiểm toán (FR-ADM-05)."""
    # Dashboard
    res_db = await client.get(
        "/api/v1/admin/dashboard",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_db.status_code == 200
    db_body = res_db.json()
    assert "total_users" in db_body
    assert "total_quizzes" in db_body
    assert "active_games" in db_body
    assert "active_ws_connections" in db_body
    assert "ai_quota_used_today" in db_body
    assert "ai_quota_remaining_today" in db_body

    # Audit logs
    res_logs = await client.get(
        "/api/v1/admin/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_logs.status_code == 200
    logs_body = res_logs.json()
    assert "items" in logs_body
    assert "total" in logs_body
