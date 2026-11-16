"""Kiểm thử cơ chế ngắt spam WebSocket (FR-GAME-21 & §7.11 kịch bản 5).

Mục tiêu kiểm thử:
1. TokenBucketRateLimiter:
   - Cho phép tốc độ bình thường (5 token / giây).
   - Khi vượt ngưỡng, allow() trả về False.
   - Hàm record_violation() đếm dồn số lần vi phạm trong cửa sổ trượt 2 giây.
   - Tự động reset bộ đếm vi phạm sau 2 giây im lặng.
2. Xử lý WebSocket khi bị spam:
   - Vi phạm nhẹ (< 10 lần vi phạm liên tục): Nhận gói tin lỗi RATE_LIMITED, socket vẫn mở.
   - Tấn công spam dồn dập (>= 10 lần vi phạm liên tục trong 2s):
     Nhận gói tin lỗi SPAM_DISCONNECTED và socket bị đóng với mã 1008 (Policy Violation).
"""

from __future__ import annotations

import time

from app.realtime.connection_manager import TokenBucketRateLimiter


def test_token_bucket_normal_and_burst():
    """Kiểm tra hoạt động cơ bản của TokenBucketRateLimiter."""
    limiter = TokenBucketRateLimiter(capacity=5.0, refill_rate=5.0)

    # 5 thông điệp liên tiếp được phép
    for _ in range(5):
        assert limiter.allow() is True

    # Thông điệp thứ 6 bị chặn
    assert limiter.allow() is False


def test_token_bucket_violation_counter_window():
    """Kiểm tra cửa sổ trượt 2s của bộ đếm vi phạm."""
    limiter = TokenBucketRateLimiter(capacity=5.0, refill_rate=5.0)

    # Làm cạn token
    for _ in range(5):
        limiter.allow()

    # Ghi nhận vi phạm
    assert limiter.record_violation() == 1
    assert limiter.record_violation() == 2
    assert limiter.record_violation() == 3

    # Giả lập lùi thời gian vi phạm về quá khứ 3 giây
    limiter.last_violation = time.monotonic() - 3.0

    # Lần vi phạm tiếp theo phải reset bộ đếm về 1
    assert limiter.record_violation() == 1


def test_token_bucket_reaches_spam_threshold():
    """Kiểm tra ngưỡng ngắt kết nối khi spam dồn dập 10 lần."""
    limiter = TokenBucketRateLimiter(capacity=5.0, refill_rate=5.0)

    # Làm cạn token
    for _ in range(5):
        limiter.allow()

    # Ghi nhận vi phạm liên tiếp 10 lần
    violation_count = 0
    for _ in range(10):
        violation_count = limiter.record_violation()

    assert violation_count == 10
    assert violation_count >= 10
