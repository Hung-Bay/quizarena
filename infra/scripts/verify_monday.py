"""Kịch bản nghiệm thu Thứ Hai (Phase 5):
1. Tạo 1 trận đấu mẫu trực tiếp trên Redis cluster.
2. Tạo 1 vé Host và 6 vé Player.
3. Mở đồng thời 7 kết nối WebSocket qua Nginx tại ws://localhost/ws/game?ticket=...
4. Kiểm tra phân bổ kết nối trên cả 3 replica realtime (đọc `ws_connections` hoặc nginx log upstream_addr).
5. Host broadcast một thông điệp -> Kiểm tra toàn bộ 6 Player (ở cả 3 replica) đều nhận được.
6. Host kick 1 Player -> Player đó bị đóng kết nối với code 4002.
"""

import asyncio
import json
import urllib.request
import websockets
from redis.asyncio import Redis

REDIS_URL = "redis://:AK.Gamer1@localhost:6380/0"
BASE_HTTP = "http://localhost"
BASE_WS = "ws://localhost"


async def main() -> None:
    print("=== BẮT ĐẦU NGHIỆM THU THỨ HAI - PHASE 5 ===")
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    await redis.ping()
    print("[1] Đã kết nối Redis container thành công.")

    # 1. Tạo dữ liệu trận đấu
    import uuid
    game_id = str(uuid.uuid4())
    host_id = str(uuid.uuid4())
    pin = "654321"

    await redis.hset(
        f"game:{game_id}:state",
        mapping={
            "game_id": game_id,
            "pin": pin,
            "host_id": host_id,
            "status": "lobby",
            "q_index": "0",
            "total_questions": "1",
        },
    )
    await redis.set(f"pin:{pin}", game_id)
    await redis.set(
        f"game:{game_id}:quiz",
        json.dumps({
            "title": "Monday Cluster Quiz",
            "questions": [
                {
                    "id": str(uuid.uuid4()),
                    "position": 1,
                    "content": "1 + 1 = ?",
                    "type": "single",
                    "points": 1000,
                    "time_limit_s": 15,
                    "options": [
                        {"id": str(uuid.uuid4()), "position": 1, "content": "2", "is_correct": True},
                        {"id": str(uuid.uuid4()), "position": 2, "content": "3", "is_correct": False},
                    ]
                }
            ]
        }),
    )

    # 2. Tạo vé WS cho 1 Host + 6 Player
    async def make_ticket(sub: str, is_host: bool = False) -> str:
        ticket = f"t_{sub}"
        data = {
            "sub": sub,
            "game_id": game_id,
            "roles": ["host"] if is_host else ["guest"],
            "permissions": ["game:host_own"] if is_host else ["game:play"],
            "type": "guest",
        }
        await redis.set(f"ws:ticket:{ticket}", json.dumps(data), ex=60)
        return ticket

    host_ticket = await make_ticket(host_id, is_host=True)
    player_tickets = []
    for i in range(1, 7):
        pid = f"player_{i}"
        nick = f"Player_{i}"
        await redis.hset(f"game:{game_id}:nicknames", pid, nick)
        await redis.hset(f"game:{game_id}:players", nick.lower(), json.dumps({"id": pid, "nickname": nick}))
        t = await make_ticket(f"guest:{pid}", is_host=False)
        player_tickets.append((pid, nick, t))

    print(f"[2] Đã sinh xong vé WS cho Host và 6 Players.")

    # 3. Kết nối WebSocket qua Nginx (reverse proxy least_conn)
    host_ws = await websockets.connect(f"{BASE_WS}/ws/game?ticket={host_ticket}")
    host_snap = json.loads(await host_ws.recv())
    assert host_snap["type"] == "snapshot"
    print(f"[3.1] Host đã kết nối thành công và nhận snapshot.")

    player_conns = []
    for pid, nick, t in player_tickets:
        ws = await websockets.connect(f"{BASE_WS}/ws/game?ticket={t}")
        snap = json.loads(await ws.recv())
        assert snap["type"] == "snapshot"
        player_conns.append((pid, nick, ws))
    print(f"[3.2] 6 Players đã kết nối thành công qua Nginx.")

    # Chờ 0.5s để metrics cập nhật
    await asyncio.sleep(0.5)

    # 4. Kiểm tra phân bổ kết nối trên 3 replica realtime
    print("\n[4] Kiểm tra số lượng kết nối `ws_connections` trên 3 replica:")
    total_conns = 0
    conns_per_replica = []
    for replica_name in ["quizarena-realtime-1", "quizarena-realtime-2", "quizarena-realtime-3"]:
        # Chạy lệnh docker exec để lấy metrics từ container
        import subprocess
        cmd = f"docker exec {replica_name} python -c \"import urllib.request as u; [print(l) for l in u.urlopen('http://127.0.0.1:8000/metrics').read().decode().splitlines() if 'ws_connections ' in l]\""
        out = subprocess.check_output(cmd, shell=True).decode().strip()
        val = float(out.split()[-1])
        conns_per_replica.append(val)
        total_conns += val
        print(f"    - {replica_name}: ws_connections = {val}")

    print(f"    => Tổng số kết nối ghi nhận trên cụm: {total_conns} (kỳ vọng = 7)")
    assert total_conns == 7.0, f"Tổng kết nối không khớp: {total_conns}"
    # Đảm bảo phân bổ trên nhiều hơn 1 replica
    active_replicas = sum(1 for c in conns_per_replica if c > 0)
    print(f"    => Số replica đang gánh kết nối: {active_replicas}/3")
    assert active_replicas >= 2, "Các kết nối chưa được phân bổ trên nhiều replica!"

    # 5. Broadcast xuyên replica: Host gửi lệnh bắt đầu / broadcast
    print("\n[5] Kiểm tra broadcast từ Host tới toàn bộ người chơi trên các replica...")
    broadcast_msg = {
        "type": "game.start",
        "v": 1,
        "data": {},
    }
    await host_ws.send(json.dumps(broadcast_msg))

    # Toàn bộ 6 players phải nhận được question.start
    received_count = 0
    for pid, nick, ws in player_conns:
        try:
            msg_raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            msg = json.loads(msg_raw)
            if msg.get("type") == "question.start":
                received_count += 1
        except Exception as e:
            print(f"    [!] Lỗi nhận broadcast ở {nick}: {e}")

    print(f"    => Số players nhận được broadcast question.start: {received_count}/6")
    assert received_count == 6, f"Không đủ người chơi nhận broadcast: {received_count}/6"

    # 6. Kiểm tra kick xuyên replica
    print("\n[6] Kiểm tra Host kick Player 1 xuyên replica...")
    target_pid = player_tickets[0][0]
    target_ws = player_conns[0][2]
    kick_msg = {
        "type": "player.kick",
        "v": 1,
        "data": {"player_id": target_pid},
    }
    await host_ws.send(json.dumps(kick_msg))

    # Chờ target_ws bị đóng với mã 4002
    kicked_closed_code = None
    try:
        await asyncio.wait_for(target_ws.recv(), timeout=2.0)
    except websockets.exceptions.ConnectionClosed as cc:
        kicked_closed_code = cc.code
    except Exception:
        kicked_closed_code = target_ws.close_code

    print(f"    => Kết quả đóng WebSocket của Player 1: mã = {kicked_closed_code}")
    assert kicked_closed_code == 4002, f"Mã đóng không phải 4002: {kicked_closed_code}"

    # Đóng các kết nối còn lại
    await host_ws.close()
    for _, _, ws in player_conns[1:]:
        try:
            await ws.close()
        except Exception:
            pass

    await redis.aclose()
    print("\n=== TẤT CẢ TIÊU CHÍ NGHIỆM THU THỨ HAI ĐỀU THÀNH CÔNG RỰC RỠ! ===")

if __name__ == "__main__":
    asyncio.run(main())
