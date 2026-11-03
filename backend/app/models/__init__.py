"""Import mọi model ở đây để Alembic (autogenerate) nhìn thấy chúng qua Base.metadata."""

from app.models.base import Base
from app.models.rbac import Permission, Role, role_permissions, user_roles
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "Base",
    "Permission",
    "RefreshToken",
    "Role",
    "User",
    "role_permissions",
    "user_roles",
]
