import json
import logging

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, request_id_ctx
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
