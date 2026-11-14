"""ConnectionManager quản lý kết nối WebSocket và cầu nối Redis Pub/Sub (FR-GAME-03..22, AU-11).

Tính năng chính:
- Lưu trữ kết nối theo game_id -> client_id -> ClientConnection.
- Quản lý giới hạn tốc độ 5 msg/s mỗi kết nối bằng thuật toán Token Bucket (FR-GAME-21).
- Cầu nối Pub/Sub phân tán: Lắng nghe kênh `game:{id}` và fan-out cho các kết nối cục bộ.
- Serialize JSON 1 lần duy nhất cho toàn bộ kết nối.
- Timeout an toàn (1.5s) để client mạng chậm không làm nghẽn event loop.
- Hỗ trợ kick người chơi trên toàn bộ replica qua Pub/Sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from app.core.metrics import GAME_BROADCAST_DURATION_SECONDS

log = logging.getLogger("app.realtime.connection_manager")


class TokenBucketRateLimiter:
    """Bộ giới hạn tốc độ theo thuật toán Token Bucket (FR-GAME-21: tối đa 5 msg/s)."""

    def __init__(self, capacity: float = 5.0, refill_rate: float = 5.0) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.monotonic()

    def allow(self, cost: float = 1.0) -> bool:
        """Kiểm tra và trừ token nếu đủ quota."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


@dataclass
class ClientConnection:
    """Thông tin định danh và kết nối WebSocket của một client."""

    ws: WebSocket
    client_id: str
    nickname: str
    is_host: bool
    game_id: str
    rate_limiter: TokenBucketRateLimiter = field(default_factory=TokenBucketRateLimiter)


class ConnectionManager:
    """Quản lý kết nối WebSocket tập trung và cầu nối Pub/Sub phân tán."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        # Mapping: game_id -> {client_id: ClientConnection}
        self.active_connections: dict[str, dict[str, ClientConnection]] = {}
        # Mapping: game_id -> asyncio.Task (PubSub listener)
        self.pubsub_tasks: dict[str, asyncio.Task[None]] = {}
        # Mapping: game_id -> PubSub object
        self.pubsubs: dict[str, PubSub] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        ws: WebSocket,
        *,
        game_id: str,
        client_id: str,
        nickname: str,
        is_host: bool,
    ) -> ClientConnection:
        """Đăng ký kết nối WebSocket mới và kích hoạt Pub/Sub nếu cần."""
        conn = ClientConnection(
            ws=ws,
            client_id=client_id,
            nickname=nickname,
            is_host=is_host,
            game_id=game_id,
        )

        async with self._lock:
            if game_id not in self.active_connections:
                self.active_connections[game_id] = {}

            # Nếu client này đã có kết nối cũ trên cùng replica -> đóng kết nối cũ
            old_conn = self.active_connections[game_id].get(client_id)
            if old_conn:
                try:
                    await old_conn.ws.close(code=1000, reason="Reconnected")
                except Exception:
                    pass

            self.active_connections[game_id][client_id] = conn

            # Khởi chạy PubSub listener cho game_id nếu chưa có
            if game_id not in self.pubsub_tasks:
                pubsub = self.redis.pubsub()
                self.pubsubs[game_id] = pubsub
                task = asyncio.create_task(
                    self._pubsub_listener(game_id, pubsub),
                    name=f"pubsub-listener-{game_id}",
                )
                self.pubsub_tasks[game_id] = task
                log.info("Kích hoạt Pub/Sub listener cho game %s", game_id)

            try:
                await self.redis.incr("realtime:active_connections")
            except Exception as e:
                log.warning("Không thể tăng realtime:active_connections: %s", e)

        return conn

    async def disconnect(self, game_id: str, client_id: str) -> None:
        """Hủy đăng ký kết nối và dọn dẹp task Pub/Sub nếu không còn kết nối nào."""
        task_to_cancel: asyncio.Task[None] | None = None
        pubsub_to_close: PubSub | None = None

        async with self._lock:
            if game_id in self.active_connections:
                self.active_connections[game_id].pop(client_id, None)
                if not self.active_connections[game_id]:
                    # Không còn ai trong phòng trên replica này -> dọn dẹp
                    self.active_connections.pop(game_id, None)
                    task_to_cancel = self.pubsub_tasks.pop(game_id, None)
                    pubsub_to_close = self.pubsubs.pop(game_id, None)

            try:
                cnt = await self.redis.decr("realtime:active_connections")
                if cnt < 0:
                    await self.redis.set("realtime:active_connections", 0)
            except Exception as e:
                log.warning("Không thể giảm realtime:active_connections: %s", e)

        if task_to_cancel:
            task_to_cancel.cancel()
            try:
                await task_to_cancel
            except asyncio.CancelledError:
                pass
            log.info("Đã hủy Pub/Sub listener cho game %s", game_id)

        if pubsub_to_close:
            try:
                await pubsub_to_close.unsubscribe(f"game:{game_id}")
                await pubsub_to_close.aclose()
            except Exception as exc:  # noqa: BLE001
                log.debug("Lỗi khi đóng PubSub %s: %s", game_id, exc)

    async def _pubsub_listener(self, game_id: str, pubsub: PubSub) -> None:
        """Task nền lắng nghe kênh Redis Pub/Sub và fan-out cho các kết nối cục bộ."""
        channel_name = f"game:{game_id}"
        try:
            await pubsub.subscribe(channel_name)
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue

                raw_data: str = msg.get("data", "")
                if not raw_data:
                    continue

                # Parse thử xem có phải lệnh nội bộ (ví dụ: kick player)
                try:
                    parsed = json.loads(raw_data)
                    msg_type = parsed.get("type")
                except Exception:
                    parsed = {}
                    msg_type = None

                # Xử lý thông điệp kick người chơi
                if msg_type == "internal.kick":
                    target_pid = parsed.get("data", {}).get("player_id")
                    if target_pid:
                        await self._kick_local_player(game_id, target_pid)
                    continue

                # Cá nhân hóa thông điệp question.result và game.finished cho từng kết nối
                if msg_type in ("question.result", "game.finished"):
                    await self.broadcast_personalized(game_id, parsed)
                    continue

                # Fan-out cho toàn bộ client cục bộ của game
                await self.broadcast_local(game_id, raw_data, msg_type=msg_type or "unknown")

        except asyncio.CancelledError:
            log.debug("Pub/Sub listener cho game %s đã nhận tín hiệu dừng", game_id)
            raise
        except Exception as exc:
            log.exception("Lỗi không mong muốn trong Pub/Sub listener %s: %s", game_id, exc)

    async def _kick_local_player(self, game_id: str, player_id: str) -> None:
        """Đóng kết nối WebSocket của người bị kick trên replica này với mã 4002 (KICKED)."""
        async with self._lock:
            game_conns = self.active_connections.get(game_id, {})
            conn = game_conns.pop(player_id, None)

        if conn:
            try:
                await conn.ws.close(code=4002, reason="KICKED")
                log.info(
                    "Đã đóng kết nối của player %s trong game %s do bị kick", player_id, game_id
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("Lỗi khi đóng WebSocket của player bị kick %s: %s", player_id, exc)

    async def broadcast_local(
        self, game_id: str, serialized_json: str, msg_type: str = "unknown"
    ) -> None:
        """Phát thông điệp đã serialize sẵn tới toàn bộ kết nối cục bộ của game.

        Sử dụng asyncio.wait_for với timeout 1.5s để bảo vệ vòng lặp,
        kết nối mạng chậm hoặc treo không thể làm nghẽn các kết nối khác.
        """
        async with self._lock:
            conns = list(self.active_connections.get(game_id, {}).values())

        if not conns:
            return

        broadcast_start_t = time.perf_counter()

        async def _safe_send(conn: ClientConnection) -> None:
            try:
                await asyncio.wait_for(conn.ws.send_text(serialized_json), timeout=1.5)
            except TimeoutError:
                log.warning(
                    "Timeout 1.5s khi gửi cho client %s (game %s), client phản hồi quá chậm",
                    conn.client_id,
                    game_id,
                )
            except WebSocketDisconnect:
                # Client đã ngắt kết nối
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("Lỗi khi gửi thông điệp cho client %s: %s", conn.client_id, exc)

        # Chạy đồng thời cho toàn bộ kết nối cục bộ
        await asyncio.gather(*(_safe_send(c) for c in conns), return_exceptions=True)

        # Đo thời gian broadcast cục bộ (NF-3: p95 < 500ms)
        elapsed = time.perf_counter() - broadcast_start_t
        GAME_BROADCAST_DURATION_SECONDS.labels(msg_type=msg_type).observe(elapsed)

    async def broadcast_personalized(self, game_id: str, envelope: dict[str, Any]) -> None:
        """Phát thông điệp cá nhân hóa (question.result, game.finished) cho từng kết nối.

        Mỗi replica tự pipeline ZSCORE/ZREVRANK/HGET cho các kết nối của mình (FR-GAME-16).
        """
        async with self._lock:
            conns = list(self.active_connections.get(game_id, {}).values())

        if not conns:
            return

        msg_type = envelope.get("type")
        base_data = envelope.get("data", {})
        seq = envelope.get("seq")
        v = envelope.get("v", 1)

        players = [c for c in conns if not c.is_host]
        hosts = [c for c in conns if c.is_host]

        async def _safe_send_to(conn: ClientConnection, text: str) -> None:
            try:
                await asyncio.wait_for(conn.ws.send_text(text), timeout=1.5)
            except TimeoutError:
                log.warning("Timeout 1.5s khi gửi cho client %s (game %s)", conn.client_id, game_id)
            except WebSocketDisconnect:
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("Lỗi khi gửi thông điệp cho client %s: %s", conn.client_id, exc)

        tasks: list[asyncio.Task[None]] = []

        # 1. Cá nhân hóa cho người chơi trong question.result
        if players and msg_type == "question.result":
            q_index = base_data.get("q_index", 0)
            pipe = self.redis.pipeline()
            for p in players:
                pipe.hget(f"game:{game_id}:q:{q_index}:pts", p.client_id)
                pipe.zscore(f"game:{game_id}:lb", p.client_id)
                pipe.zrevrank(f"game:{game_id}:lb", p.client_id)
                pipe.hget(f"game:{game_id}:streaks", p.client_id)
            pipe_results = await pipe.execute()

            for idx, p in enumerate(players):
                pts = pipe_results[idx * 4]
                score = pipe_results[idx * 4 + 1]
                rank = pipe_results[idx * 4 + 2]
                streak = pipe_results[idx * 4 + 3]

                p_data = dict(base_data)
                p_data["player_points_awarded"] = int(pts) if pts is not None else 0
                p_data["player_total_score"] = int(score) if score is not None else 0
                p_data["player_rank"] = (rank + 1) if rank is not None else None
                p_data["player_streak"] = int(streak) if streak is not None else 0

                p_env = {"type": msg_type, "v": v, "seq": seq, "data": p_data}
                tasks.append(asyncio.create_task(_safe_send_to(p, json.dumps(p_env))))

        # 2. Cá nhân hóa cho người chơi trong game.finished
        elif players and msg_type == "game.finished":
            pipe = self.redis.pipeline()
            for p in players:
                pipe.zscore(f"game:{game_id}:lb", p.client_id)
                pipe.zrevrank(f"game:{game_id}:lb", p.client_id)
            pipe_results = await pipe.execute()

            for idx, p in enumerate(players):
                score = pipe_results[idx * 2]
                rank = pipe_results[idx * 2 + 1]

                p_data = dict(base_data)
                p_data["final_score"] = int(score) if score is not None else 0
                p_data["final_rank"] = (rank + 1) if rank is not None else None

                p_env = {"type": msg_type, "v": v, "seq": seq, "data": p_data}
                tasks.append(asyncio.create_task(_safe_send_to(p, json.dumps(p_env))))

        # 3. Host nhận bản tin gốc (không chứa trường cá nhân hóa player_*)
        raw_base = json.dumps(envelope)
        for h in hosts:
            tasks.append(asyncio.create_task(_safe_send_to(h, raw_base)))

        start_t = time.perf_counter()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.perf_counter() - start_t
            GAME_BROADCAST_DURATION_SECONDS.labels(msg_type=msg_type or "personalized").observe(
                elapsed
            )

    async def broadcast_game(self, game_id: str, envelope: dict[str, Any]) -> None:
        """Gán seq tự tăng và phát thông điệp lên kênh Redis Pub/Sub của game."""
        # Seq tự tăng trên Redis đảm bảo tính đơn điệu tăng dần trên toàn cụm (GM-17)
        seq = await self.redis.incr(f"game:{game_id}:seq")
        envelope["seq"] = seq

        serialized = json.dumps(envelope)
        await self.redis.publish(f"game:{game_id}", serialized)

    async def kick_player_cluster(self, game_id: str, player_id: str) -> None:
        """Phát tín hiệu kick người chơi lên toàn cụm Redis Pub/Sub."""
        msg = {
            "type": "internal.kick",
            "v": 1,
            "data": {
                "player_id": player_id,
                "game_id": game_id,
            },
        }
        await self.redis.publish(f"game:{game_id}", json.dumps(msg))

    async def send_personal(self, ws: WebSocket, envelope: dict[str, Any]) -> None:
        """Gửi thông điệp trực tiếp cho một kết nối WebSocket."""
        serialized = json.dumps(envelope)
        await ws.send_text(serialized)

    async def close_all(self) -> None:
        """Dọn dẹp toàn bộ kết nối và task nền khi server shutdown."""
        async with self._lock:
            for task in self.pubsub_tasks.values():
                task.cancel()
            self.pubsub_tasks.clear()

            for pubsub in self.pubsubs.values():
                try:
                    await pubsub.aclose()
                except Exception:
                    pass
            self.pubsubs.clear()

            for game_conns in self.active_connections.values():
                for conn in game_conns.values():
                    try:
                        await conn.ws.close(code=1001, reason="Server shutting down")
                    except Exception:
                        pass
            self.active_connections.clear()
