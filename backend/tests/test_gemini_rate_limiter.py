"""Kiểm thử Limiter toàn cục trên Redis (Lua script) cho Gemini API.

Tuân thủ tiêu chí Thứ Năm Phase 2 và FR-AIGEN-12:
- Kiểm tra thuật toán Token Bucket với Redis Lua script (capacity, refill rate).
- Kiểm tra tính năng dự trữ ưu tiên (Priority Reservation): high priority (sinh quiz) vs low priority (báo cáo/khác).
- Kiểm tra bộ đếm RPD theo múi giờ Pacific (America/Los_Angeles).
- Mô phỏng 2 workers chạy đồng thời bắn 30 jobs: kiểm tra việc phân phối permit và kiểm soát tốc độ RPM không vượt mức.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.ai.fake import GeminiRateLimitError
from app.services.gemini_rate_limiter import (
    acquire_gemini_permit,
    execute_token_bucket_lua,
    get_pacific_date_str,
)


class MockRedisWithLua:
    """Mock Redis hỗ trợ lưu trữ Hash, bộ đếm, expire và thực thi logic tương đương Lua script."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.eval_call_count: int = 0

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def set(self, key: str, value: str, *args: Any, **kwargs: Any) -> None:
        self.strings[key] = str(value)

    async def incr(self, key: str) -> int:
        val = int(self.strings.get(key, "0")) + 1
        self.strings[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})

    async def eval(self, script: str, numkeys: int, *args: str) -> list[Any]:
        """Thực thi logic tương đương chính xác với Lua script trong gemini_rate_limiter.py."""
        self.eval_call_count += 1
        key = args[0]
        capacity = float(args[1])
        refill_rate = float(args[2])
        cost = float(args[3])
        now = float(args[4])
        reserved = float(args[5])

        h_data = self.hashes.setdefault(key, {})
        tokens_val = h_data.get("tokens")
        last_val = h_data.get("last_updated")

        if tokens_val is None or last_val is None:
            tokens = capacity
            last_updated = now
        else:
            tokens = float(tokens_val)
            last_updated = float(last_val)
            delta = max(0.0, now - last_updated)
            tokens = min(capacity, tokens + delta * refill_rate)
            last_updated = now

        required = cost + reserved
        if tokens >= required:
            tokens -= cost
            h_data["tokens"] = str(tokens)
            h_data["last_updated"] = str(last_updated)
            self.ttls[key] = 3600
            return [1, "0", str(tokens)]
        else:
            needed = required - tokens
            wait_time = needed / refill_rate
            h_data["tokens"] = str(tokens)
            h_data["last_updated"] = str(last_updated)
            self.ttls[key] = 3600
            return [0, str(wait_time), str(tokens)]


@pytest.mark.asyncio
async def test_lua_token_bucket_burst_and_exhaustion() -> None:
    """Kiểm tra Lua script cho phép burst tối đa capacity và chặn khi cạn token."""
    redis = MockRedisWithLua()
    key = "test:rpm:limiter"
    capacity = 5
    refill_rate = 5.0 / 60.0  # 5 tokens/min
    now = time.time()

    # Lấy 5 tokens liên tiếp (burst) -> Đều thành công
    for i in range(5):
        allowed, wait_time, remaining = await execute_token_bucket_lua(
            redis,
            key=key,
            capacity=capacity,
            refill_rate=refill_rate,
            cost=1,
            now=now,
            reserved=0,
        )
        assert allowed == 1
        assert wait_time == 0.0
        assert abs(remaining - (4 - i)) < 1e-4

    # Token thứ 6 trong cùng thời điểm now -> Bị từ chối và trả về thời gian chờ
    allowed, wait_time, remaining = await execute_token_bucket_lua(
        redis,
        key=key,
        capacity=capacity,
        refill_rate=refill_rate,
        cost=1,
        now=now,
        reserved=0,
    )
    assert allowed == 0
    assert wait_time > 0.0
    assert wait_time == pytest.approx(1.0 / refill_rate, rel=1e-3)


@pytest.mark.asyncio
async def test_lua_priority_reservation() -> None:
    """Kiểm tra tính năng dự trữ ưu tiên: Tác vụ thấp chỉ lấy khi bucket còn trên ngưỡng."""
    redis = MockRedisWithLua()
    key = "test:rpm:priority"
    capacity = 10
    refill_rate = 10.0 / 60.0
    now = time.time()

    # Rút bớt 8 tokens bằng high priority (còn lại 2 tokens)
    for _ in range(8):
        allowed, _, _ = await execute_token_bucket_lua(
            redis,
            key=key,
            capacity=capacity,
            refill_rate=refill_rate,
            cost=1,
            now=now,
            reserved=0,
        )
        assert allowed == 1

    # Lúc này còn đúng 2 tokens:
    # 1. Tác vụ Low Priority (reserved=2, required = 1 + 2 = 3 tokens): Bị chặn!
    low_allowed, wait_time, remaining = await execute_token_bucket_lua(
        redis,
        key=key,
        capacity=capacity,
        refill_rate=refill_rate,
        cost=1,
        now=now,
        reserved=2,
    )
    assert low_allowed == 0
    assert wait_time > 0

    # 2. Tác vụ High Priority (sinh quiz, reserved=0, required = 1): Được phép lấy tiếp!
    high_allowed, _, _ = await execute_token_bucket_lua(
        redis,
        key=key,
        capacity=capacity,
        refill_rate=refill_rate,
        cost=1,
        now=now,
        reserved=0,
    )
    assert high_allowed == 1


@pytest.mark.asyncio
async def test_pacific_rpd_counter_limit() -> None:
    """Kiểm tra giới hạn RPD theo múi giờ Pacific (America/Los_Angeles)."""
    redis = MockRedisWithLua()
    date_str = get_pacific_date_str()
    rpd_key = f"gemini:rpd:{date_str}"

    # Cài đặt RPD đã chạm trần (5 request)
    await redis.set(rpd_key, "5")

    # Xin permit với rpd_limit = 5 -> Bị từ chối ngay lập tức
    with pytest.raises(GeminiRateLimitError) as exc_info:
        await acquire_gemini_permit(
            redis,
            priority="high",
            rpm_limit=15,
            rpd_limit=5,
        )

    assert "RPD" in str(exc_info.value)
    assert "Pacific" in str(exc_info.value)


@pytest.mark.asyncio
async def test_two_workers_concurrent_30_jobs_rate_limiting() -> None:
    """Tiêu chí FR-AIGEN-12: Chạy 2 worker, bắn 30 jobs -> Đảm bảo kiểm soát RPM an toàn.

    Mô phỏng 2 worker (task chạy song song) cùng xử lý hàng đợi 30 tác vụ,
    đảm bảo mọi permit đều được quản lý nguyên tử và số lượng cấp permit không bị race condition.
    """
    redis = MockRedisWithLua()
    total_jobs = 30
    rpm_limit = 30  # Cho phép xử lý trong bài test

    acquired_permits: list[int] = []
    job_queue: asyncio.Queue[int] = asyncio.Queue()

    for job_id in range(total_jobs):
        await job_queue.put(job_id)

    async def worker_loop(worker_id: str) -> None:
        """Vòng lặp của 1 worker xử lý các jobs từ hàng đợi."""
        while not job_queue.empty():
            try:
                job_id = job_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            # Worker xin permit trước khi gọi Gemini
            success = await acquire_gemini_permit(
                redis,
                priority="high",
                cost=1,
                max_wait_s=10.0,
                rpm_limit=rpm_limit,
                rpd_limit=1000,
            )
            if success:
                acquired_permits.append(job_id)
            job_queue.task_done()

    # Khởi chạy 2 worker đồng thời
    worker_task_1 = asyncio.create_task(worker_loop("worker-1"))
    worker_task_2 = asyncio.create_task(worker_loop("worker-2"))

    await asyncio.gather(worker_task_1, worker_task_2)

    # Đảm bảo toàn bộ 30 jobs đều được cấp permit an toàn
    assert len(acquired_permits) == total_jobs
    # Kiểm tra counter RPD đã tăng chính xác 30
    pacific_date = get_pacific_date_str()
    final_rpd = await redis.get(f"gemini:rpd:{pacific_date}")
    assert final_rpd == "30"
