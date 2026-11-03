"""CLI quản trị (Typer).

Chạy:  uv run quizarena seed          hoặc        uv run python -m app.cli seed
       uv run quizarena create-admin --email admin@quizarena.io.vn
Trong container:                      python -m app.cli seed
"""

import asyncio
import re

import typer

from app.core.db import create_engine, create_session_factory
from app.core.rbac import RoleName
from app.core.security import hash_password_async
from app.repositories import user_repository as users
from app.services.seed import SeedResult, seed_rbac

app = typer.Typer(help="QuizArena — công cụ quản trị", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Công cụ dòng lệnh của QuizArena."""
    # Callback rỗng này ép Typer dùng dạng `quizarena <lệnh>` ngay cả khi mới có một lệnh.


async def _seed() -> SeedResult:
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session, session.begin():
            return await seed_rbac(session)  # session.begin(): tự commit, lỗi thì tự rollback
    finally:
        await engine.dispose()


@app.command()
def seed() -> None:
    """Nạp vai trò và quyền theo ma trận §3.2. Chạy nhiều lần vẫn an toàn."""
    result = asyncio.run(_seed())
    typer.echo(
        f"Đã tạo mới: {result.roles} vai trò, {result.permissions} quyền, "
        f"{result.role_permissions} lượt gán quyền."
    )


async def _create_admin(email: str, password: str) -> str:
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session, session.begin():
            await seed_rbac(session)
            email_normalized = email.strip().lower()
            admin_role_id = await users.role_id_by_name(session, RoleName.ADMIN.value)
            assert admin_role_id is not None

            user = await users.get_by_email(session, email_normalized)
            if user is None:
                pw_hash = await hash_password_async(password)
                user = await users.create(
                    session,
                    email=email_normalized,
                    password_hash=pw_hash,
                    display_name=email_normalized.split("@")[0],
                )
                await users.add_role(session, user.id, admin_role_id)
                return f"Đã tạo tài khoản admin thành công: {email_normalized}"

            user_roles = await users.get_roles(session, user.id)
            if RoleName.ADMIN.value not in user_roles:
                await users.add_role(session, user.id, admin_role_id)
                return f"Đã cấp quyền ADMIN cho tài khoản {email_normalized}"
            return f"Tài khoản {email_normalized} đã có quyền ADMIN từ trước."
    finally:
        await engine.dispose()


@app.command("create-admin")
def create_admin(
    email: str = typer.Option(..., "--email", "-e", help="Email tài khoản quản trị"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Mật khẩu (nếu bỏ trống sẽ được nhắc nhập)"
    ),
) -> None:
    """Tạo tài khoản quản trị viên mới hoặc cấp vai trò ADMIN cho người dùng sẵn có (§3.8)."""
    if not password:
        password = typer.prompt("Mật khẩu", hide_input=True, confirmation_prompt=True)

    if not (8 <= len(password) <= 128) or not (
        re.search(r"[A-Za-z]", password) and re.search(r"\d", password)
    ):
        typer.echo(
            "Lỗi: Mật khẩu phải dài từ 8 đến 128 ký tự và gồm cả chữ lẫn số.",
            err=True,
        )
        raise typer.Exit(code=1)

    msg = asyncio.run(_create_admin(email=email, password=password))
    typer.echo(msg)


if __name__ == "__main__":
    app()
