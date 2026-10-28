from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="QuizArena Realtime")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/echo")
async def echo(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_text(await ws.receive_text())
    except WebSocketDisconnect:
        pass  # client đóng kết nối: bình thường, không phải lỗi
