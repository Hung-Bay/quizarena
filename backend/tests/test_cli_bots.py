"""Kiểm thử tính năng CLI Bots Runner (FR-GAME-01..06, 14, 16, GM-13).

Kiểm tra:
- Cấu hình BotConfig và thống kê BotStats.
- Vòng đời SingleBot: REST join, WS ticket, kết nối WS, nhận câu hỏi, jitter, nộp bài, nhận ack.
- Xử lý các sự kiện WS: question.start, answer.ack, game.finished, player.kicked.
- BotManager điều phối đồng thời nhiều bot, xử lý ngắt an toàn.
- Lệnh Typer CLI `quizarena bots`: kiểm tra PIN, số lượng bot, gọi runner.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from app.cli import app
from app.game.bot_runner import BotConfig, BotManager, BotStats, SingleBot

runner = CliRunner()


def test_bot_config_va_stats_khoi_tao() -> None:
    """Kiểm tra khởi tạo BotConfig và BotStats với giá trị mặc định."""
    config = BotConfig(pin="123456", count=50)
    assert config.pin == "123456"
    assert config.count == 50
    assert config.base_url == "http://localhost:8000"
    assert config.jitter_min == 0.5
    assert config.jitter_max == 2.0
    assert config.prefix == "Bot"

    stats = BotStats(total_bots=50)
    assert stats.total_bots == 50
    assert stats.joined == 0
    assert stats.connected == 0
    assert stats.answers_submitted == 0
    assert stats.answers_accepted == 0
    assert stats.completed == 0
    assert len(stats.errors) == 0


def test_single_bot_resolve_ws_url() -> None:
    """Kiểm tra suy diễn URL WebSocket từ HTTP/HTTPS hoặc URL tùy biến."""
    config = BotConfig(pin="123456", base_url="http://localhost:8000")
    stats = BotStats()
    bot = SingleBot(1, config, stats)
    bot.ticket = "test-ticket-xyz"

    # URL tự động từ HTTP
    assert bot._resolve_ws_url() == "ws://localhost:8000/ws/game?ticket=test-ticket-xyz"

    # URL tự động từ HTTPS
    bot.config.base_url = "https://quizarena.io.vn"
    assert bot._resolve_ws_url() == "wss://quizarena.io.vn/ws/game?ticket=test-ticket-xyz"

    # URL tùy biến
    bot.config.ws_url = "ws://custom-host:9000/game"
    assert bot._resolve_ws_url() == "ws://custom-host:9000/game?ticket=test-ticket-xyz"


@pytest.mark.asyncio
async def test_single_bot_join_game_thanh_cong_va_that_bai() -> None:
    """Kiểm tra bot tham gia qua REST API: thành công nhận token, thất bại ghi nhận lỗi."""
    config = BotConfig(pin="123456", base_url="http://localhost:8000")
    stats = BotStats()
    bot = SingleBot(1, config, stats)

    # 1. Thành công
    game_id = str(uuid.uuid4())
    player_id = str(uuid.uuid4())

    async def mock_handler_success(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "token": "guest-jwt-token-123",
                "game_id": game_id,
                "player_id": player_id,
                "nickname": "Bot-01",
            },
        )

    transport = httpx.MockTransport(mock_handler_success)
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await bot._join_game(client)
        assert ok is True
        assert bot.token == "guest-jwt-token-123"
        assert bot.game_id == game_id
        assert bot.player_id == player_id

    # 2. Thất bại (404 Game not found)
    async def mock_handler_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Mã PIN không tồn tại"})

    transport_fail = httpx.MockTransport(mock_handler_fail)
    async with httpx.AsyncClient(transport=transport_fail) as client:
        bot_fail = SingleBot(2, config, stats)
        ok_fail = await bot_fail._join_game(client)
        assert ok_fail is False
        assert len(stats.errors) >= 1


@pytest.mark.asyncio
async def test_single_bot_get_ws_ticket_thanh_cong_va_that_bai() -> None:
    """Kiểm tra bot đổi Guest token lấy WS ticket 1 lần."""
    config = BotConfig(pin="123456", base_url="http://localhost:8000")
    stats = BotStats()
    bot = SingleBot(1, config, stats)
    bot.token = "valid-token"
    bot.game_id = str(uuid.uuid4())

    # 1. Thành công
    async def mock_ticket_success(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ticket": "ws-ticket-abc", "expires_in": 30})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_ticket_success)) as client:
        ok = await bot._get_ws_ticket(client)
        assert ok is True
        assert bot.ticket == "ws-ticket-abc"

    # 2. Thất bại (403 Forbidden)
    async def mock_ticket_fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Không có quyền"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_ticket_fail)) as client:
        bot_fail = SingleBot(2, config, stats)
        bot_fail.token = "invalid-token"
        bot_fail.game_id = str(uuid.uuid4())
        ok_fail = await bot_fail._get_ws_ticket(client)
        assert ok_fail is False


@pytest.mark.asyncio
async def test_single_bot_handle_ws_messages() -> None:
    """Kiểm tra xử lý các thông điệp WebSocket: question.start, answer.ack, game.finished, player.kicked."""
    config = BotConfig(pin="123456", jitter_min=0.01, jitter_max=0.02)
    stats = BotStats()
    bot = SingleBot(1, config, stats)
    bot.player_id = "pid-1"
    bot.is_running = True

    mock_ws = AsyncMock()
    bot.ws = mock_ws

    # 1. question.start -> nộp đáp án
    opt1_id = str(uuid.uuid4())
    opt2_id = str(uuid.uuid4())
    q_start_envelope = {
        "type": "question.start",
        "v": 1,
        "data": {
            "q_index": 1,
            "total_questions": 3,
            "options": [
                {"id": opt1_id, "position": 1, "content": "A"},
                {"id": opt2_id, "position": 2, "content": "B"},
            ],
        },
    }
    await bot._handle_ws_message(q_start_envelope)
    assert stats.answers_submitted == 1
    assert mock_ws.send.called
    sent_text = mock_ws.send.call_args[0][0]
    sent_data = json.loads(sent_text)
    assert sent_data["type"] == "answer.submit"
    assert sent_data["data"]["q_index"] == 1
    assert sent_data["data"]["selected_option_ids"][0] in (opt1_id, opt2_id)

    # 2. answer.ack -> ghi nhận accepted
    ack_envelope = {
        "type": "answer.ack",
        "v": 1,
        "data": {"accepted": True, "q_index": 1},
    }
    await bot._handle_ws_message(ack_envelope)
    assert stats.answers_accepted == 1

    # 3. game.finished -> kết thúc vòng lặp
    finished_envelope = {
        "type": "game.finished",
        "v": 1,
        "data": {"game_id": str(uuid.uuid4())},
    }
    await bot._handle_ws_message(finished_envelope)
    assert stats.completed == 1
    assert bot.is_running is False

    # 4. player.kicked cho bot khác vs chính bot này
    bot.is_running = True
    # Kick bot khác -> không ảnh hưởng
    await bot._handle_ws_message(
        {"type": "player.kicked", "v": 1, "data": {"player_id": "other-pid"}}
    )
    assert bot.is_running is True

    # Kick chính bot này -> ngắt
    await bot._handle_ws_message({"type": "player.kicked", "v": 1, "data": {"player_id": "pid-1"}})
    assert bot.is_running is False


@pytest.mark.asyncio
async def test_bot_manager_start_and_stop() -> None:
    """Kiểm tra BotManager khởi tạo bầy bot và điều phối dừng an toàn."""
    config = BotConfig(pin="654321", count=3, join_stagger_s=0.01)
    manager = BotManager(config)
    assert len(manager.bots) == 3

    # Giả lập SingleBot.run chạy hoàn thành
    for bot in manager.bots:
        bot.run = AsyncMock()  # type: ignore[method-assign]

    stats_task = asyncio.create_task(manager.start())
    await asyncio.sleep(0.05)
    await manager.stop()
    stats = await stats_task

    assert stats.total_bots == 3


def test_cli_bots_command_validation() -> None:
    """Kiểm tra xác thực đầu vào của lệnh CLI `quizarena bots`."""
    # 1. PIN không hợp lệ (không phải 6 chữ số)
    res_bad_pin = runner.invoke(app, ["bots", "--pin", "123"])
    assert res_bad_pin.exit_code == 1
    assert "Mã PIN phải gồm đúng 6 chữ số" in res_bad_pin.output

    # 2. Count không hợp lệ (< 1 hoặc > 500)
    res_bad_count = runner.invoke(app, ["bots", "--pin", "123456", "--count", "0"])
    assert res_bad_count.exit_code == 1
    assert "Số lượng bot phải nằm trong khoảng từ 1 đến 500" in res_bad_count.output

    # 3. Thành công với Mocked BotManager.start
    mock_stats = BotStats(
        total_bots=5,
        joined=5,
        connected=5,
        answers_submitted=10,
        answers_accepted=10,
        completed=5,
    )

    with patch("app.game.bot_runner.BotManager.start", new_callable=AsyncMock) as mock_start:
        mock_start.return_value = mock_stats
        res_ok = runner.invoke(app, ["bots", "--pin", "123456", "--count", "5"])
        assert res_ok.exit_code == 0
        assert "QUIZARENA BOT RUNNER — PIN: 123456" in res_ok.output
        assert "Số bot kết nối WS        : 5" in res_ok.output
        assert "Số lượt nộp bài          : 10" in res_ok.output
