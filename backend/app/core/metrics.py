"""Hệ thống đo lường Prometheus Metrics cho QuizArena (NF-2, NF-3, §7.9, Phase 5).

Nguyên tắc thiết kế:
- Bucket dày quanh ngưỡng SLA 150ms cho `game_answer_duration_seconds` (NF-2).
- TUYỆT ĐỐI KHÔNG đặt `game_id` hay `user_id` làm nhãn (cardinality phình to làm sập Prometheus).
- Phơi endpoint `/metrics` trên cả API và Realtime service.
"""

from __future__ import annotations

from fastapi import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# 1. Histogram đo thời gian xử lý nộp đáp án phía server (NF-2: p95 < 150ms)
# Bucket dày quanh 150ms để tính histogram_quantile(0.95, ...) chuẩn xác
ANSWER_DURATION_BUCKETS = (
    0.01,  # 10ms
    0.025,  # 25ms
    0.05,  # 50ms
    0.075,  # 75ms
    0.1,  # 100ms
    0.125,  # 125ms
    0.15,  # 150ms (ngưỡng SLA NF-2)
    0.175,  # 175ms
    0.2,  # 200ms
    0.25,  # 250ms
    0.3,  # 300ms
    0.5,  # 500ms
    1.0,  # 1s
    2.5,  # 2.5s
)

GAME_ANSWER_DURATION_SECONDS = Histogram(
    "game_answer_duration_seconds",
    "Thời gian xử lý nộp đáp án phía server tính từ lúc nhận message đến khi ack (giây)",
    labelnames=["status"],
    buckets=ANSWER_DURATION_BUCKETS,
)

# 2. Histogram đo thời gian broadcast thông điệp tới client (NF-3: p95 < 500ms)
BROADCAST_DURATION_BUCKETS = (
    0.005,  # 5ms
    0.01,  # 10ms
    0.025,  # 25ms
    0.05,  # 50ms
    0.1,  # 100ms
    0.2,  # 200ms
    0.3,  # 300ms
    0.5,  # 500ms (ngưỡng SLA NF-3)
    1.0,  # 1s
    2.0,  # 2s
)

GAME_BROADCAST_DURATION_SECONDS = Histogram(
    "game_broadcast_duration_seconds",
    "Thời gian broadcast thông điệp từ server tới client (giây)",
    labelnames=["msg_type"],
    buckets=BROADCAST_DURATION_BUCKETS,
)

# 3. Gauge đo số kết nối WebSocket trên replica hiện tại
WS_CONNECTIONS = Gauge(
    "ws_connections",
    "Số kết nối WebSocket đang hoạt động trên replica hiện tại",
)

# 4. Histogram đo thời gian xử lý HTTP request
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "Thời gian xử lý HTTP request (giây)",
    labelnames=["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# 5. Counter số lượt gọi Gemini API và token
GEMINI_CALLS_TOTAL = Counter(
    "gemini_calls_total",
    "Tổng số lượt gọi Gemini API",
    labelnames=["purpose", "status"],
)

GEMINI_TOKENS_TOTAL = Counter(
    "gemini_tokens_total",
    "Tổng số token Gemini API sử dụng",
    labelnames=["model", "type"],
)

# 6. Counter số lượng đáp án
GAME_ANSWERS_TOTAL = Counter(
    "game_answers_total",
    "Tổng số lượt nộp đáp án",
    labelnames=["status"],
)

# 7. Gauge số lượng trận đấu đang mở
GAMES_ACTIVE = Gauge(
    "games_active",
    "Số lượng trận đấu đang mở",
)


def metrics_endpoint_response() -> Response:
    """Trả về dữ liệu metrics chuẩn Prometheus text format."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
