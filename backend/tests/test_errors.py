"""Chuẩn lỗi {"error": {"code", "message", "request_id"}} ở mọi loại lỗi."""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.app_factory import create_app
from app.core.errors import AppError


class Body(BaseModel):
    name: str


@pytest.fixture
def client():
    app = create_app("test")

    @app.get("/loi-nghiep-vu")
    async def loi_nghiep_vu():
        raise AppError("Trận không tồn tại", code="GAME_NOT_FOUND", status_code=404)

    @app.get("/loi-bat-ngo")
    async def loi_bat_ngo():
        raise RuntimeError("chi tiết nội bộ nhạy cảm")

    @app.post("/can-body")
    async def can_body(body: Body):
        return body

    # raise_server_exceptions=False: để test thấy response 500 thay vì ngoại lệ bị ném lại
    return TestClient(app, raise_server_exceptions=False)


def _assert_chuan(r, status, code):
    assert r.status_code == status
    err = r.json()["error"]
    assert err["code"] == code
    assert err["request_id"] == r.headers["X-Request-ID"]
    assert isinstance(err["message"], str) and err["message"]
    return err


def test_404_route_khong_ton_tai(client):
    _assert_chuan(client.get("/khong-co"), 404, "NOT_FOUND")


def test_405_sai_method(client):
    _assert_chuan(client.post("/loi-nghiep-vu"), 405, "METHOD_NOT_ALLOWED")


def test_422_du_lieu_sai(client):
    err = _assert_chuan(client.post("/can-body", json={}), 422, "VALIDATION_ERROR")
    assert err["details"][0]["loc"] == ["body", "name"]


def test_apperror_ma_loi_rieng(client):
    err = _assert_chuan(client.get("/loi-nghiep-vu"), 404, "GAME_NOT_FOUND")
    assert err["message"] == "Trận không tồn tại"


def test_500_khong_lo_chi_tiet_noi_bo(client):
    r = client.get("/loi-bat-ngo")
    err = _assert_chuan(r, 500, "INTERNAL_ERROR")
    assert "nhạy cảm" not in r.text
    assert "RuntimeError" not in r.text
    assert err["request_id"]
