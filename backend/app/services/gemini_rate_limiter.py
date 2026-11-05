"""Bộ kiểm soát tốc độ toàn cục cho Gemini API qua Redis (Rate Limiter).

Tuân thủ kiến trúc ADR-002:
1. RPM Limiter: Thuật toán Token Bucket thực thi bằng Redis Lua script nguyên tử,
   áp dụng đồng nhất trên tất cả worker chạy song song.
   - Hỗ trợ "dự trữ ưu tiên" (Priority Reservation): Tác vụ sinh quiz (high priority)
     được lấy token đến cạn; tác vụ báo cáo/khác (low priority) chỉ lấy token khi
     số dư bucket còn trên ngưỡng dự trữ an toàn (reserve threshold = 2).
2. RPD Counter: Đếm số lượng request trong ngày theo múi giờ Pacific (America/Los_Angeles),
   reset vào 00:00 Pacific, tự động hết hạn sau 48 giờ.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from app.ai.fake import GeminiRateLimitError
from app.core.config import get_settings

log = logging.getLogger("app.services.rate_limiter")

# Lua script cho thuật toán Token Bucket với Priority Reservation
TOKEN_BUCKET_LUA_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local reserved = tonumber(ARGV[5])

local data = redis.call('HMGET', key, 'tokens', 'last_updated')
local tokens = tonumber(data[1])
local last_updated = tonumber(data[2])

if tokens == nil or last_updated == nil then
    tokens = capacity
    last_updated = now
else
    local delta = math.max(0, now - last_updated)
    tokens = math.min(capacity, tokens + delta * refill_rate)
    last_updated = now
end

local required = cost + reserved
if tokens >= required then
    tokens = tokens - cost
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_updated', tostring(last_updated))
    redis.call('EXPIRE', key, 3600)
    return {1, 0, tostring(tokens)}
else
    local needed = required - tokens
    local wait_time = needed / refill_rate
    redis.call('HMSET', key, 'tokens', tostring(tokens), 'last_updated', tostring(last_updated))
    redis.call('EXPIRE', key, 3600)
    return {0, tostring(wait_time), tostring(tokens)}
end
"""


def get_pacific_date_str() -> str:
    """Lấy ngày hiện tại theo định dạng YYYY-MM-DD theo múi giờ Pacific (America/Los_Angeles)."""
    pacific_tz = ZoneInfo("America/Los_Angeles")
    return datetime.now(pacific_tz).strftime("%Y-%m-%d")


async def execute_token_bucket_lua(
    redis: Redis,
    *,
    key: str,
    capacity: int,
    refill_rate: float,
    cost: int,
    now: float,
    reserved: int,
) -> tuple[int, float, float]:
    """Thực thi Lua script Token Bucket trên Redis một cách an toàn."""
    keys = [key]
    args = [
        str(capacity),
        str(refill_rate),
        str(cost),
        str(now),
        str(reserved),
    ]

    # Kiểm tra phương thức eval trên Redis client (hỗ trợ cả real redis và fake redis)
    if hasattr(redis, "eval"):
        eval_result = redis.eval(TOKEN_BUCKET_LUA_SCRIPT, len(keys), *keys, *args)
        if asyncio.iscoroutine(eval_result) or hasattr(eval_result, "__await__"):
            res = await eval_result  # type: ignore[misc]
        else:
            res = eval_result
    else:
        # Fallback cho mock object nếu không có eval
        res = [1, "0", str(capacity - cost)]

    # Parse kết quả trả về từ Lua: [allowed (0/1), wait_time, remaining_tokens]
    allowed = int(res[0])
    wait_time = float(res[1])
    remaining = float(res[2])
    return allowed, wait_time, remaining


async def acquire_gemini_permit(
    redis: Redis,
    *,
    priority: str = "high",
    cost: int = 1,
    max_wait_s: float = 60.0,
    rpm_limit: int | None = None,
    rpd_limit: int | None = None,
    limiter_key: str = "gemini:limiter:rpm",
) -> bool:
    """Xin cấp quota (permit) trước khi gửi yêu cầu tới Gemini API.

    Quy trình:
    1. Kiểm tra giới hạn RPD (Requests Per Day) theo giờ Pacific.
    2. Nếu chưa vượt RPD, chạy Token Bucket qua Lua script để kiểm soát RPM.
    3. Nếu chưa đủ token, chờ đợi (sleep) theo thời gian `wait_time` do script tính toán.
    4. Nếu quá `max_wait_s` mà không lấy được token, raise GeminiRateLimitError.
    5. Sau khi lấy token thành công, tăng bộ đếm RPD và trả về True.
    """
    settings = get_settings()
    actual_rpm = rpm_limit or settings.gemini_rpm or 15
    actual_rpd = rpd_limit or settings.gemini_rpd or 1500

    # 1. Kiểm tra RPD theo ngày giờ Pacific
    pacific_date = get_pacific_date_str()
    rpd_key = f"gemini:rpd:{pacific_date}"

    current_rpd_str = await redis.get(rpd_key)
    current_rpd = int(current_rpd_str) if current_rpd_str is not None else 0
    if current_rpd >= actual_rpd:
        raise GeminiRateLimitError(
            message=f"Đã đạt giới hạn số lượng gọi Gemini trong ngày ({actual_rpd} RPD theo giờ Pacific)",
            retry_delay=60.0,
        )

    # 2. Token Bucket RPM
    refill_rate = actual_rpm / 60.0
    # Ngưỡng dự trữ: Tác vụ cao (sinh quiz) không cần dự trữ, tác vụ thấp dự trữ 2 token
    reserved = 0 if priority == "high" else 2

    start_time = time.monotonic()

    while True:
        now_ts = time.time()
        allowed, wait_time, remaining = await execute_token_bucket_lua(
            redis,
            key=limiter_key,
            capacity=actual_rpm,
            refill_rate=refill_rate,
            cost=cost,
            now=now_ts,
            reserved=reserved,
        )

        if allowed == 1:
            # Thành công: Tăng counter RPD
            await redis.incr(rpd_key)
            await redis.expire(rpd_key, 172800)  # Giữ 48 giờ để qua múi giờ
            log.debug(
                "Đã cấp permit Gemini (priority=%s, remaining=%.2f, rpd=%d/%d)",
                priority,
                remaining,
                current_rpd + 1,
                actual_rpd,
            )
            return True

        # Nếu không được cấp, tính thời gian chờ
        elapsed = time.monotonic() - start_time
        remaining_budget = max_wait_s - elapsed

        if remaining_budget <= 0 or wait_time > remaining_budget:
            raise GeminiRateLimitError(
                message=f"Hết thời gian chờ cấp quota Gemini RPM ({max_wait_s:.1f}s)",
                retry_delay=max(wait_time, 1.0),
            )

        sleep_duration = min(wait_time, remaining_budget, 5.0)
        log.warning(
            "Gemini RPM đầy (priority=%s), chờ %.2fs trước khi thử lại permit...",
            priority,
            sleep_duration,
        )
        await asyncio.sleep(sleep_duration)
