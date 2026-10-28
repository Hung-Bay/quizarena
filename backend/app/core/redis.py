"""Kết nối Redis bất đồng bộ."""

from redis.asyncio import Redis

from app.core.config import get_settings


def create_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url.get_secret_value(), decode_responses=True)
