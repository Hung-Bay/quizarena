import re

from fastapi.testclient import TestClient

from app.main_api import app

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_sinh_request_id_khi_khong_co():
    with TestClient(app) as c:
        rid = c.get("/api/v1/healthz").headers["X-Request-ID"]
    assert HEX32.match(rid)


def test_dung_lai_request_id_hop_le_tu_nginx():
    with TestClient(app) as c:
        r = c.get("/api/v1/healthz", headers={"X-Request-ID": "6a6e196e232b18dec89377d0bd023b9a"})
    assert r.headers["X-Request-ID"] == "6a6e196e232b18dec89377d0bd023b9a"


def test_thay_request_id_khong_hop_le():
    bad = "bad id <script>"
    with TestClient(app) as c:
        rid = c.get("/api/v1/healthz", headers={"X-Request-ID": bad}).headers["X-Request-ID"]
    assert rid != bad
    assert HEX32.match(rid)
