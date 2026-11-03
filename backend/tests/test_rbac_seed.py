from collections import defaultdict

from sqlalchemy import select

from app.core.db import create_session_factory
from app.core.rbac import ROLE_PERMISSIONS, PermissionCode, RoleName
from app.models import Permission, Role, role_permissions
from app.services.seed import SeedResult, seed_rbac

# ---------------------------------------------------------------- ma trận trong code (không cần DB)


def test_so_quyen_moi_vai_tro_khop_bang_3_2():
    assert {r: len(p) for r, p in ROLE_PERMISSIONS.items()} == {
        RoleName.GUEST: 1,
        RoleName.PLAYER: 3,
        RoleName.HOST: 7,
        RoleName.ADMIN: 10,
    }


def test_guest_chi_co_game_play():
    assert ROLE_PERMISSIONS[RoleName.GUEST] == {PermissionCode.GAME_PLAY}


def test_vai_tro_sau_ke_thua_vai_tro_truoc():
    assert ROLE_PERMISSIONS[RoleName.GUEST] < ROLE_PERMISSIONS[RoleName.PLAYER]
    assert ROLE_PERMISSIONS[RoleName.PLAYER] < ROLE_PERMISSIONS[RoleName.HOST]
    assert ROLE_PERMISSIONS[RoleName.HOST] < ROLE_PERMISSIONS[RoleName.ADMIN]


def test_quyen_quan_tri_chi_admin_co():
    admin_only = {
        PermissionCode.QUIZ_MODERATE,
        PermissionCode.USERS_MANAGE,
        PermissionCode.SYSTEM_READ,
    }
    for role in (RoleName.GUEST, RoleName.PLAYER, RoleName.HOST):
        assert not (ROLE_PERMISSIONS[role] & admin_only)
    assert admin_only <= ROLE_PERMISSIONS[RoleName.ADMIN]


# ---------------------------------------------------------------- seed (cần Postgres đã migrate)


async def test_seed_idempotent_va_dung_ma_tran(db_engine):
    factory = create_session_factory(db_engine)

    async with factory() as session, session.begin():
        await seed_rbac(session)
    async with factory() as session, session.begin():
        second = await seed_rbac(session)
    assert second == SeedResult(roles=0, permissions=0, role_permissions=0)

    async with factory() as session:
        rows = (
            await session.execute(
                select(Role.name, Permission.code)
                .join(role_permissions, role_permissions.c.role_id == Role.id)
                .join(Permission, Permission.id == role_permissions.c.permission_id)
            )
        ).all()
    actual = defaultdict(set)
    for role, code in rows:
        actual[role].add(code)
    expected = {r.value: {p.value for p in perms} for r, perms in ROLE_PERMISSIONS.items()}
    assert dict(actual) == expected
