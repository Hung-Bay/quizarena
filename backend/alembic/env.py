"""Môi trường Alembic (async). URL lấy từ cấu hình ứng dụng, không đặt trong alembic.ini."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata của mọi model, để `alembic revision --autogenerate` so sánh với DB.
target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    """Chế độ offline: chỉ in SQL ra (alembic upgrade head --sql), không kết nối DB."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: migration là tiến trình chạy một lần rồi thoát, không cần giữ pool kết nối.
    connectable = create_async_engine(get_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
