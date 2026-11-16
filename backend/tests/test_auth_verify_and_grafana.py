"""Unit và integration tests cho Nginx auth_request và bảo vệ Grafana (Phase 5).

Kiểm thử:
1. /api/v1/auth/verify-admin và /internal/auth/verify:
   - Thiếu token/cookie -> 401 Unauthorized
   - Token không hợp lệ / hết hạn -> 401 Unauthorized
   - Người dùng có vai trò PLAYER / HOST -> 403 Forbidden
   - Người dùng có vai trò ADMIN qua header Authorization: Bearer -> 200 OK + headers
   - Người dùng có vai trò ADMIN qua cookie admin_session -> 200 OK + headers
2. Đăng nhập:
   - ADMIN đăng nhập -> được set cookie admin_session
   - PLAYER đăng nhập -> không có cookie admin_session
   - Đăng xuất -> xóa cookie admin_session
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.db import create_session_factory
from app.core.rbac import RoleName
from app.core.security import create_access_token, hash_password
from app.main_api import app
from app.models.rbac import Role, user_roles
from app.models.user import User
from app.services.seed import seed_rbac

VERIFY_URL = "/api/v1/auth/verify-admin"
INTERNAL_VERIFY_URL = "/internal/auth/verify"
LOGIN_URL = "/api/v1/auth/login"
LOGOUT_URL = "/api/v1/auth/logout"
PASSWORD = "Password123!"


@pytest_asyncio.fixture
async def client(db_engine):
    async with create_session_factory(db_engine)() as session, session.begin():
        await seed_rbac(session)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM users WHERE email LIKE 'test_verify_%@quizarena.com'"))


async def create_user_with_role(db_engine, role_name: RoleName) -> tuple[User, str]:
    session_factory = create_session_factory(db_engine)
    unique_email = f"test_verify_{uuid.uuid4().hex[:8]}@quizarena.com"
    async with session_factory() as session, session.begin():
        user = User(
            id=uuid.uuid4(),
            email=unique_email,
            password_hash=hash_password(PASSWORD),
            display_name=f"User {role_name.value}",
            is_active=True,
            token_version=0,
        )
        session.add(user)
        await session.flush()

        role = (
            await session.execute(Role.__table__.select().where(Role.name == role_name.value))
        ).first()
        if role:
            await session.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))

    token = create_access_token(
        user_id=user.id,
        roles=[role_name.value],
        permissions=[],
        token_version=0,
    )
    return user, token


@pytest.mark.asyncio
async def test_verify_admin_missing_credentials(client):
    """Không có token hoặc cookie -> 401."""
    res = await client.get(VERIFY_URL)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"

    res_internal = await client.get(INTERNAL_VERIFY_URL)
    assert res_internal.status_code == 401


@pytest.mark.asyncio
async def test_verify_admin_invalid_token(client):
    """Token sai định dạng hoặc chữ ký giả mạo -> 401."""
    headers = {"Authorization": "Bearer invalid_garbage_token"}
    res = await client.get(VERIFY_URL, headers=headers)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "TOKEN_INVALID"


@pytest.mark.asyncio
async def test_verify_admin_forbidden_for_player(client, db_engine):
    """Người dùng có role PLAYER -> 403 Forbidden."""
    _, token = await create_user_with_role(db_engine, RoleName.PLAYER)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get(VERIFY_URL, headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_verify_admin_forbidden_for_host(client, db_engine):
    """Người dùng có role HOST -> 403 Forbidden."""
    _, token = await create_user_with_role(db_engine, RoleName.HOST)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get(VERIFY_URL, headers=headers)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_verify_admin_success_bearer(client, db_engine):
    """ADMIN xác thực qua Bearer token -> 200 OK và trả đúng thông tin headers."""
    user, token = await create_user_with_role(db_engine, RoleName.ADMIN)
    headers = {"Authorization": f"Bearer {token}"}
    res = await client.get(VERIFY_URL, headers=headers)
    assert res.status_code == 200
    assert res.headers["X-User-Id"] == str(user.id)
    assert res.headers["X-User-Email"] == user.email
    assert res.headers["X-User-Role"] == "ADMIN"


@pytest.mark.asyncio
async def test_verify_admin_success_cookie(client, db_engine):
    """ADMIN xác thực qua cookie admin_session (dùng cho trình duyệt vào Grafana) -> 200 OK."""
    user, token = await create_user_with_role(db_engine, RoleName.ADMIN)
    # Gửi cookie admin_session
    headers = {"Cookie": f"admin_session={token}"}
    res = await client.get(INTERNAL_VERIFY_URL, headers=headers)
    assert res.status_code == 200
    assert res.headers["X-User-Id"] == str(user.id)
    assert res.headers["X-User-Email"] == user.email
    assert res.headers["X-User-Role"] == "ADMIN"


@pytest.mark.asyncio
async def test_admin_login_sets_cookie_and_logout_clears(client, db_engine):
    """Đăng nhập bằng tài khoản ADMIN tự động cấp admin_session cookie; logout xóa cookie."""
    admin_user, _ = await create_user_with_role(db_engine, RoleName.ADMIN)

    # 1. Login ADMIN
    login_res = await client.post(
        LOGIN_URL,
        json={"email": admin_user.email, "password": PASSWORD},
    )
    assert login_res.status_code == 200
    cookies = login_res.headers.get_list("set-cookie")
    cookie_str = "; ".join(cookies)
    assert "admin_session=" in cookie_str
    assert "HttpOnly" in cookie_str

    access_token = login_res.json()["access_token"]

    # 2. Logout ADMIN
    logout_res = await client.post(
        LOGOUT_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_res.status_code == 200
    logout_cookies = logout_res.headers.get_list("set-cookie")
    logout_cookie_str = "; ".join(logout_cookies)
    assert "admin_session=" in logout_cookie_str
    assert "max-age=0" in logout_cookie_str.lower() or "expires=" in logout_cookie_str.lower()


@pytest.mark.asyncio
async def test_player_login_does_not_set_admin_cookie(client, db_engine):
    """Đăng nhập bằng tài khoản PLAYER thông thường không cấp admin_session cookie."""
    player_user, _ = await create_user_with_role(db_engine, RoleName.PLAYER)

    login_res = await client.post(
        LOGIN_URL,
        json={"email": player_user.email, "password": PASSWORD},
    )
    assert login_res.status_code == 200
    cookies = login_res.headers.get_list("set-cookie")
    cookie_str = "; ".join(cookies)
    # Chỉ có refresh_token, không có giá trị admin_session
    assert "refresh_token=" in cookie_str
    assert "admin_session=" not in cookie_str or "max-age=0" in cookie_str.lower()
