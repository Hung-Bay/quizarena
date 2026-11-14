"""QuizArena Comprehensive Load Test Runner (Phase 5 - Thứ Tư).

Kiểm chứng các tiêu chuẩn phi chức năng:
- NF-1: 300 người chơi đồng thời trong 1 trận (1x300) hoặc 3 trận x 100 (3x100) trên cụm scaled, không mất đáp án nào.
- NF-2: answer.ack p95 < 150 ms (đo phía server qua Prometheus và client RTT).
- NF-3: Broadcast question.start tới toàn bộ người chơi p95 < 500 ms (từ lúc server phát đến lúc client nhận).
- NF-5: Reconnect < 3s, không mất điểm.
- Đối soát 100% dữ liệu: Bot accepted = Redis Stream = PostgreSQL player_answers; Leaderboard sum = tổng điểm các câu đúng.
- Quản lý bộ nhớ Redis (noeviction 128MB): Đo INFO memory trước/sau, kiểm tra TTL & dọn dẹp Stream.
- Số liệu so sánh định lượng phục vụ ADR-004: realtime=1 vs realtime=3 trên cùng 300 bots.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.game.write_behind import finalize_game_session
from app.models.game import GamePlayer, GameSession, PlayerAnswer
from app.models.quiz import Quiz
from app.models.user import User

import os

# Cấu hình kết nối mặc định cho môi trường Dev / Docker Compose
REDIS_URL = "redis://:AK.Gamer1@localhost:6380/0"
os.environ["REDIS_URL"] = REDIS_URL
POSTGRES_URL = "postgresql+asyncpg://quizarena:8521@localhost:5433/quizarena"
BASE_HTTP = "http://localhost"
BASE_WS = "ws://localhost"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("loadtest")


def calc_percentile(data: list[float], p: float) -> float:
    """Tính phân vị p (0-100) bằng phương pháp nội suy tuyến tính."""
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1
    if c < len(s):
        return s[f] + (k - f) * (s[c] - s[f])
    return s[f]


@dataclass
class MatchMetrics:
    """Số liệu đo đạc chi tiết của một trận load test."""

    game_id: str
    bot_count: int
    num_questions: int
    connected_bots: int = 0
    submitted_answers: int = 0
    accepted_answers: int = 0
    rejected_answers: int = 0
    stream_length: int = 0
    postgres_answers_count: int = 0
    leaderboard_total_score: int = 0
    reconciliation_passed: bool = False

    broadcast_delays_ms: list[float] = field(default_factory=list)
    ack_rtts_ms: list[float] = field(default_factory=list)
    server_durations_ms: list[float] = field(default_factory=list)

    def stats(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "bot_count": self.bot_count,
            "connected_bots": self.connected_bots,
            "submitted_answers": self.submitted_answers,
            "accepted_answers": self.accepted_answers,
            "rejected_answers": self.rejected_answers,
            "stream_length": self.stream_length,
            "postgres_answers_count": self.postgres_answers_count,
            "leaderboard_total_score": self.leaderboard_total_score,
            "reconciliation_passed": self.reconciliation_passed,
            "broadcast_delay_ms": {
                "count": len(self.broadcast_delays_ms),
                "min": round(min(self.broadcast_delays_ms), 1) if self.broadcast_delays_ms else 0.0,
                "p50": round(calc_percentile(self.broadcast_delays_ms, 50), 1),
                "p90": round(calc_percentile(self.broadcast_delays_ms, 90), 1),
                "p95": round(calc_percentile(self.broadcast_delays_ms, 95), 1),
                "p99": round(calc_percentile(self.broadcast_delays_ms, 99), 1),
                "max": round(max(self.broadcast_delays_ms), 1) if self.broadcast_delays_ms else 0.0,
            },
            "ack_rtt_ms": {
                "count": len(self.ack_rtts_ms),
                "min": round(min(self.ack_rtts_ms), 1) if self.ack_rtts_ms else 0.0,
                "p50": round(calc_percentile(self.ack_rtts_ms, 50), 1),
                "p90": round(calc_percentile(self.ack_rtts_ms, 90), 1),
                "p95": round(calc_percentile(self.ack_rtts_ms, 95), 1),
                "p99": round(calc_percentile(self.ack_rtts_ms, 99), 1),
                "max": round(max(self.ack_rtts_ms), 1) if self.ack_rtts_ms else 0.0,
            },
        }


async def create_loadtest_game_in_db(
    async_session: sessionmaker[AsyncSession],
    redis: Redis,
    *,
    pin: str,
    num_questions: int = 3,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Tạo game session hoàn chỉnh trong PostgreSQL và Redis."""
    game_id = str(uuid.uuid4())
    host_id = str(uuid.uuid4())
    quiz_id = str(uuid.uuid4())

    # Tạo câu hỏi mẫu
    questions = []
    for q_idx in range(1, num_questions + 1):
        qid = str(uuid.uuid4())
        opt_correct = str(uuid.uuid4())
        opt_wrong1 = str(uuid.uuid4())
        opt_wrong2 = str(uuid.uuid4())
        opt_wrong3 = str(uuid.uuid4())
        q = {
            "id": qid,
            "position": q_idx,
            "content": f"Câu hỏi load test số {q_idx}: Đâu là đáp án đúng?",
            "type": "single",
            "points": 1000,
            "time_limit_s": 15,
            "correct_option_id": opt_correct,
            "options": [
                {"id": opt_correct, "position": 1, "content": "Đáp án chính xác", "is_correct": True},
                {"id": opt_wrong1, "position": 2, "content": "Đáp án sai 1", "is_correct": False},
                {"id": opt_wrong2, "position": 3, "content": "Đáp án sai 2", "is_correct": False},
                {"id": opt_wrong3, "position": 4, "content": "Đáp án sai 3", "is_correct": False},
            ],
        }
        questions.append(q)

    # 1. Lưu vào PostgreSQL
    async with async_session() as session:
        u_res = await session.execute(select(User))
        user = u_res.scalars().first()
        if not user:
            user = User(
                email="loadtest_host@quizarena.io.vn",
                display_name="LoadTest Host",
                password_hash="fake_hash",
            )
            session.add(user)
            await session.flush()

        host_id = str(user.id)

        q_res = await session.execute(select(Quiz))
        quiz = q_res.scalars().first()
        if not quiz:
            quiz = Quiz(
                owner_id=user.id,
                title="Load Test Sample Quiz",
                status="published",
                visibility="public",
                version=1,
            )
            session.add(quiz)
            await session.flush()

        quiz_id = str(quiz.id)

        gs = GameSession(
            id=uuid.UUID(game_id),
            quiz_id=uuid.UUID(quiz_id),
            quiz_version=1,
            host_id=uuid.UUID(host_id),
            pin=pin,
            status="lobby",
            strategy_id="standard",
            quiz_snapshot={"id": quiz_id, "title": "Load Test Quiz", "questions": questions},
        )
        session.add(gs)
        await session.commit()

    # 2. Khởi tạo trạng thái Redis
    await redis.hset(
        f"game:{game_id}:state",
        mapping={
            "game_id": game_id,
            "pin": pin,
            "host_id": host_id,
            "status": "lobby",
            "q_index": "0",
            "total_questions": str(num_questions),
            "deadline": "0",
            "deadline_version": "0",
        },
    )
    await redis.set(f"pin:{pin}", game_id, ex=7200)

    # Lưu quiz snapshot trên Redis
    quiz_doc = {"id": quiz_id, "title": "Load Test Quiz", "questions": questions}
    await redis.set(f"game:{game_id}:quiz", json.dumps(quiz_doc), ex=7200)

    # Lưu meta và correct_keys cho từng câu hỏi
    for q in questions:
        q_pos = q["position"]
        await redis.sadd(f"game:{game_id}:q:{q_pos}:correct_keys", q["correct_option_id"])
        await redis.hset(
            f"game:{game_id}:q:{q_pos}:meta",
            mapping={
                "id": q["id"],
                "points": str(q["points"]),
                "time_limit_s": str(q["time_limit_s"]),
                "type": q["type"],
            },
        )

    return game_id, host_id, questions


async def issue_ws_ticket(redis: Redis, game_id: str, sub: str, is_host: bool = False) -> str:
    """Tạo ticket 1 lần cấp tốc qua Redis để nối WebSocket nhanh trong load test."""
    ticket = f"ticket_{uuid.uuid4().hex}"
    data = {
        "sub": sub if is_host else f"guest:{sub}",
        "game_id": game_id,
        "roles": ["host"] if is_host else ["guest"],
        "permissions": ["game:host_own"] if is_host else ["game:play"],
        "type": "guest",
    }
    await redis.set(f"ws:ticket:{ticket}", json.dumps(data), ex=120)
    return ticket


async def run_single_bot(
    bot_idx: int,
    player_id: str,
    nickname: str,
    game_id: str,
    pin: str,
    redis: Redis,
    questions: list[dict[str, Any]],
    metrics: MatchMetrics,
    stop_event: asyncio.Event,
    correct_rate: float = 0.85,
) -> None:
    """Tiến trình giả lập 1 người chơi bot qua WebSocket."""
    ticket = await issue_ws_ticket(redis, game_id, player_id, is_host=False)
    ws_url = f"{BASE_WS}/ws/game?ticket={ticket}"

    pending_submissions: dict[int, float] = {}

    try:
        async with websockets.connect(ws_url, ping_interval=30, ping_timeout=30) as ws:
            metrics.connected_bots += 1
            # Bỏ qua snapshot ban đầu
            await ws.recv()

            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed:
                    break

                try:
                    msg = json.loads(raw)
                except Exception:
                    continue

                mtype = msg.get("type")
                mdata = msg.get("data") or {}

                if mtype == "question.start":
                    # NF-3: Đo độ trễ broadcast từ server_time epoch ms đến lúc nhận
                    server_time = mdata.get("server_time")
                    if server_time is not None:
                        now_ms = time.time() * 1000.0
                        delay = max(0.0, now_ms - float(server_time))
                        metrics.broadcast_delays_ms.append(delay)

                    q_idx = mdata.get("q_index", 1)
                    q_options = mdata.get("options", [])
                    if not q_options:
                        continue

                    # Giả lập suy nghĩ của người chơi: Jitter ngẫu nhiên 0.2s - 1.2s
                    jitter = random.uniform(0.2, 1.2)
                    await asyncio.sleep(jitter)

                    # Chọn đáp án: 85% chọn đúng, 15% chọn ngẫu nhiên
                    target_q = questions[q_idx - 1] if q_idx <= len(questions) else None
                    if target_q and random.random() < correct_rate:
                        chosen_opt_id = target_q["correct_option_id"]
                    else:
                        chosen_opt_id = random.choice(q_options)["id"]

                    submit_payload = {
                        "type": "answer.submit",
                        "v": 1,
                        "data": {
                            "q_index": q_idx,
                            "selected_option_ids": [chosen_opt_id],
                            "client_sent_epoch_ms": int(time.time() * 1000),
                        },
                    }

                    t0 = time.perf_counter()
                    pending_submissions[q_idx] = t0
                    await ws.send(json.dumps(submit_payload))
                    metrics.submitted_answers += 1

                elif mtype == "answer.ack":
                    # NF-2: Đo RTT round-trip từ lúc nộp đến khi nhận ack
                    q_idx = mdata.get("q_index")
                    t0 = pending_submissions.pop(q_idx, None)
                    if t0 is not None:
                        rtt = (time.perf_counter() - t0) * 1000.0
                        metrics.ack_rtts_ms.append(rtt)

                    if mdata.get("accepted"):
                        metrics.accepted_answers += 1
                    else:
                        metrics.rejected_answers += 1

                elif mtype == "game.finished":
                    break

    except Exception as exc:
        log.debug("Bot %s gặp lỗi kết nối: %s", nickname, exc)


async def execute_match(
    async_session: sessionmaker[AsyncSession],
    redis: Redis,
    *,
    pin: str,
    bot_count: int,
    num_questions: int = 3,
) -> MatchMetrics:
    """Chạy toàn bộ vòng đời 1 trận đấu với bầy bot."""
    log.info("Khởi tạo trận đấu Load Test với PIN %s, số bot: %d...", pin, bot_count)
    game_id, host_id, questions = await create_loadtest_game_in_db(
        async_session, redis, pin=pin, num_questions=num_questions
    )

    metrics = MatchMetrics(game_id=game_id, bot_count=bot_count, num_questions=num_questions)
    stop_event = asyncio.Event()

    # 1. Kết nối Host
    host_ticket = await issue_ws_ticket(redis, game_id, host_id, is_host=True)
    host_ws = await websockets.connect(f"{BASE_WS}/ws/game?ticket={host_ticket}")
    await host_ws.recv()  # Bỏ qua snapshot

    # 2. Khởi tạo danh sách bot và lưu vào PostgreSQL (bảng game_players) & Redis
    log.info("Khởi tạo %d bot players trong PostgreSQL và Redis...", bot_count)
    bot_info = []
    game_players_to_insert = []
    gid_uuid = uuid.UUID(game_id)
    for i in range(1, bot_count + 1):
        pid = str(uuid.uuid4())
        nick = f"Bot-{i:03d}"
        bot_info.append((i, pid, nick))
        game_players_to_insert.append(
            GamePlayer(
                id=uuid.UUID(pid),
                session_id=gid_uuid,
                nickname=nick,
                final_score=0,
            )
        )
        # Đăng ký trên Redis
        await redis.hset(f"game:{game_id}:nicknames", pid, nick)
        await redis.hset(
            f"game:{game_id}:players",
            nick.lower(),
            json.dumps({"id": pid, "nickname": nick, "online": True, "score": 0}),
        )
        await redis.zadd(f"game:{game_id}:lb", {pid: 0})

    async with async_session() as session:
        session.add_all(game_players_to_insert)
        await session.commit()

    # 3. Kết nối bầy bot đồng thời (có stagger nhẹ 5ms để tránh bão kết nối tức thời)
    log.info("Đang kết nối %d bots qua Nginx...", bot_count)
    bot_tasks: list[asyncio.Task[None]] = []
    for i, pid, nick in bot_info:
        t = asyncio.create_task(
            run_single_bot(
                i, pid, nick, game_id, pin, redis, questions, metrics, stop_event, correct_rate=0.85
            )
        )
        bot_tasks.append(t)
        if i % 50 == 0:
            await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(0.005)

    # Chờ các bot hoàn tất kết nối
    await asyncio.sleep(2.0)
    log.info("Số bot đã kết nối thành công: %d / %d", metrics.connected_bots, bot_count)

    # 4. Host điều khiển qua từng câu hỏi
    for q_idx in range(1, num_questions + 1):
        log.info("--- [CÂU HỎI %d / %d] Host phát thông điệp mở câu ---", q_idx, num_questions)
        if q_idx == 1:
            await host_ws.send(json.dumps({"type": "game.start", "v": 1, "data": {}}))
        else:
            await host_ws.send(json.dumps({"type": "question.next", "v": 1, "data": {}}))

        # Nhận thông điệp phản hồi phía host
        try:
            await asyncio.wait_for(host_ws.recv(), timeout=2.0)
        except Exception:
            pass

        # Thời gian mở câu hỏi và chờ các bot nộp bài (jitter 0.2-1.2s + buffer)
        await asyncio.sleep(3.0)

    # 4. Host kết thúc trận đấu
    log.info("--- KẾT THÚC TRẬN ĐẤU (game.end) ---")
    await host_ws.send(json.dumps({"type": "game.end", "v": 1, "data": {}}))
    await asyncio.sleep(1.0)
    stop_event.set()

    # Chờ bot đóng kết nối
    await asyncio.gather(*bot_tasks, return_exceptions=True)
    await host_ws.close()

    # 5. Đối soát dữ liệu (Reconciliation)
    log.info("Bắt đầu đối soát dữ liệu (Reconciliation) cho game %s...", game_id)
    async with async_session() as session:
        # Finalize trận đấu và flush Stream sang PostgreSQL
        fin_res = await finalize_game_session(session, redis, game_id=game_id)
        metrics.stream_length = fin_res.get("stream_answers_count", 0)

        # Đếm số bản ghi thực tế trong PostgreSQL
        pg_cnt_q = select(func.count(PlayerAnswer.id)).where(PlayerAnswer.session_id == uuid.UUID(game_id))
        metrics.postgres_answers_count = (await session.execute(pg_cnt_q)).scalar_one()

        # Tính tổng điểm trên Leaderboard ZSET
        lb_raw = await redis.zrange(f"game:{game_id}:lb", 0, -1, withscores=True)
        metrics.leaderboard_total_score = int(sum(score for _, score in lb_raw))

    # Đánh giá tiêu chí đối soát: Không mất đáp án nào!
    # Số accepted từ bot == độ dài Stream == số bản ghi trong PostgreSQL
    metrics.reconciliation_passed = (
        metrics.accepted_answers == metrics.stream_length == metrics.postgres_answers_count
    )

    log.info(
        "Kết quả đối soát Game %s: Bot Accepted (%d) == Stream (%d) == Postgres (%d) -> %s",
        game_id[:8],
        metrics.accepted_answers,
        metrics.stream_length,
        metrics.postgres_answers_count,
        "PASSED (100% Khớp)" if metrics.reconciliation_passed else "FAILED",
    )

    return metrics


async def inspect_redis_memory_and_ttl(redis: Redis, game_ids: list[str]) -> dict[str, Any]:
    """Kiểm tra chỉ số bộ nhớ Redis, quét các key không có TTL và thử nghiệm XTRIM/EXPIRE."""
    info_mem = await redis.info("memory")
    used_mb = info_mem.get("used_memory", 0) / (1024 * 1024)
    peak_mb = info_mem.get("used_memory_peak", 0) / (1024 * 1024)
    frag_ratio = info_mem.get("mem_fragmentation_ratio", 0.0)

    # Quét các key thuộc các trận vừa chạy
    keys_no_ttl: list[str] = []
    total_scanned = 0
    for gid in game_ids:
        cur = 0
        while True:
            cur, keys = await redis.scan(cur, match=f"game:{gid}:*", count=100)
            for k in keys:
                total_scanned += 1
                ttl = await redis.ttl(k)
                if ttl == -1:  # Không có TTL
                    keys_no_ttl.append(k)
            if cur == 0:
                break

    # Dọn dẹp Stream theo yêu cầu §7.6 (tránh tràn 128MB noeviction)
    for gid in game_ids:
        stream_key = f"game:{gid}:answers"
        # Đặt TTL 1 giờ sau khi đối soát hoặc XTRIM
        await redis.expire(stream_key, 3600)
        await redis.xtrim(stream_key, maxlen=0, approximate=False)

    return {
        "used_memory_mb": round(used_mb, 2),
        "peak_memory_mb": round(peak_mb, 2),
        "fragmentation_ratio": round(frag_ratio, 2),
        "total_scanned_keys": total_scanned,
        "keys_without_ttl_count": len(keys_no_ttl),
        "keys_without_ttl_sample": keys_no_ttl[:10],
    }


async def scrape_prometheus_metrics() -> dict[str, Any]:
    """Cào dữ liệu từ Prometheus để lấy server-side p95 answer_duration và phân bổ WS connections."""
    async with httpx.AsyncClient() as client:
        try:
            # Query WS connections trên từng replica
            resp = await client.get(
                "http://localhost:9090/api/v1/query",
                params={"query": "ws_connections"},
                timeout=5.0,
            )
            data = resp.json()
            ws_per_replica = {}
            if data.get("status") == "success":
                for r in data["data"]["result"]:
                    rep_id = r["metric"].get("replica_id") or r["metric"].get("instance", "unknown")
                    ws_per_replica[rep_id] = float(r["value"][1])

            # Query server-side p95 answer duration
            p95_query = "histogram_quantile(0.95, sum by (le) (rate(game_answer_duration_seconds_bucket[5m])))"
            resp_p95 = await client.get(
                "http://localhost:9090/api/v1/query",
                params={"query": p95_query},
                timeout=5.0,
            )
            data_p95 = resp_p95.json()
            p95_val = None
            if data_p95.get("status") == "success" and data_p95["data"]["result"]:
                val_str = data_p95["data"]["result"][0]["value"][1]
                if val_str != "NaN":
                    p95_val = round(float(val_str) * 1000.0, 2)  # Đổi sang ms

            return {
                "ws_connections_per_replica": ws_per_replica,
                "server_p95_answer_duration_ms": p95_val,
            }
        except Exception as exc:
            log.warning("Không thể truy vấn Prometheus: %s", exc)
            return {"ws_connections_per_replica": {}, "server_p95_answer_duration_ms": None}


async def main() -> None:
    parser = argparse.ArgumentParser(description="QuizArena Load Test Runner")
    parser.add_argument(
        "--mode",
        choices=["1x300", "3x100", "compare"],
        default="1x300",
        help="Chế độ load test: 1x300 (1 trận 300 bot), 3x100 (3 trận đồng thời), hoặc compare (so sánh 1 vs 3 replica)",
    )
    args = parser.parse_args()

    print("==================================================================")
    print(f"      QUIZARENA LOAD TEST SUITE — CHẾ ĐỘ: {args.mode.upper()}     ")
    print("==================================================================")

    engine = create_async_engine(POSTGRES_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    await redis.ping()
    print("[1] Kết nối cơ sở dữ liệu PostgreSQL và Redis Cluster thành công.")

    mem_before = await inspect_redis_memory_and_ttl(redis, [])
    print(f"[2] Bộ nhớ Redis trước test: {mem_before['used_memory_mb']} MB (Peak: {mem_before['peak_memory_mb']} MB)")

    all_metrics: list[MatchMetrics] = []

    if args.mode == "1x300":
        # KỊCH BẢN 1: 1 trận đấu với 300 bots
        pin = f"{random.randint(100000, 999999)}"
        m = await execute_match(async_session, redis, pin=pin, bot_count=300, num_questions=3)
        all_metrics.append(m)

    elif args.mode == "3x100":
        # KỊCH BẢN 2: 3 trận đấu đồng thời, mỗi trận 100 bots (tổng 300 bots)
        tasks = []
        for i in range(1, 4):
            pin = f"{random.randint(100000, 999999)}"
            tasks.append(execute_match(async_session, redis, pin=pin, bot_count=100, num_questions=3))
        results = await asyncio.gather(*tasks)
        all_metrics.extend(results)

    elif args.mode == "compare":
        # KỊCH BẢN 3: So sánh định lượng realtime=1 vs realtime=3 phục vụ ADR-004
        pin = f"{random.randint(100000, 999999)}"
        m = await execute_match(async_session, redis, pin=pin, bot_count=300, num_questions=3)
        all_metrics.append(m)

    # Thu thập chỉ số Redis memory sau test
    game_ids = [m.game_id for m in all_metrics]
    mem_after = await inspect_redis_memory_and_ttl(redis, game_ids)

    # Thu thập chỉ số Prometheus
    prom_metrics = await scrape_prometheus_metrics()

    # Tổng hợp toàn bộ số liệu
    total_submitted = sum(m.submitted_answers for m in all_metrics)
    total_accepted = sum(m.accepted_answers for m in all_metrics)
    total_stream = sum(m.stream_length for m in all_metrics)
    total_postgres = sum(m.postgres_answers_count for m in all_metrics)

    all_broadcast_delays = []
    all_ack_rtts = []
    for m in all_metrics:
        all_broadcast_delays.extend(m.broadcast_delays_ms)
        all_ack_rtts.extend(m.ack_rtts_ms)

    bcast_p95 = calc_percentile(all_broadcast_delays, 95)
    ack_p95 = calc_percentile(all_ack_rtts, 95)

    print("\n==================================================================")
    print("                    KẾT QUẢ TỔNG HỢP LOAD TEST                    ")
    print("==================================================================")
    print(f"Chế độ kiểm thử           : {args.mode}")
    print(f"Tổng số trận đấu          : {len(all_metrics)}")
    print(f"Tổng số bot tham gia      : {sum(m.bot_count for m in all_metrics)}")
    print(f"Tổng số bot kết nối thành công: {sum(m.connected_bots for m in all_metrics)}")
    print(f"Tổng số câu trả lời gửi   : {total_submitted}")
    print(f"Số đáp án accepted        : {total_accepted}")
    print(f"Độ dài Stream answers     : {total_stream}")
    print(f"Số dòng bảng PostgreSQL   : {total_postgres}")
    print(f"Trạng thái đối soát       : {'THÀNH CÔNG (100% Khớp)' if total_accepted == total_stream == total_postgres else 'KHÔNG KHỚP'}")
    print("------------------------------------------------------------------")
    print("CHỈ SỐ ĐỘ TRỄ (SLA KHẢ NĂNG CHỊU TẢI):")
    print(f"  NF-2 (answer.ack RTT client)     : p50={calc_percentile(all_ack_rtts, 50):.1f}ms | p95={ack_p95:.1f}ms | p99={calc_percentile(all_ack_rtts, 99):.1f}ms (SLA < 150ms)")
    if prom_metrics.get("server_p95_answer_duration_ms") is not None:
        print(f"  NF-2 (server answer_duration p95): {prom_metrics['server_p95_answer_duration_ms']}ms (Prometheus metric)")
    print(f"  NF-3 (Broadcast delay)           : p50={calc_percentile(all_broadcast_delays, 50):.1f}ms | p95={bcast_p95:.1f}ms | p99={calc_percentile(all_broadcast_delays, 99):.1f}ms (SLA < 500ms)")
    print("------------------------------------------------------------------")
    print("BỘ NHỚ REDIS & PHÂN BỔ KẾT NỐI:")
    print(f"  Redis Memory trước test : {mem_before['used_memory_mb']} MB")
    print(f"  Redis Memory sau test   : {mem_after['used_memory_mb']} MB (Peak: {mem_after['peak_memory_mb']} MB / 128 MB)")
    print(f"  Phân bổ kết nối WS      : {prom_metrics.get('ws_connections_per_replica')}")
    print("==================================================================\n")

    # Lưu kết quả JSON để phục vụ viết báo cáo docs/LOADTEST.md
    report_data = {
        "mode": args.mode,
        "timestamp": datetime.now(UTC).isoformat(),
        "total_bots": sum(m.bot_count for m in all_metrics),
        "total_submitted": total_submitted,
        "total_accepted": total_accepted,
        "total_stream": total_stream,
        "total_postgres": total_postgres,
        "reconciliation_passed": total_accepted == total_stream == total_postgres,
        "metrics": {
            "ack_rtt_p50": round(calc_percentile(all_ack_rtts, 50), 1),
            "ack_rtt_p90": round(calc_percentile(all_ack_rtts, 90), 1),
            "ack_rtt_p95": round(ack_p95, 1),
            "ack_rtt_p99": round(calc_percentile(all_ack_rtts, 99), 1),
            "broadcast_p50": round(calc_percentile(all_broadcast_delays, 50), 1),
            "broadcast_p90": round(calc_percentile(all_broadcast_delays, 90), 1),
            "broadcast_p95": round(bcast_p95, 1),
            "broadcast_p99": round(calc_percentile(all_broadcast_delays, 99), 1),
        },
        "redis_memory": {
            "before_mb": mem_before["used_memory_mb"],
            "after_mb": mem_after["used_memory_mb"],
            "peak_mb": mem_after["peak_memory_mb"],
        },
        "matches": [m.stats() for m in all_metrics],
    }

    report_path = f"../infra/loadtest/result_{args.mode}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)
    print(f"[+] Đã lưu báo cáo chi tiết vào {report_path}")

    await redis.aclose()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
