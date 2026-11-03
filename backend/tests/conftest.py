"""Đặt biến môi trường mặc định TRƯỚC khi import app (Settings đọc lúc import).

Giá trị trong môi trường thật (CI, .env) luôn được ưu tiên nhờ setdefault.
"""

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://quizarena:test@localhost:5432/quizarena"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.db import create_engine


async def db_ready() -> str | None:
    """None nếu Postgres chạy và đã migrate; ngược lại trả lý do."""
    engine = create_engine()
    try:
        async with engine.connect() as conn:
            found = (await conn.execute(text("SELECT to_regclass('role_permissions')"))).scalar()
        return None if found else "chưa chạy `alembic upgrade head`"
    except Exception as exc:  # noqa: BLE001 - mọi lỗi kết nối đều có nghĩa là "chưa sẵn sàng"
        return f"không kết nối được Postgres ({type(exc).__name__})"
    finally:
        await engine.dispose()


def skip_or_fail(reason: str) -> None:
    """CI đặt REQUIRE_SERVICES=1 → thiếu dịch vụ là LỖI; máy local → bỏ qua test."""
    if os.getenv("REQUIRE_SERVICES"):
        pytest.fail(f"Cần Postgres đã migrate: {reason}")
    pytest.skip(f"Cần Postgres đã migrate: {reason}")


@pytest_asyncio.fixture
async def db_engine():
    reason = await db_ready()
    if reason:
        skip_or_fail(reason)
    engine = create_engine()
    yield engine
    await engine.dispose()
