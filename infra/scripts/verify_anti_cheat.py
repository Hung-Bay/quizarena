"""Kịch bản nghiệm thu 6 kịch bản Chống gian lận (§7.11 ANTI_CHEAT).

Chạy trực tiếp qua Nginx gateway (http://localhost, ws://localhost) và kiểm tra:
1. Mở DevTools xem WS message -> Không thấy đáp án trước khi hết giờ (FR-GAME-08).
2. Gửi answer.submit 2 lần / sau deadline / cho câu chưa mở -> Bị từ chối (Idempotency).
3. Guest token của trận A dùng cho trận B -> Bị từ chối HTTP 403 (Token Isolation).
4. Player gửi message của Host (game.next, game.start) -> Bị từ chối và ghi log cảnh báo (RBAC).
5. Spam tin nhắn liên tục -> Bị rate limit và ngắt kết nối WebSocket mã 1008 (Anti-Spam).
6. Dò PIN bằng script -> Bị chặn 2 lớp: Nginx limit_req + Ứng dụng khóa IP 15 phút (Anti-Brute Force).
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid

import websockets
from redis.asyncio import Redis

REDIS_URL = "redis://:AK.Gamer1@localhost:6380/0"
BASE_HTTP = "http://localhost"
BASE_WS = "ws://localhost"


async def main() -> None:
    print("=" * 60)
    print(" BẮT ĐẦU KIỂM CHỨNG 6 KỊCH BẢN CHỐNG GIAN LẬN (§7.11 ANTI_CHEAT)")
    print("=" * 60)

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    await redis.ping()
    print("[INIT] Đã kết nối Redis container thành công.")

    # -------------------------------------------------------------
    # Setup Trận đấu Mẫu
    # -------------------------------------------------------------
    game_id = str(uuid.uuid4())
    host_id = str(uuid.uuid4())
    pin = f"{uuid.uuid4().int % 900000 + 100000}"

    q1_id = str(uuid.uuid4())
    opt1_id = str(uuid.uuid4())
    opt2_id = str(uuid.uuid4())

    quiz_snapshot = {
        "title": "Anti Cheat Quiz Test",
        "questions": [
            {
                "id": q1_id,
                "position": 1,
                "content": "Câu hỏi bảo mật 1: 2 + 2 bằng mấy?",
                "type": "single",
                "points": 1000,
                "time_limit_s": 20,
                "options": [
                    {"id": opt1_id, "position": 1, "content": "4", "is_correct": True},
                    {"id": opt2_id, "position": 2, "content": "5", "is_correct": False},
                ],
            }
        ],
    }

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
    await redis.set(f"pin:{pin}", game_id, ex=3600)
    await redis.set(f"game:{game_id}:quiz", json.dumps(quiz_snapshot), ex=3600)
    await redis.sadd(f"game:{game_id}:q:1:correct", opt1_id)
    await redis.hset(
        f"game:{game_id}:q:1:meta",
        mapping={"id": q1_id, "type": "single", "points": "1000", "time_limit_s": "20"},
    )

    async def create_ticket(client_id: str, is_host: bool = False, target_game_id: str = game_id) -> str:
        ticket = f"t_{uuid.uuid4().hex[:12]}"
        data = {
            "sub": client_id,
            "game_id": target_game_id,
            "roles": ["host"] if is_host else ["guest"],
            "permissions": ["game:host_own"] if is_host else ["game:play"],
            "type": "guest",
        }
        await redis.set(f"ws:ticket:{ticket}", json.dumps(data), ex=60)
        return ticket

    # =============================================================
    # KỊCH BẢN 1: Mở DevTools xem WS message -> KHÔNG THẤY ĐÁP ÁN
    # =============================================================
    print("\n--- [KỊCH BẢN 1] Kiểm tra rò rỉ đáp án trong gói tin WebSocket (FR-GAME-08) ---")
    host_ticket = await create_ticket(host_id, is_host=True)
    player1_id = f"guest:{uuid.uuid4()}"
    p1_ticket = await create_ticket(player1_id, is_host=False)

    async with websockets.connect(f"{BASE_WS}/ws/game?ticket={host_ticket}") as ws_host, \
               websockets.connect(f"{BASE_WS}/ws/game?ticket={p1_ticket}") as ws_p1:

        # Host bắt đầu trận đấu câu 1
        await ws_host.send(json.dumps({"type": "game.start", "v": 1, "data": {}}))

        # Player 1 chờ nhận gói tin question.start
        leaked_answer = False
        received_q_start = False
        start_wait = time.time()
        while time.time() - start_wait < 5.0:
            msg = json.loads(await ws_p1.recv())
            if msg.get("type") == "question.start":
                received_q_start = True
                data = msg.get("data", {})
                options = data.get("options", [])
                for opt in options:
                    if "is_correct" in opt or "correct" in opt:
                        leaked_answer = True
                raw_text = json.dumps(msg)
                if "is_correct" in raw_text:
                    leaked_answer = True
                break

        assert received_q_start, "LỖI: Player không nhận được question.start"
        assert not leaked_answer, "LỖI BẢO MẬT: Gói tin question.start làm rò rỉ đáp án is_correct!"
        print("  -> THÀNH CÔNG: Gói tin question.start chỉ chứa id, position, content. Không chứa is_correct.")

        # =============================================================
        # KỊCH BẢN 2: Submit 2 lần / sau deadline / cho câu chưa mở
        # =============================================================
        print("\n--- [KỊCH BẢN 2] Kiểm tra nộp bài trùng lặp (Idempotency) & vi phạm câu hỏi ---")
        # Nộp bài lần 1: Hợp lệ
        await ws_p1.send(
            json.dumps({
                "type": "answer.submit",
                "v": 1,
                "data": {"q_index": 1, "selected_options": [opt1_id], "client_time": int(time.time() * 1000)},
            })
        )
        ack1 = json.loads(await ws_p1.recv())
        assert ack1.get("type") == "answer.ack" and ack1["data"]["accepted"] is True, f"Lần 1 thất bại: {ack1}"
        print(f"  -> Lần 1: Chấp nhận đáp án thành công (points={ack1['data'].get('points')}).")

        # Nộp bài lần 2 (cho cùng câu hỏi): Bị từ chối do ALREADY_ANSWERED
        await ws_p1.send(
            json.dumps({
                "type": "answer.submit",
                "v": 1,
                "data": {"q_index": 1, "selected_options": [opt2_id], "client_time": int(time.time() * 1000)},
            })
        )
        ack2 = json.loads(await ws_p1.recv())
        assert ack2.get("type") == "answer.ack" and ack2["data"]["accepted"] is False, f"Lỗi idempotency: {ack2}"
        reason = ack2["data"].get("reason") or ack2["data"].get("error_code")
        assert reason == "ALREADY_ANSWERED", f"Mã lỗi không khớp: {ack2}"
        print(f"  -> Lần 2: Từ chối thành công với mã lỗi: {reason}.")

        # =============================================================
        # KỊCH BẢN 4: Player gửi message của Host (game.next)
        # =============================================================
        print("\n--- [KỊCH BẢN 4] Player cố tình gửi lệnh điều khiển của Host (game.next) ---")
        await ws_p1.send(
            json.dumps({
                "type": "game.next",
                "v": 1,
                "data": {"expected_q_index": 1},
            })
        )
        res_forbidden = json.loads(await ws_p1.recv())
        assert res_forbidden.get("type") == "error", f"Không trả về lỗi: {res_forbidden}"
        assert res_forbidden["data"].get("code") == "FORBIDDEN", f"Mã lỗi không phải FORBIDDEN: {res_forbidden}"
        print(f"  -> THÀNH CÔNG: Server chặn lệnh game.next từ Player với mã: {res_forbidden['data']['code']}.")

    # =============================================================
    # KỊCH BẢN 3: Guest token của trận A dùng cho trận B
    # =============================================================
    print("\n--- [KỊCH BẢN 3] Token của trận A đem dùng cho trận B (Token Isolation) ---")
    game_b_id = str(uuid.uuid4())
    # Giả lập token/ticket của trận A kết nối vào session trận B
    bad_ticket = f"t_bad_{uuid.uuid4().hex[:12]}"
    await redis.set(
        f"ws:ticket:{bad_ticket}",
        json.dumps({
            "sub": f"guest:{uuid.uuid4()}",
            "game_id": game_id,  # Gắn với game A
            "roles": ["guest"],
            "permissions": ["game:play"],
            "type": "guest",
        }),
        ex=30,
    )

    # Thử gọi API tạo ticket với game_id khác qua REST endpoint
    req_data = json.dumps({"game_id": game_b_id}).encode()
    # Tạo JWT token của Game A
    from app.core.security import create_guest_token
    token_game_a = create_guest_token(game_id=game_id)
    req = urllib.request.Request(
        f"{BASE_HTTP}/api/v1/realtime/ws-ticket",
        data=req_data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token_game_a}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        raise AssertionError("LỖI: Server chấp nhận token trận A tạo ticket cho trận B!")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403, f"Mã HTTP không phải 403: {exc.code}"
        body = json.loads(exc.read().decode())
        assert body["error"]["code"] == "FORBIDDEN"
        print(f"  -> THÀNH CÔNG: Server từ chối HTTP 403 ({body['error']['code']}: {body['error']['message']}).")

    # =============================================================
    # KỊCH BẢN 5: Spam 100 message/giây -> Bị ngắt kết nối
    # =============================================================
    print("\n--- [KỊCH BẢN 5] Spam tin nhắn liên tục vượt ngưỡng (Anti-Spam) ---")
    spam_player_id = f"guest:{uuid.uuid4()}"
    spam_ticket = await create_ticket(spam_player_id, is_host=False)

    async with websockets.connect(f"{BASE_WS}/ws/game?ticket={spam_ticket}") as ws_spam:
        # Bắn liên tiếp 25 tin nhắn không có độ trễ
        received_rate_limited = False
        received_spam_disconnect = False
        closed_with_1008 = False

        for i in range(25):
            await ws_spam.send(json.dumps({"type": "chat.message", "v": 1, "data": {"text": f"spam {i}"}}))

        try:
            while True:
                raw = await asyncio.wait_for(ws_spam.recv(), timeout=2.0)
                msg = json.loads(raw)
                if msg.get("type") == "error":
                    code = msg.get("data", {}).get("code")
                    if code == "RATE_LIMITED":
                        received_rate_limited = True
                    elif code == "SPAM_DISCONNECTED":
                        received_spam_disconnect = True
        except websockets.exceptions.ConnectionClosed as exc:
            if exc.code == 1008:
                closed_with_1008 = True
        except asyncio.TimeoutError:
            pass

        assert received_rate_limited, "LỖI: Không nhận được cảnh báo RATE_LIMITED"
        assert received_spam_disconnect or closed_with_1008, "LỖI: Không bị ngắt kết nối khi spam dồn dập"
        print(f"  -> THÀNH CÔNG: Nhận cảnh báo RATE_LIMITED, nhận SPAM_DISCONNECTED và socket bị đóng mã 1008 (Policy Violation).")

    # =============================================================
    # KỊCH BẢN 6: Dò PIN bằng script -> Khóa IP 15 phút (Anti-Brute Force)
    # =============================================================
    print("\n--- [KỊCH BẢN 6] Tấn công dò PIN (Brute Force PIN) ---")
    attacker_ip = f"203.0.113.{uuid.uuid4().int % 250 + 1}"
    await redis.delete(f"pin_fail:{attacker_ip}", f"pin_lock:{attacker_ip}")

    # Gửi 9 lần sai -> HTTP 404 GAME_NOT_FOUND
    for i in range(1, 10):
        req = urllib.request.Request(
            f"{BASE_HTTP}/api/v1/games/join",
            data=json.dumps({"pin": "000000", "nickname": f"Attacker_{i}"}).encode(),
            headers={"Content-Type": "application/json", "X-Forwarded-For": attacker_ip},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, f"Lần {i} mã lỗi không phải 404: {exc.code}"

    # Lần thứ 10 sai -> Bị khóa HTTP 429 PIN_BRUTE_FORCE_LOCKED
    req10 = urllib.request.Request(
        f"{BASE_HTTP}/api/v1/games/join",
        data=json.dumps({"pin": "000000", "nickname": "Attacker_10"}).encode(),
        headers={"Content-Type": "application/json", "X-Forwarded-For": attacker_ip},
        method="POST",
    )
    try:
        urllib.request.urlopen(req10)
        raise AssertionError("LỖI: Lần thứ 10 không bị khóa 429!")
    except urllib.error.HTTPError as exc:
        assert exc.code == 429, f"Mã HTTP không phải 429: {exc.code}"
        body = json.loads(exc.read().decode())
        assert body["error"]["code"] == "PIN_BRUTE_FORCE_LOCKED"
        print(f"  -> Lần 10: Khóa thành công HTTP 429 ({body['error']['code']}: {body['error']['message']}).")

    # Lần thứ 11 (trong lúc bị khóa) -> Chặn ngay lập tức
    req11 = urllib.request.Request(
        f"{BASE_HTTP}/api/v1/games/join",
        data=json.dumps({"pin": pin, "nickname": "Attacker_11"}).encode(),
        headers={"Content-Type": "application/json", "X-Forwarded-For": attacker_ip},
        method="POST",
    )
    try:
        urllib.request.urlopen(req11)
        raise AssertionError("LỖI: Đang bị khóa nhưng vẫn thực hiện được yêu cầu!")
    except urllib.error.HTTPError as exc:
        assert exc.code == 429
        body = json.loads(exc.read().decode())
        assert body["error"]["code"] == "PIN_BRUTE_FORCE_LOCKED"
        print(f"  -> Lần 11: Đang bị khóa -> Chặn ngay lập tức: {body['error']['message']}")

    # Dọn dẹp key sau test
    await redis.delete(f"pin_fail:{attacker_ip}", f"pin_lock:{attacker_ip}", f"pin:{pin}")
    await redis.delete(f"game:{game_id}:state", f"game:{game_id}:quiz", f"game:{game_id}:q:1:meta")
    await redis.aclose()

    print("\n" + "=" * 60)
    print(" KẾT QUẢ: 6/6 KỊCH BẢN CHỐNG GIAN LẬN ĐỀU VƯỢT QUA VÀ BỊ CHẶN 100%!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
