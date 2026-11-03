"""Test RBAC bằng ma trận parametrize 4 vai trò × 10 quyền (§3.2, AU-9)."""

import uuid
from collections.abc import Callable
from typing import Annotated, Any
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_redis, require_permissions
from app.core import security
from app.core.errors import register_error_handlers
from app.core.rbac import ROLE_PERMISSIONS, PermissionCode, RoleName

# Tạo app kiểm thử với endpoint cho từng quyền trong 10 quyền (đặt tên rbac_app để pytest không nhầm là test)
rbac_app = FastAPI()
register_error_handlers(rbac_app)

fake_redis = AsyncMock()
fake_redis.exists.return_value = False
rbac_app.dependency_overrides[get_redis] = lambda: fake_redis


def _create_endpoint(code: PermissionCode) -> Callable[..., Any]:
    async def _test_endpoint(
        claims: Annotated[dict[str, Any], Depends(require_permissions(code))],
    ) -> dict[str, Any]:
        return {"status": "ok", "permissions": claims.get("permissions")}

    return _test_endpoint


for code in PermissionCode:
    rbac_app.add_api_route(f"/test/{code.value}", _create_endpoint(code), methods=["GET"])

client = TestClient(rbac_app)

ROLES = list(RoleName)
PERMISSIONS = list(PermissionCode)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("permission", PERMISSIONS)
def test_rbac_matrix_du_4_vai_tro_x_10_quyen(role: RoleName, permission: PermissionCode) -> None:
    """Kiểm tra ma trận phân quyền 4 vai trò × 10 quyền: vai trò có quyền thì 200, không có thì 403."""
    perms = [p.value for p in ROLE_PERMISSIONS[role]]
    if role == RoleName.GUEST:
        token = security.create_guest_token(game_id=uuid.uuid4())
    else:
        token = security.create_access_token(
            user_id=uuid.uuid4(),
            roles=[role.value],
            permissions=perms,
            token_version=0,
        )

    response = client.get(
        f"/test/{permission.value}",
        headers={"Authorization": f"Bearer {token}"},
    )

    should_allow = permission in ROLE_PERMISSIONS[role]
    if should_allow:
        assert response.status_code == 200, (
            f"Vai trò {role} phải có quyền {permission.value}, nhưng nhận {response.status_code}"
        )
    else:
        assert response.status_code == 403, (
            f"Vai trò {role} KHÔNG được có quyền {permission.value}, nhưng nhận {response.status_code}"
        )
        assert response.json()["error"]["code"] == "FORBIDDEN"
