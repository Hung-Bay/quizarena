"""Service Realtime WebSocket cho QuizArena Live Game (FR-GAME-01..22, AU-11).

Chịu trách nhiệm:
- Điểm vào WebSocket `/ws/game` (xác thực qua 1-time ticket, phân quyền host/player).
- Điểm kiểm tra sức khỏe `/healthz`.
- Điểm thử nghiệm trực quan `/demo`.
- Quản lý vòng đời kết nối, giới hạn 5 msg/s, fan-out broadcast qua Redis Pub/Sub.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.v1 import realtime
from app.api.v1.realtime import redeem_ws_ticket
from app.core.app_factory import create_app
from app.core.logging import game_id_ctx, user_id_ctx
from app.core.metrics import WS_CONNECTIONS, metrics_endpoint_response
from app.core.redis import create_redis
from app.realtime.connection_manager import ConnectionManager
from app.realtime.handlers import MessageHandler
from app.realtime.snapshot import build_game_snapshot

log = logging.getLogger("app.realtime")

_DEMO_HTML_PATH = Path(__file__).parent / "static" / "demo.html"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Khởi tạo Redis, ConnectionManager và quản lý chu trình Graceful Drain."""
    app.state.is_draining = False
    app.state.redis = create_redis()
    app.state.manager = ConnectionManager(app.state.redis)
    app.state.handler = MessageHandler(app.state.manager, app.state.redis)

    loop = asyncio.get_running_loop()

    def _trigger_drain() -> None:
        if not getattr(app.state, "is_draining", False):
            app.state.is_draining = True
            log.warning("Nhận tín hiệu SIGTERM -> Kích hoạt Graceful Drain cho Realtime service")
            asyncio.create_task(app.state.manager.drain_and_close_all())

    try:
        loop.add_signal_handler(signal.SIGTERM, _trigger_drain)
    except (NotImplementedError, AttributeError):
        pass  # Windows event loop không hỗ trợ add_signal_handler

    log.info("QuizArena Realtime service started")
    try:
        yield
    finally:
        app.state.is_draining = True
        log.info("Lifespan shutdown: Bắt đầu drain và đóng mọi kết nối cục bộ")
        await app.state.manager.drain_and_close_all()
        await app.state.redis.aclose()
        log.info("QuizArena Realtime service stopped")


app = create_app("QuizArena Realtime", lifespan=lifespan)
app.include_router(realtime.router, prefix="/api/v1")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe cho Kubernetes / Docker healthcheck."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> Response:
    """Readiness probe cho Kubernetes / Nginx gateway."""
    if getattr(app.state, "is_draining", False):
        return JSONResponse(
            status_code=503,
            content={"status": "draining", "message": "Server đang drain kết nối"},
        )
    return JSONResponse(status_code=200, content={"status": "ready"})


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint cho Realtime service."""
    return metrics_endpoint_response()


@app.get("/demo", response_class=HTMLResponse)
async def demo_page() -> HTMLResponse:
    """Giao diện HTML trực quan 2 tab (Host & Player) để kiểm thử thủ công."""
    if _DEMO_HTML_PATH.exists():
        content = _DEMO_HTML_PATH.read_text(encoding="utf-8")
        return HTMLResponse(content=content)
    return HTMLResponse("<h3>Chưa tìm thấy demo.html</h3>", status_code=404)


@app.websocket("/ws/echo")
async def echo(ws: WebSocket) -> None:
    """Endpoint echo giữ tương thích ngược cho các bài kiểm tra kết nối cơ bản."""
    await ws.accept()
    try:
        while True:
            await ws.send_text(await ws.receive_text())
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/game")
async def game_websocket(
    ws: WebSocket,
    ticket: str | None = Query(default=None),
) -> None:
    """WebSocket chính cho Live Game (v=1 theo WS_PROTOCOL.md).

    Quy trình kết nối:
    1. Đổi ticket lấy danh tính (GETDEL atomic trên Redis - AU-11).
       Nếu không có hoặc vé không hợp lệ -> đóng với mã 4001 (TICKET_INVALID).
    2. Kiểm tra trạng thái trận: nếu trận không tồn tại hoặc đã FINISHED -> đóng với mã 4003 (GAME_ENDED).
    3. Kiểm tra kick-ban 10 phút: nếu bị cấm -> đóng với mã 4002 (KICKED).
    4. Chấp nhận kết nối, đăng ký ConnectionManager.
    5. Gửi ngay Game Snapshot ban đầu (thời gian còn lại, câu hỏi hiện tại không lộ đáp án, điểm hiện tại - FR-GAME-17).
    6. Vòng lặp nhận thông điệp, kiểm tra giới hạn 5 msg/s (FR-GAME-21), định tuyến qua MessageHandler.
    """
    if not ticket:
        await ws.close(code=4001, reason="TICKET_INVALID")
        return

    # 0. Nếu replica đang trong quá trình Drain -> Từ chối kết nối mới bằng mã 1013 (Try Again Later)
    if getattr(app.state, "is_draining", False):
        await ws.close(code=1013, reason="SERVER_DRAINING")
        return

    redis = app.state.redis
    manager: ConnectionManager = app.state.manager
    handler: MessageHandler = app.state.handler

    # 1. Đổi ticket dùng 1 lần lấy danh tính
    ticket_data = await redeem_ws_ticket(redis, ticket)
    if not ticket_data:
        await ws.close(code=4001, reason="TICKET_INVALID")
        return

    game_id = str(ticket_data.get("game_id", ""))
    sub = str(ticket_data.get("sub", ""))
    permissions = set(ticket_data.get("permissions", []))

    # 2. Kiểm tra trạng thái trận từ Redis
    state = await redis.hgetall(f"game:{game_id}:state")  # type: ignore[misc]
    if not state:
        await ws.close(code=4003, reason="GAME_ENDED")
        return

    status = state.get("status")
    if status == "finished":
        await ws.close(code=4003, reason="GAME_ENDED")
        return

    # 3. Phân biệt Host vs Player
    host_id = state.get("host_id")
    is_host = ("game:host_own" in permissions) or (host_id == sub)

    if is_host:
        client_id = sub
        nickname = "Host"
    else:
        # Nếu là guest token thì sub dạng "guest:<player_id>"
        if sub.startswith("guest:"):
            client_id = sub[len("guest:") :]
        else:
            client_id = sub

        # Kiểm tra người chơi có bị kick ban 10 phút không (FR-GAME-05)
        if await redis.exists(f"game:{game_id}:kicked_ids:{client_id}"):
            await ws.close(code=4002, reason="KICKED")
            return

        nickname = (
            await redis.hget(f"game:{game_id}:nicknames", client_id)  # type: ignore[misc]
            or "Player"
        )
        if await redis.exists(f"game:{game_id}:kicked:{nickname.lower()}"):
            await ws.close(code=4002, reason="KICKED")
            return

    # 4. Chấp nhận kết nối
    await ws.accept()
    game_id_ctx.set(game_id)
    user_id_ctx.set(client_id)
    WS_CONNECTIONS.inc()
    conn = await manager.connect(
        ws,
        game_id=game_id,
        client_id=client_id,
        nickname=nickname,
        is_host=is_host,
    )
    # Đánh dấu trạng thái online ban đầu (TTL 45s, làm mới định kỳ qua heartbeat 15s - FR-GAME-18)
    await redis.set(f"game:{game_id}:presence:{client_id}", "1", ex=45)

    try:
        # 5. Gửi snapshot trạng thái hiện tại ngay sau khi kết nối thành công (FR-GAME-17)
        snapshot_envelope = await build_game_snapshot(
            redis,
            game_id=game_id,
            client_id=client_id,
            is_host=is_host,
        )
        await manager.send_personal(ws, snapshot_envelope)

        # 6. Vòng lặp nhận thông điệp từ client
        while True:
            raw_text = await ws.receive_text()

            # Giới hạn tốc độ: 5 thông điệp / giây mỗi kết nối (FR-GAME-21)
            if not conn.rate_limiter.allow():
                err_env = {
                    "type": "error",
                    "v": 1,
                    "data": {
                        "code": "RATE_LIMITED",
                        "message": "Quá giới hạn 5 thông điệp/giây (FR-GAME-21)",
                    },
                }
                await manager.send_personal(ws, err_env)
                continue

            await handler.handle_message(conn, raw_text)

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("Lỗi kết nối WebSocket của client %s (game %s): %s", client_id, game_id, exc)
    finally:
        WS_CONNECTIONS.dec()
        await redis.delete(f"game:{game_id}:presence:{client_id}")
        await manager.disconnect(game_id, client_id)
