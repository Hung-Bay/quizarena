"""CLI quản trị (Typer).

Chạy:  uv run quizarena seed          hoặc        uv run python -m app.cli seed
Trong container:                      python -m app.cli seed
"""

import asyncio

import typer

from app.core.db import create_engine, create_session_factory
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


if __name__ == "__main__":
    app()
