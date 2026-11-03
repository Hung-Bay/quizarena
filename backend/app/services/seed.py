"""Nạp dữ liệu nền: vai trò, quyền và việc gán quyền cho vai trò (ma trận §3.2)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import ROLE_PERMISSIONS, PermissionCode, RoleName
from app.models import Permission, Role, role_permissions


@dataclass(frozen=True)
class SeedResult:
    """Số dòng MỚI được tạo. Chạy lần hai thì mọi số đều bằng 0 (idempotent)."""

    roles: int
    permissions: int
    role_permissions: int


async def seed_rbac(session: AsyncSession) -> SeedResult:
    # ON CONFLICT DO NOTHING + RETURNING: chỉ trả về những dòng thật sự được chèn mới.
    new_roles = (
        await session.execute(
            insert(Role)
            .values([{"name": r.value} for r in RoleName])
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(Role.id)
        )
    ).all()
    new_permissions = (
        await session.execute(
            insert(Permission)
            .values([{"code": p.value} for p in PermissionCode])
            .on_conflict_do_nothing(index_elements=["code"])
            .returning(Permission.id)
        )
    ).all()

    role_ids = {
        name: id_ for id_, name in (await session.execute(select(Role.id, Role.name))).all()
    }
    permission_ids = {
        code: id_
        for id_, code in (await session.execute(select(Permission.id, Permission.code))).all()
    }

    links = [
        {"role_id": role_ids[role.value], "permission_id": permission_ids[perm.value]}
        for role, perms in ROLE_PERMISSIONS.items()
        for perm in perms
    ]
    new_links = (
        await session.execute(
            insert(role_permissions)
            .values(links)
            .on_conflict_do_nothing()
            .returning(role_permissions.c.role_id)
        )
    ).all()

    return SeedResult(len(new_roles), len(new_permissions), len(new_links))
