"""Mô-đun Bot Runner cho Live Game QuizArena (FR-GAME-01..06, 14, 16, GM-13).

Tính năng:
- Sinh N bots (mặc định 50) tham gia phòng thi live qua mã PIN.
- Tự động lấy Guest Token (AU-9) và đổi WS Ticket 1 lần (AU-11).
- Kết nối đồng thời qua WebSocket, lắng nghe các sự kiện và duy trì heartbeat 15s (FR-GAME-18).
- Nhận question.start -> chọn ngẫu nhiên phương án -> nộp bài kèm jitter (0.5s - 2.5s) (FR-GAME-07).
- Hỗ trợ dừng an toàn khi nhận Ctrl+C hoặc khi nhận game.finished.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets
from websockets.asyncio.client import ClientConnection as WSClient

log = logging.getLogger("app.game.bot_runner")


@dataclass
class BotConfig:
    """Cấu hình cho bầy bot."""

    pin: str
    count: int = 50
    base_url: str = "http://localhost:8000"
    ws_url: str | None = None
    jitter_min: float = 0.5
    jitter_max: float = 2.0
    prefix: str = "Bot"
    join_stagger_s: float = 0.03  # Giãn cách 30ms giữa các bot khi join


@dataclass
class BotStats:
    """Thống kê hoạt động của bầy bot và các chỉ số đo lường hiệu năng (NF-1, NF-2, NF-3, NF-5)."""

    total_bots: int = 0
    joined: int = 0
    connected: int = 0
    answers_submitted: int = 0
    answers_accepted: int = 0
    answers_rejected: int = 0
    completed: int = 0
    errors: list[str] = field(default_factory=list)

    # Đo độ trễ broadcast question.start (NF-3 SLA: p95 < 500 ms)
    broadcast_delays_ms: list[float] = field(default_factory=list)

    # Đo RTT answer.ack từ góc nhìn client (NF-2)
    answer_rtts_ms: list[float] = field(default_factory=list)

    # Đo thời gian reconnect (NF-5 SLA: < 3.0s)
    reconnect_times_s: list[float] = field(default_factory=list)

    @staticmethod
    def calc_percentile(data: list[float], p: float) -> float:
        """Tính phân vị p (0-100) bằng nội suy tuyến tính."""
        if not data:
            return 0.0
        s = sorted(data)
        k = (len(s) - 1) * (p / 100.0)
        f = int(k)
        c = f + 1
        if c < len(s):
            return s[f] + (k - f) * (s[c] - s[f])
        return s[f]

    def summary(self) -> dict[str, Any]:
        """Tổng hợp toàn bộ chỉ số đo lường hiệu năng."""
        return {
            "total_bots": self.total_bots,
            "joined": self.joined,
            "connected": self.connected,
            "answers_submitted": self.answers_submitted,
            "answers_accepted": self.answers_accepted,
            "answers_rejected": self.answers_rejected,
            "completed": self.completed,
            "error_count": len(self.errors),
            "broadcast_delay_ms": {
                "count": len(self.broadcast_delays_ms),
                "min": min(self.broadcast_delays_ms) if self.broadcast_delays_ms else 0.0,
                "avg": sum(self.broadcast_delays_ms) / len(self.broadcast_delays_ms)
                if self.broadcast_delays_ms
                else 0.0,
                "p50": self.calc_percentile(self.broadcast_delays_ms, 50),
                "p90": self.calc_percentile(self.broadcast_delays_ms, 90),
                "p95": self.calc_percentile(self.broadcast_delays_ms, 95),
                "p99": self.calc_percentile(self.broadcast_delays_ms, 99),
                "max": max(self.broadcast_delays_ms) if self.broadcast_delays_ms else 0.0,
            },
            "answer_rtt_ms": {
                "count": len(self.answer_rtts_ms),
                "min": min(self.answer_rtts_ms) if self.answer_rtts_ms else 0.0,
                "avg": sum(self.answer_rtts_ms) / len(self.answer_rtts_ms)
                if self.answer_rtts_ms
                else 0.0,
                "p50": self.calc_percentile(self.answer_rtts_ms, 50),
                "p90": self.calc_percentile(self.answer_rtts_ms, 90),
                "p95": self.calc_percentile(self.answer_rtts_ms, 95),
                "p99": self.calc_percentile(self.answer_rtts_ms, 99),
                "max": max(self.answer_rtts_ms) if self.answer_rtts_ms else 0.0,
            },
            "reconnect_time_s": {
                "count": len(self.reconnect_times_s),
                "p95": self.calc_percentile(self.reconnect_times_s, 95),
                "max": max(self.reconnect_times_s) if self.reconnect_times_s else 0.0,
            },
        }


class SingleBot:
    """Đại diện cho một bot độc lập tham gia phòng thi."""

    def __init__(self, index: int, config: BotConfig, stats: BotStats) -> None:
        self.index = index
        self.config = config
        self.stats = stats
        self.nickname = f"{config.prefix}-{index:02d}"

        self.token: str | None = None
        self.game_id: str | None = None
        self.player_id: str | None = None
        self.ticket: str | None = None

        self.ws: WSClient | None = None
        self.is_running: bool = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._answered_questions: set[int] = set()
        self._submit_t0: dict[int, float] = {}

    async def run(self, http_client: httpx.AsyncClient) -> None:
        """Thực thi toàn bộ vòng đời của một bot."""
        try:
            # 1. Tham gia phòng chơi qua REST API
            joined = await self._join_game(http_client)
            if not joined:
                return

            self.stats.joined += 1

            # 2. Lấy WS ticket
            ticket_ok = await self._get_ws_ticket(http_client)
            if not ticket_ok:
                return

            # 3. Kết nối WebSocket
            await self._connect_ws()

        except asyncio.CancelledError:
            log.debug("Bot %s bị hủy", self.nickname)
        except Exception as exc:  # noqa: BLE001
            err_msg = f"Bot {self.nickname} gặp lỗi: {exc}"
            log.warning(err_msg)
            self.stats.errors.append(err_msg)
        finally:
            await self.close()

    async def _join_game(self, http_client: httpx.AsyncClient) -> bool:
        """Gửi yêu cầu tham gia phòng thi (POST /api/v1/games/join)."""
        join_url = f"{self.config.base_url.rstrip('/')}/api/v1/games/join"
        payload = {"pin": self.config.pin, "nickname": self.nickname}

        resp = await http_client.post(join_url, json=payload, timeout=10.0)
        if resp.status_code != 200:
            err = f"Bot {self.nickname} join thất bại ({resp.status_code}): {resp.text}"
            log.warning(err)
            self.stats.errors.append(err)
            return False

        data = resp.json()
        self.token = data.get("token")
        self.game_id = data.get("game_id")
        self.player_id = data.get("player_id")
        return bool(self.token and self.game_id)

    async def _get_ws_ticket(self, http_client: httpx.AsyncClient) -> bool:
        """Đổi Guest token lấy WS Ticket dùng 1 lần (POST /api/v1/realtime/ws-ticket)."""
        ticket_url = f"{self.config.base_url.rstrip('/')}/api/v1/realtime/ws-ticket"
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = {"game_id": self.game_id}

        resp = await http_client.post(ticket_url, headers=headers, json=payload, timeout=10.0)
        if resp.status_code != 200:
            err = f"Bot {self.nickname} lấy ticket thất bại ({resp.status_code}): {resp.text}"
            log.warning(err)
            self.stats.errors.append(err)
            return False

        data = resp.json()
        self.ticket = data.get("ticket")
        return bool(self.ticket)

    def _resolve_ws_url(self) -> str:
        """Xác định URL WebSocket từ cấu hình."""
        if self.config.ws_url:
            base = self.config.ws_url.rstrip("/")
            if "?" in base:
                return f"{base}&ticket={self.ticket}"
            return f"{base}?ticket={self.ticket}"

        # Tự động suy ra từ base_url
        parsed = urlparse(self.config.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc
        return f"{scheme}://{netloc}/ws/game?ticket={self.ticket}"

    async def _connect_ws(self) -> None:
        """Kết nối WebSocket và lắng nghe thông điệp."""
        ws_url = self._resolve_ws_url()
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
            self.ws = ws
            self.is_running = True
            self.stats.connected += 1
            log.info("Bot %s đã kết nối WebSocket thành công", self.nickname)

            # Khởi chạy heartbeat nền (15s/lần)
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            async for raw_msg in ws:
                if not self.is_running:
                    break
                try:
                    envelope = json.loads(raw_msg)
                    await self._handle_ws_message(envelope)
                except Exception as exc:  # noqa: BLE001
                    log.debug("Bot %s parse message lỗi: %s", self.nickname, exc)

    async def _heartbeat_loop(self) -> None:
        """Gửi heartbeat định kỳ 15 giây (FR-GAME-18)."""
        try:
            while self.is_running and self.ws:
                await asyncio.sleep(15.0)
                hb_envelope = {"type": "heartbeat", "v": 1, "data": {}}
                await self.ws.send(json.dumps(hb_envelope))
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("Bot %s heartbeat dừng: %s", self.nickname, exc)

    async def _handle_ws_message(self, envelope: dict[str, Any]) -> None:
        """Xử lý từng loại thông điệp nhận được từ server."""
        msg_type = envelope.get("type")
        data = envelope.get("data") or {}

        if msg_type == "question.start":
            # Đo NF-3: thời gian từ lúc server phát đến lúc client nhận (server_time epoch ms)
            server_time = data.get("server_time")
            if server_time is not None:
                now_ms = time.time() * 1000.0
                delay_ms = max(0.0, now_ms - float(server_time))
                self.stats.broadcast_delays_ms.append(delay_ms)
            await self._handle_question_start(data)

        elif msg_type == "answer.ack":
            # Đo NF-2: Round-Trip Time từ lúc nộp đến lúc nhận ack
            q_idx = data.get("q_index")
            t0 = self._submit_t0.pop(q_idx, None) if q_idx is not None else None
            if t0 is not None:
                rtt_ms = (time.perf_counter() - t0) * 1000.0
                self.stats.answer_rtts_ms.append(rtt_ms)

            if data.get("accepted"):
                self.stats.answers_accepted += 1
            else:
                self.stats.answers_rejected += 1

        elif msg_type == "server.draining":
            log.info("Bot %s nhận tín hiệu server.draining", self.nickname)
            # Tự động đóng để chuẩn bị reconnect (nếu có logic reconnect)
            retry_after = data.get("retry_after_ms", 300) / 1000.0
            await asyncio.sleep(retry_after)

        elif msg_type == "game.finished":
            log.info("Bot %s nhận tín hiệu trận đấu kết thúc", self.nickname)
            self.stats.completed += 1
            self.is_running = False
            if self.ws:
                await self.ws.close()

        elif msg_type == "player.kicked":
            if data.get("player_id") == self.player_id:
                log.warning("Bot %s đã bị Host kick khỏi trận đấu", self.nickname)
                self.is_running = False
                if self.ws:
                    await self.ws.close()

    async def _handle_question_start(self, data: dict[str, Any]) -> None:
        """Xử lý khi có câu hỏi mới: chọn ngẫu nhiên option và nộp bài kèm jitter (FR-GAME-07)."""
        q_index = data.get("q_index")
        if q_index is None or q_index in self._answered_questions:
            return

        options = data.get("options") or []
        if not options:
            return

        self._answered_questions.add(q_index)

        # Tính thời gian jitter ngẫu nhiên
        jitter = random.uniform(self.config.jitter_min, self.config.jitter_max)
        await asyncio.sleep(jitter)

        if not self.is_running or not self.ws:
            return

        # Chọn ngẫu nhiên 1 phương án
        chosen_opt = random.choice(options)
        chosen_id = str(chosen_opt.get("id"))

        submit_envelope = {
            "type": "answer.submit",
            "v": 1,
            "data": {
                "q_index": q_index,
                "selected_option_ids": [chosen_id],
                "client_sent_epoch_ms": int(time.time() * 1000),
            },
        }

        self._submit_t0[q_index] = time.perf_counter()
        await self.ws.send(json.dumps(submit_envelope))
        self.stats.answers_submitted += 1
        log.debug("Bot %s đã nộp câu %s (option %s)", self.nickname, q_index, chosen_id)

    async def close(self) -> None:
        """Đóng kết nối bot an toàn."""
        self.is_running = False
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass


class BotManager:
    """Quản lý bầy bot đồng thời."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.stats = BotStats(total_bots=config.count)
        self.bots: list[SingleBot] = [
            SingleBot(i + 1, self.config, self.stats) for i in range(config.count)
        ]
        self._is_stopped: bool = False

    async def start(self) -> BotStats:
        """Khởi chạy toàn bộ bầy bot."""
        log.info(
            "Bắt đầu khởi chạy %d bots tham gia PIN %s (Host: %s)...",
            self.config.count,
            self.config.pin,
            self.config.base_url,
        )

        limits = httpx.Limits(max_connections=self.config.count + 10, max_keepalive_connections=20)
        async with httpx.AsyncClient(limits=limits) as http_client:
            tasks: list[asyncio.Task[None]] = []
            for bot in self.bots:
                if self._is_stopped:
                    break
                t = asyncio.create_task(bot.run(http_client))
                tasks.append(t)
                if self.config.join_stagger_s > 0:
                    await asyncio.sleep(self.config.join_stagger_s)

            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                log.info("BotManager nhận yêu cầu dừng...")
            finally:
                await self.stop()

        return self.stats

    async def stop(self) -> None:
        """Dừng tất cả các bot đang chạy."""
        self._is_stopped = True
        await asyncio.gather(*(bot.close() for bot in self.bots), return_exceptions=True)
