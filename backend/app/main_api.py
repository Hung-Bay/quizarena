"""Điểm vào của service api (REST)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import health
from app.core.app_factory import create_app
from app.core.db import create_engine
from app.core.redis import create_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = create_engine()
    app.state.redis = create_redis()
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


app = create_app("QuizArena API", lifespan=lifespan)
app.include_router(health.router, prefix="/api/v1")
