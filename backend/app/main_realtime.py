from fastapi import FastAPI, WebSocket


app = FastAPI(title="QuizArena Realtime")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.websocket("/ws/echo")
async def echo(ws: WebSocket):
    await ws.accept()
    while True:
        await ws.send_text(await ws.receive_text())
