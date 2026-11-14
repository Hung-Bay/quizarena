"""Điểm vào của service api (REST)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

from app.api.v1 import admin, ai_jobs, auth, games, health, quizzes, realtime
from app.core.app_factory import create_app
from app.core.db import create_engine, create_session_factory
from app.core.metrics import metrics_endpoint_response
from app.core.redis import create_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = create_engine()
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.redis = create_redis()
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.engine.dispose()


app = create_app("QuizArena API", lifespan=lifespan)
app.include_router(health.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(games.router, prefix="/api/v1")
app.include_router(games.me_router, prefix="/api/v1")
app.include_router(realtime.router, prefix="/api/v1")
app.include_router(quizzes.router, prefix="/api/v1")
app.include_router(ai_jobs.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.get("/readyz")
@app.get("/api/v1/readyz")
async def readyz() -> dict[str, str]:
    """Readiness probe cho API gateway."""
    return {"status": "ready"}


@app.get("/metrics")
@app.get("/api/v1/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint (chỉ truy cập từ nội bộ hoặc qua monitoring)."""
    return metrics_endpoint_response()


_DEMO_HTML_PATH = Path(__file__).parent / "static" / "demo.html"


@app.get("/demo", response_class=HTMLResponse)
async def demo_page() -> HTMLResponse:
    """Giao diện HTML trực quan 2 tab (Host & Player) để kiểm thử thủ công."""
    if _DEMO_HTML_PATH.exists():
        return HTMLResponse(content=_DEMO_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h3>Chưa tìm thấy demo.html</h3>", status_code=404)
