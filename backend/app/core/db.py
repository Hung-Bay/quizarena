"""Kết nối Postgres bất đồng bộ."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings


def create_engine() -> AsyncEngine:
    return create_async_engine(
        get_settings().database_url.get_secret_value(),
        pool_pre_ping=True,  # kiểm tra kết nối còn sống trước khi dùng
        pool_size=5,
        max_overflow=5,
    )
