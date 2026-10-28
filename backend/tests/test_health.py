import os

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import health
from app.main_api import app


@pytest.fixture
def client():
    # `with` để chạy lifespan (tạo engine/redis; chưa kết nối thật cho tới khi gọi readyz)
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    r = client.get("/api/v1/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_503_khi_redis_loi(client, monkeypatch):
    async def redis_fail(_client):
        return "fail"

    monkeypatch.setattr(health, "_check_redis", redis_fail)
    r = client.get("/api/v1/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["redis"] == "fail"


def test_readyz_voi_postgres_redis_that(client):
    """Cần Postgres + Redis đang chạy. CI đặt REQUIRE_SERVICES=1 nên ở CI test này bắt buộc phải qua."""
    r = client.get("/api/v1/readyz")
    if r.status_code == 503 and not os.getenv("REQUIRE_SERVICES"):
        pytest.skip("Postgres/Redis chưa chạy (đặt REQUIRE_SERVICES=1 để bắt buộc)")
    assert r.status_code == 200
    assert r.json()["checks"] == {"postgres": "ok", "redis": "ok"}
