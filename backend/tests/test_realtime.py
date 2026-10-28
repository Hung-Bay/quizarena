from fastapi.testclient import TestClient

from app.main_realtime import app


def test_healthz():
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"status": "ok"}


def test_websocket_echo():
    with TestClient(app) as c, c.websocket_connect("/ws/echo") as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "hello"
