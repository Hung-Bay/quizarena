"""Kiểm thử cơ chế chống Brute-force PIN phòng (§7.11 kịch bản 6).

Mục tiêu kiểm thử:
1. Nhập sai PIN 1..9 lần từ cùng một IP:
   - Trả về mã lỗi 404 GAME_NOT_FOUND.
   - Bộ đếm pin_fail:{ip} tăng dần và có TTL 600s (10 phút).
2. Lần thứ 10 nhập sai PIN:
   - Trả về mã lỗi 429 PIN_BRUTE_FORCE_LOCKED.
   - Đặt khóa pin_lock:{ip} trong 900s (15 phút).
   - Xóa bộ đếm pin_fail:{ip}.
3. Lần nhập thứ 11 (trong khi đang bị khóa):
   - Ngay lập tức bị chặn với 429 PIN_BRUTE_FORCE_LOCKED kèm số giây còn lại.
   - Không thực hiện tra cứu DB/Redis PIN.
4. Khi nhập đúng mã PIN:
   - Bộ đếm pin_fail:{ip} được xóa sạch.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.core.errors import AppError
from app.services.game_service import join_game

REDIS_TEST_URL = os.environ.get(
    "REDIS_TEST_URL",
    os.environ.get("REDIS_URL", "redis://:AK.Gamer1@localhost:6380/0"),
)


@pytest_asyncio.fixture
async def redis():
    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Cần Redis server đang chạy: {exc}")
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_pin_brute_force_lock_after_10_failures(redis):
    """Nhập sai PIN 10 lần liên tiếp dẫn đến khóa IP 15 phút."""
    test_ip = f"198.51.100.{uuid.uuid4().int % 250 + 1}"
    fail_key = f"pin_fail:{test_ip}"
    lock_key = f"pin_lock:{test_ip}"

    # Dọn dẹp key trước khi test
    await redis.delete(fail_key, lock_key)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # 1. Thử sai 9 lần đầu tiên -> Đều nhận 404 GAME_NOT_FOUND
    for i in range(1, 10):
        with pytest.raises(AppError) as exc_info:
            await join_game(
                mock_session,
                redis,
                pin="999999",
                nickname="BotAttacker",
                client_ip=test_ip,
            )
        assert exc_info.value.code == "GAME_NOT_FOUND"
        assert exc_info.value.status_code == 404

        current_fails = await redis.get(fail_key)
        assert int(current_fails) == i
        ttl = await redis.ttl(fail_key)
        assert 0 < ttl <= 600

    # 2. Lần thứ 10 nhập sai -> Bị khóa 429 PIN_BRUTE_FORCE_LOCKED
    with pytest.raises(AppError) as exc_info:
        await join_game(
            mock_session,
            redis,
            pin="999999",
            nickname="BotAttacker",
            client_ip=test_ip,
        )
    assert exc_info.value.code == "PIN_BRUTE_FORCE_LOCKED"
    assert exc_info.value.status_code == 429

    # Kiểm tra key khóa tồn tại và có TTL gần 900s
    lock_exists = await redis.exists(lock_key)
    assert lock_exists == 1
    lock_ttl = await redis.ttl(lock_key)
    assert 800 <= lock_ttl <= 900

    # Bộ đếm sai đã được xóa
    fail_exists = await redis.exists(fail_key)
    assert fail_exists == 0

    # 3. Lần thứ 11 trong thời gian bị khóa -> Bị từ chối ngay lập tức với 429
    with pytest.raises(AppError) as exc_info:
        await join_game(
            mock_session,
            redis,
            pin="123456",
            nickname="BotAttacker",
            client_ip=test_ip,
        )
    assert exc_info.value.code == "PIN_BRUTE_FORCE_LOCKED"
    assert exc_info.value.status_code == 429
    assert "Vui lòng thử lại sau" in exc_info.value.message

    # Dọn dẹp
    await redis.delete(fail_key, lock_key)


@pytest.mark.asyncio
async def test_pin_brute_force_reset_on_correct_pin(redis):
    """Khi nhập đúng mã PIN, bộ đếm lỗi của IP bị xóa."""
    test_ip = f"198.51.100.{uuid.uuid4().int % 250 + 1}"
    fail_key = f"pin_fail:{test_ip}"
    lock_key = f"pin_lock:{test_ip}"

    await redis.delete(fail_key, lock_key)

    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Nhập sai 3 lần
    for _ in range(3):
        with pytest.raises(AppError):
            await join_game(
                mock_session,
                redis,
                pin="999999",
                nickname="BotAttacker",
                client_ip=test_ip,
            )

    current_fails = await redis.get(fail_key)
    assert int(current_fails) == 3

    # Giả lập mã PIN hợp lệ trong Redis
    valid_pin = "888888"
    fake_game_id = uuid.uuid4()
    await redis.set(f"pin:{valid_pin}", str(fake_game_id), ex=60)

    # Giả lập game_session trong DB hoặc để tiếp tục xử lý
    # Khi PIN hợp lệ, join_game sẽ xóa fail_key trước khi kiểm tra trạng thái DB
    try:
        await join_game(
            mock_session,
            redis,
            pin=valid_pin,
            nickname="LegitPlayer",
            client_ip=test_ip,
        )
    except Exception:
        pass  # Không quan tâm các bước sau của join_game

    # Bộ đếm sai phải được xóa
    fail_after = await redis.get(fail_key)
    assert fail_after is None

    # Dọn dẹp
    await redis.delete(fail_key, lock_key, f"pin:{valid_pin}")
