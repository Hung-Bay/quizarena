"""Kịch bản Locust kiểm thử hiệu năng REST endpoints (NF-4 SLA).

Yêu cầu NF-4:
"REST endpoint thường p95 < 200 ms với 50 user đồng thời"

Mục tiêu kiểm thử:
- Mô phỏng 50 người dùng đồng thời (concurrent users).
- Gửi yêu cầu tuần tự tới các endpoint thường:
  - GET /healthz
  - GET /api/v1/healthz
  - GET /api/v1/readyz
  - GET /api/v1/quizzes/public
- Đo lường RPS, độ trễ trung bình, p50, p90, p95, p99.
"""

from __future__ import annotations

from locust import HttpUser, between, task


class QuizArenaRestUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(3)
    def test_healthz(self) -> None:
        self.client.get("/healthz", name="GET /healthz")

    @task(3)
    def test_api_healthz(self) -> None:
        self.client.get("/api/v1/healthz", name="GET /api/v1/healthz")

    @task(3)
    def test_readyz(self) -> None:
        self.client.get("/api/v1/readyz", name="GET /api/v1/readyz")

    @task(2)
    def test_public_quizzes(self) -> None:
        self.client.get("/api/v1/quizzes/public", name="GET /api/v1/quizzes/public")
