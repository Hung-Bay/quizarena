import json
import logging

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, game_id_ctx, request_id_ctx, user_id_ctx
from app.main_api import app


def test_formatter_ra_json_mot_dong_co_request_id():
    token = request_id_ctx.set("abc12345")
    try:
        record = logging.LogRecord("x", logging.INFO, "f.py", 1, "xin chào %s", ("bạn",), None)
        record.status = 200  # trường thêm
        line = JsonFormatter().format(record)
    finally:
        request_id_ctx.reset(token)
    data = json.loads(line)
    assert "\n" not in line
    assert data["msg"] == "xin chào bạn"
    assert data["level"] == "INFO"
    assert data["request_id"] == "abc12345"
    assert data["status"] == 200


def test_access_log_ghi_method_path_status_va_request_id(caplog):
    with caplog.at_level(logging.INFO, logger="app.access"), TestClient(app) as c:
        r = c.get("/khong-co?ticket=bi-mat")
    rec = next(x for x in caplog.records if x.name == "app.access")
    assert (rec.method, rec.path, rec.status) == ("GET", "/khong-co", 404)
    assert rec.duration_ms >= 0
    # query string không được ghi vào log
    assert "bi-mat" not in json.dumps(rec.__dict__, default=str)
    assert r.headers["X-Request-ID"]


def test_healthz_thanh_cong_khong_ghi_access_log(caplog):
    with caplog.at_level(logging.INFO, logger="app.access"), TestClient(app) as c:
        c.get("/api/v1/healthz")
    assert not [x for x in caplog.records if x.name == "app.access"]


def test_formatter_co_replica_id_game_id_va_user_id():
    token_req = request_id_ctx.set("req-test-999")
    token_game = game_id_ctx.set("game-uuid-123")
    token_user = user_id_ctx.set("user-uuid-456")
    try:
        record = logging.LogRecord(
            "app.game", logging.INFO, "f.py", 1, "test log with context", (), None
        )
        line = JsonFormatter(replica_id="replica-node-1").format(record)
    finally:
        request_id_ctx.reset(token_req)
        game_id_ctx.reset(token_game)
        user_id_ctx.reset(token_user)

    data = json.loads(line)
    assert data["replica_id"] == "replica-node-1"
    assert data["request_id"] == "req-test-999"
    assert data["game_id"] == "game-uuid-123"
    assert data["user_id"] == "user-uuid-456"
    assert data["msg"] == "test log with context"


def test_metrics_endpoint_tra_ve_prometheus_format():
    with TestClient(app) as c:
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "game_answer_duration_seconds" in r.text
        assert "ws_connections" in r.text

        r_v1 = c.get("/api/v1/metrics")
        assert r_v1.status_code == 200
        assert "game_answer_duration_seconds" in r_v1.text
