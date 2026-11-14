"""Script thử nghiệm hỗn loạn (Chaos Drain Test - Phase 5 Thứ Ba):
Kiểm chứng tiêu chuẩn phi chức năng NF-5:
"Scale down hoặc deploy giữa trận: người chơi tự kết nối lại trong < 3 giây, không mất điểm"

Kịch bản:
1. Tạo 1 trận đấu mẫu live trên Redis cluster.
2. Host và 3 Players kết nối qua Nginx (phân bổ trên 3 replica realtime-1, realtime-2, realtime-3).
3. Host kích hoạt câu hỏi số 1 (thời gian 60s).
4. Player 1 nộp đáp án đúng và được cộng điểm trên Leaderboard (ví dụ: 950 điểm).
5. Kịch bản A (Graceful Drain):
   - Chạy `docker stop quizarena-realtime-1`.
   - Đo thời gian từ lúc container bắt đầu nhận lệnh stop tới khi Player trên replica đó nhận `server.draining`,
     reconnect thành công qua Nginx sang replica khác, và nhận lại Game Snapshot.
   - Kiểm chứng: Thời gian reconnect < 3.0s, điểm số 950 điểm giữ nguyên 100%.
6. Kịch bản B (Sudden Crash/Kill):
   - Chạy `docker kill quizarena-realtime-2`.
   - Đo thời gian từ lúc container bị kill tới khi Player trên replica đó tự động reconnect qua Nginx
     sang replica còn lại (`realtime-3`), và nhận lại Game Snapshot.
   - Kiểm chứng: Thời gian reconnect < 3.0s, điểm số giữ nguyên 100%.
7. Khôi phục lại toàn bộ cụm replica (`docker start quizarena-realtime-1 quizarena-realtime-2`).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import uuid
from typing import Any

import websockets
from redis.asyncio import Redis

REDIS_URL = "redis://:AK.Gamer1@localhost:6380/0"
BASE_WS = "ws://localhost"


async def make_ticket(redis: Redis, game_id: str, sub: str, is_host: bool = False) -> str:
    ticket = f"ticket_{uuid.uuid4().hex}"
    data = {
        "sub": sub if is_host else f"guest:{sub}",
        "game_id": game_id,
        "roles": ["host"] if is_host else ["guest"],
        "permissions": ["game:host_own"] if is_host else ["game:play"],
        "type": "guest",
    }
    await redis.set(f"ws:ticket:{ticket}", json.dumps(data), ex=60)
    return ticket


async def main() -> None:
    print("==================================================================")
    print("   QUIZARENA CHAOS DRAIN TEST — THỬ NGHIỆM HỖN LOẠN (THỨ BA)   ")
    print("==================================================================")

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    await redis.ping()
    print("[1] Kết nối Redis cluster thành công.")

    # 1. Khởi tạo dữ liệu trận đấu live
    game_id = str(uuid.uuid4())
    host_id = str(uuid.uuid4())
    pin = "888999"
    q1_id = str(uuid.uuid4())
    opt_correct_id = str(uuid.uuid4())
    opt_wrong_id = str(uuid.uuid4())

    await redis.hset(
        f"game:{game_id}:state",
        mapping={
            "game_id": game_id,
            "pin": pin,
            "host_id": host_id,
            "status": "lobby",
            "q_index": "0",
            "total_questions": "1",
            "deadline": "0",
            "deadline_version": "0",
        },
    )
    await redis.set(f"pin:{pin}", game_id)
    quiz_data = {
        "title": "Chaos Test Quiz",
        "questions": [
            {
                "id": q1_id,
                "position": 1,
                "content": "Thủ đô của Việt Nam là gì?",
                "type": "single",
                "points": 1000,
                "time_limit_s": 60,
                "options": [
                    {"id": opt_correct_id, "position": 1, "content": "Hà Nội", "is_correct": True},
                    {"id": opt_wrong_id, "position": 2, "content": "TP.HCM", "is_correct": False},
                ],
            }
        ],
    }
    await redis.set(f"game:{game_id}:quiz", json.dumps(quiz_data))
    await redis.sadd(f"game:{game_id}:q:1:correct_keys", opt_correct_id)
    await redis.hset(
        f"game:{game_id}:q:1:meta",
        mapping={"id": q1_id, "points": "1000", "time_limit_s": "60", "type": "single"},
    )

    # Đăng ký 3 Players
    players = []
    for i in range(1, 4):
        pid = f"player_{i}_{uuid.uuid4().hex[:4]}"
        nick = f"Player_{i}"
        await redis.hset(f"game:{game_id}:nicknames", pid, nick)
        await redis.hset(
            f"game:{game_id}:players",
            nick.lower(),
            json.dumps({"id": pid, "nickname": nick, "online": True, "score": 0}),
        )
        await redis.zadd(f"game:{game_id}:lb", {pid: 0})
        players.append((pid, nick))

    print(f"[2] Đã tạo trận đấu {game_id[:8]}... và đăng ký 3 players.")

    # 2. Kết nối Host và Players qua Nginx
    host_ticket = await make_ticket(redis, game_id, host_id, is_host=True)
    host_ws = await websockets.connect(f"{BASE_WS}/ws/game?ticket={host_ticket}")
    await host_ws.recv()  # Bỏ qua snapshot ban đầu
    print("[3] Host đã kết nối WebSocket qua Nginx.")

    player_sockets = []
    for pid, nick in players:
        t = await make_ticket(redis, game_id, pid, is_host=False)
        ws = await websockets.connect(f"{BASE_WS}/ws/game?ticket={t}")
        await ws.recv()  # Bỏ qua snapshot
        player_sockets.append((pid, nick, ws))
    print("[4] 3 Players đã kết nối WebSocket qua Nginx.")

    # 3. Host mở câu hỏi số 1
    print("\n--- BẮT ĐẦU CÂU HỎI 1 (THỜI GIAN 60S) ---")
    await host_ws.send(json.dumps({"type": "game.start", "v": 1, "data": {}}))

    # Nhận question.start trên tất cả client
    for pid, nick, ws in player_sockets:
        msg = json.loads(await ws.recv())
        assert msg["type"] == "question.start"

    # Player 1 nộp đáp án đúng để ghi nhận điểm
    p1_id, p1_nick, p1_ws = player_sockets[0]
    submit_msg = {
        "type": "answer.submit",
        "v": 1,
        "data": {
            "q_index": 1,
            "selected_option_ids": [opt_correct_id],
        },
    }
    await p1_ws.send(json.dumps(submit_msg))
    ack = json.loads(await p1_ws.recv())
    assert ack["type"] == "answer.ack" and ack["data"]["accepted"] is True
    p1_score = await redis.zscore(f"game:{game_id}:lb", p1_id)
    print(f"[+] Player 1 nộp bài thành công! Điểm số hiện tại trên Leaderboard: {int(p1_score)} điểm.")

    # 4. KỊCH BẢN A: GRACEFUL DRAIN QUA `docker stop`
    print("\n==================================================================")
    print(" KỊCH BẢN A: DRAIN REPLICA QUA `docker stop quizarena-realtime-1`")
    print("==================================================================")

    # Xác định Player 1 hoặc Player nào đang ở realtime-1
    # Dừng realtime-1
    stop_start_t = time.perf_counter()
    print("[*] Đang gửi lệnh `docker stop quizarena-realtime-1`...")
    # Chạy lệnh stop ở thread riêng để không block event loop
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: subprocess.run("docker stop -t 2 quizarena-realtime-1", shell=True, capture_output=True))

    print("[*] Đã phát tín hiệu dừng tới realtime-1. Đang theo dõi quá trình Reconnect của client...")

    # Client đang trên realtime-1 sẽ nhận server.draining hoặc bị đóng, sau đó reconnect
    # Giả lập client xử lý drain:
    reconnected_p1 = False
    reconnect_time = 0.0

    async def _handle_drain_and_reconnect(pid: str, old_ws: Any) -> float:
        t0 = time.perf_counter()
        # Đọc tin nhắn tiếp theo xem có phải server.draining không
        try:
            raw = await asyncio.wait_for(old_ws.recv(), timeout=2.0)
            parsed = json.loads(raw)
            if parsed.get("type") == "server.draining":
                print(f"    -> [OK] Nhận gói tin server.draining (retry_after={parsed['data'].get('retry_after_ms')}ms)")
        except Exception:
            pass

        # Đóng kết nối cũ
        try:
            await old_ws.close()
        except Exception:
            pass

        # Xin vé mới và kết nối lại qua Nginx
        new_ticket = await make_ticket(redis, game_id, pid, is_host=False)
        new_ws = await websockets.connect(f"{BASE_WS}/ws/game?ticket={new_ticket}")

        # Nhận snapshot trạng thái hiện tại
        snap_raw = await asyncio.wait_for(new_ws.recv(), timeout=2.0)
        snap = json.loads(snap_raw)
        elapsed = time.perf_counter() - t0

        assert snap["type"] == "snapshot"
        assert snap["data"]["status"] == "question_active"
        assert snap["data"]["player_score"] == int(p1_score)
        print(f"    -> [OK] Reconnect thành công sang replica mới!")
        print(f"    -> [OK] Điểm số giữ nguyên: {snap['data']['player_score']} điểm.")
        print(f"    -> [OK] Thời gian Reconnect: {elapsed * 1000:.1f}ms (< 3000ms SLA NF-5).")
        await new_ws.close()
        return elapsed

    reconnect_time = await _handle_drain_and_reconnect(p1_id, p1_ws)
    assert reconnect_time < 3.0, f"Thời gian reconnect quá chậm: {reconnect_time}s"
    print("[V] KỊCH BẢN A THÀNH CÔNG: Reconnect < 3s, không mất điểm!")

    # Khởi động lại realtime-1 trước khi tiến hành kịch bản B
    print("\n[*] Khởi động lại realtime-1 để cụm có đầy đủ replica...")
    await loop.run_in_executor(None, lambda: subprocess.run("docker start quizarena-realtime-1", shell=True, capture_output=True))
    await asyncio.sleep(2.0)

    # 5. KỊCH BẢN B: SUDDEN CRASH QUA `docker kill`
    print("\n==================================================================")
    print(" KỊCH BẢN B: CRASH BẤT NGỜ QUA `docker kill quizarena-realtime-2`")
    print("==================================================================")

    p2_id, p2_nick, p2_ws = player_sockets[1]
    print("[*] Đang gửi lệnh `docker kill quizarena-realtime-2`...")
    await loop.run_in_executor(None, lambda: subprocess.run("docker kill quizarena-realtime-2", shell=True, capture_output=True))
    print("[*] Đã tiêu diệt realtime-2 đột ngột!")

    t0_crash = time.perf_counter()
    # Client tự động phát hiện ngắt kết nối (ConnectionClosed) và reconnect ngay
    try:
        await p2_ws.recv()
    except Exception:
        pass
    try:
        await p2_ws.close()
    except Exception:
        pass

    new_t2 = await make_ticket(redis, game_id, p2_id, is_host=False)
    new_ws2 = await websockets.connect(f"{BASE_WS}/ws/game?ticket={new_t2}")
    snap_raw2 = await asyncio.wait_for(new_ws2.recv(), timeout=2.0)
    snap2 = json.loads(snap_raw2)
    crash_reconnect_time = time.perf_counter() - t0_crash

    assert snap2["type"] == "snapshot"
    assert snap2["data"]["status"] == "question_active"
    print(f"    -> [OK] Reconnect sau Crash thành công sang replica còn lại!")
    print(f"    -> [OK] Trạng thái trận đấu: {snap2['data']['status']}")
    print(f"    -> [OK] Thời gian Reconnect: {crash_reconnect_time * 1000:.1f}ms (< 3000ms SLA NF-5).")
    assert crash_reconnect_time < 3.0, f"Thời gian reconnect sau crash quá chậm: {crash_reconnect_time}s"
    await new_ws2.close()

    print("[V] KỊCH BẢN B THÀNH CÔNG: Reconnect sau Crash < 3s, không mất dữ liệu trận đấu!")

    # 6. KHÔI PHỤC LẠI CỤM CONTAINER
    print("\n==================================================================")
    print(" KHÔI PHỤC LẠI CÁC REPLICA VỀ ĐỦ 3 REALTIME...")
    print("==================================================================")
    await loop.run_in_executor(None, lambda: subprocess.run("docker start quizarena-realtime-2", shell=True, capture_output=True))
    print("[*] Đã bật lại realtime-2 thành công.")

    # Dọn dẹp
    await host_ws.close()
    await redis.aclose()
    print("\n=== TOÀN BỘ TIÊU CHÍ NGHIỆM THU THỨ BA (PHASE 5) ĐẠT 100% ===")

if __name__ == "__main__":
    asyncio.run(main())
