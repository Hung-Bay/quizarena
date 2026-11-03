"""Truy vấn bảng users và quan hệ vai trò/quyền."""

import uuid

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Permission, Role, User, role_permissions, user_roles


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    # Cột email kiểu citext nên so sánh không phân biệt hoa thường.
    return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def create(
    session: AsyncSession, *, email: str, password_hash: str, display_name: str
) -> User:
    user = User(email=email, password_hash=password_hash, display_name=display_name)
    session.add(user)
    await session.flush()  # chạy INSERT ngay để lấy id và bắt lỗi trùng email sớm
    return user


async def role_id_by_name(session: AsyncSession, name: str) -> uuid.UUID | None:
    return (await session.execute(select(Role.id).where(Role.name == name))).scalar_one_or_none()


async def add_role(session: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
    await session.execute(insert(user_roles).values(user_id=user_id, role_id=role_id))


async def get_roles(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(Role.name)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user_id)
        .order_by(Role.name)
    )
    return list(rows.scalars())


async def get_permissions(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(Permission.code)
        .join(role_permissions, role_permissions.c.permission_id == Permission.id)
        .join(user_roles, user_roles.c.role_id == role_permissions.c.role_id)
        .where(user_roles.c.user_id == user_id)
        .distinct()
        .order_by(Permission.code)
    )
    return list(rows.scalars())


async def increment_token_version(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Tăng token_version lên 1 để vô hiệu hóa mọi access token cũ (AU-7)."""
    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(token_version=User.token_version + 1)
        .returning(User.token_version)
    )
    return result.scalar_one()


async def update_password(session: AsyncSession, user_id: uuid.UUID, password_hash: str) -> None:
    """Cập nhật mật khẩu và tăng token_version (AU-7)."""
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(password_hash=password_hash, token_version=User.token_version + 1)
    )
