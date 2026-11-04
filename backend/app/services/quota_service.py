"""Dịch vụ quản lý hạn mức (Quota) của Host khi sinh quiz bằng AI.

Tuân thủ FR-AIGEN-13 và §7.12:
- Giới hạn mỗi host tối đa N lần sinh/ngày (cấu hình được, mặc định 5).
- Ngày tính theo múi giờ Pacific (America/Los_Angeles) của Google API.
- Khi vượt hạn mức, trả về 429 kèm thời điểm reset.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from app.core.errors import AppError

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def get_pacific_today() -> tuple[str, datetime, int]:
    """Lấy ngày hiện tại, thời điểm reset nửa đêm tiếp theo và số giây còn lại theo múi giờ Pacific.

    Returns:
        tuple[str, datetime, int]: (chuỗi ngày YYYY-MM-DD, datetime reset, số giây còn lại).
    """
    now_pacific = datetime.now(PACIFIC_TZ)
    today_str = now_pacific.strftime("%Y-%m-%d")
    next_midnight = (now_pacific + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    seconds_remaining = max(1, int((next_midnight - now_pacific).total_seconds()))
    return today_str, next_midnight, seconds_remaining


async def check_and_increment_host_ai_quota(
    redis: Redis,
    user_id: uuid.UUID,
    daily_limit: int,
) -> None:
    """Kiểm tra và tăng bộ đếm lượt sinh quiz của host trong ngày.

    Args:
        redis: Redis async client.
        user_id: ID của host.
        daily_limit: Số lượt tối đa mỗi ngày.

    Raises:
        AppError: HTTP 429 nếu host đã dùng hết hạn mức.
    """
    today_str, reset_at, seconds_left = get_pacific_today()
    quota_key = f"ai:quota:host:{user_id}:{today_str}"

    current_usage = await redis.get(quota_key)
    current_count = int(current_usage) if current_usage is not None else 0

    if current_count >= daily_limit:
        raise AppError(
            f"Bạn đã đạt giới hạn {daily_limit} lần sinh quiz trong ngày hôm nay (múi giờ Pacific).",
            code="HOST_DAILY_QUOTA_EXCEEDED",
            status_code=429,
            headers={"Retry-After": str(seconds_left)},
            details=[
                {
                    "daily_limit": daily_limit,
                    "current_usage": current_count,
                    "reset_at": reset_at.isoformat(),
                    "retry_after_seconds": seconds_left,
                }
            ],
        )

    # Tăng lượt và set TTL nếu mới tạo
    new_count = await redis.incr(quota_key)
    if new_count == 1:
        # Buffer thêm 1 giờ đề phòng lệch clock
        await redis.expire(quota_key, seconds_left + 3600)
