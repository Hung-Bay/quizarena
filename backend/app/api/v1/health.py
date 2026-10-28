import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger("app.health")
router = APIRouter(tags=["health"])

_TIMEOUT_S = 2.0


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: tiến trình còn chạy. KHÔNG kiểm tra phụ thuộc bên ngoài."""
    return {"status": "ok"}


async def _check_postgres(engine: AsyncEngine) -> str:
    try:
        async with asyncio.timeout(_TIMEOUT_S):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        log.warning("postgres không sẵn sàng", extra={"error": type(exc).__name__})
        return "fail"


async def _check_redis(client: Redis) -> str:
    try:
        async with asyncio.timeout(_TIMEOUT_S):
            await client.ping()
        return "ok"
    except Exception as exc:
        log.warning("redis không sẵn sàng", extra={"error": type(exc).__name__})
        return "fail"


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: kiểm tra Postgres và Redis. 503 nếu một trong hai lỗi."""
    checks = {
        "postgres": await _check_postgres(request.app.state.engine),
        "redis": await _check_redis(request.app.state.redis),
    }
    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "unavailable", "checks": checks},
        status_code=200 if ready else 503,
    )
