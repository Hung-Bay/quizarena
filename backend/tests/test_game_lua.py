"""Kiểm thử toàn diện Lua Engine cho Live Game Engine (Thứ Ba).

Tuân thủ nghiêm ngặt các tiêu chí nghiệm thu Thứ Ba:
- Lua transition (start/next/close/end/pause) và submit_answer (state + deadline + chưa trả lời -> ghi -> ZINCRBY -> HINCRBY -> XADD).
- Idempotent close_question: ARQ job và host cùng đóng không bị lỗi.
- Chống bấm đúp (game.next): chặn nhảy hai câu bằng expected_q_index.
- Test đối chiếu chéo (Cross-validation) giữa Python và Lua trên 150+ ca ngẫu nhiên, khớp tuyệt đối cả quy tắc làm tròn nửa lên (half-up).
- Test đồng thời 200 đáp án:
  + Không race condition.
  + Điểm trên bảng ZSET = tổng điểm đã ghi.
  + Tổng phân bố trong dist = số đáp án chấp nhận.
  + Số bản ghi trong Stream answers = 200.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from typing import Any

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.game import lua_engine
from app.game.scoring import BinaryScoringStrategy, StandardScoringStrategy

REDIS_TEST_URL = "redis://localhost:6379/0"


@pytest_asyncio.fixture
async def redis_client() -> Any:
    """Fixture cung cấp Redis client kết nối trực tiếp Redis 7 thật cho test Lua."""
    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(
            f"Cần Redis server đang chạy trên localhost:6379 để kiểm thử Lua scripts: {exc}"
        )
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


# Helper khởi tạo một trận đấu mẫu trên Redis
async def setup_redis_game(
    redis: Redis,
    *,
    game_id: uuid.UUID | str,
    total_questions: int = 3,
    strategy_id: str = "standard",
    player_ids: list[str] | None = None,
) -> None:
    gid = str(game_id)
    pipe = redis.pipeline()
    pipe.hset(
        f"game:{gid}:state",
        mapping={
            "game_id": gid,
            "status": "lobby",
            "q_index": "0",
            "total_questions": str(total_questions),
            "strategy_id": strategy_id,
            "deadline": "0",
            "deadline_version": "0",
        },
    )

    # Thêm câu hỏi
    for idx in range(1, total_questions + 1):
        pipe.hset(
            f"game:{gid}:q:{idx}:meta",
            mapping={
                "id": str(uuid.uuid4()),
                "points": "1000",
                "time_limit_s": "15",
                "type": "single" if idx % 2 != 0 else "multiple",
            },
        )
        if idx % 2 != 0:
            # Single: opt_a đúng
            pipe.delete(f"game:{gid}:q:{idx}:correct_keys")
            pipe.sadd(f"game:{gid}:q:{idx}:correct_keys", "opt_a")
        else:
            # Multiple: opt_a và opt_b đúng
            pipe.delete(f"game:{gid}:q:{idx}:correct_keys")
            pipe.sadd(f"game:{gid}:q:{idx}:correct_keys", "opt_a", "opt_b")

    # Thêm players nếu có
    if player_ids:
        for pid in player_ids:
            pipe.hset(
                f"game:{gid}:players", pid, json.dumps({"id": pid, "nickname": f"player_{pid[:4]}"})
            )
            pipe.zadd(f"game:{gid}:lb", {pid: 0})

    await pipe.execute()


# =========================================================================
# 1. Test Đối chiếu chéo (Cross-validation): Python <-> Lua
# =========================================================================


class TestCrossValidationPythonVsLua:
    """Đối chiếu chéo 100+ ca tính điểm giữa Python và Lua trên cùng đầu vào."""

    @pytest.mark.asyncio
    async def test_cross_validation_standard_strategy(self, redis_client: Redis) -> None:
        py_strategy = StandardScoringStrategy()
        game_id = uuid.uuid4()
        await setup_redis_game(redis_client, game_id=game_id, strategy_id="standard")

        # Chuẩn bị meta câu 1: time_limit = 10s, base_points = 1000
        time_limit_s = 10
        base_points = 1000
        await redis_client.hset(
            f"game:{game_id}:q:1:meta",
            mapping={
                "points": str(base_points),
                "time_limit_s": str(time_limit_s),
                "type": "single",
            },
        )

        # Chạy 100 test case đa dạng bao gồm cả các mốc biên và phần lẻ .5
        test_cases = [
            # (response_ms, current_streak, is_correct)
            (0, 0, True),  # Đúng tức thì, streak 0 -> 1
            (0, 2, True),  # Đúng tức thì, streak 2 -> 3 (thưởng 10%)
            (5000, 0, True),  # Đúng ở giữa (5s/10s = 0.5)
            (5000, 2, True),  # Đúng ở giữa có thưởng streak (750 * 1.1 = 825)
            (10000, 0, True),  # Đúng ở deadline (10s/10s = 1.0 -> 50% điểm)
            (10000, 2, True),  # Đúng ở deadline có thưởng streak (500 * 1.1 = 550)
            (2500, 1, True),  # Điểm lẻ
            (3333, 4, True),  # Điểm có số thập phân
            (9999, 0, True),  # Sát deadline
            (1000, 5, False),  # Trả lời sai -> 0 điểm, reset streak
        ]

        # Thêm 90 ca ngẫu nhiên
        random.seed(42)
        for _ in range(90):
            r_ms = random.randint(0, 10000)
            c_strk = random.randint(0, 5)
            corr = random.choice([True, True, True, False])
            test_cases.append((r_ms, c_strk, corr))

        for idx, (resp_ms, curr_streak, is_corr) in enumerate(test_cases, start=1):
            player_id = f"test_p_{idx}"
            # Thiết lập streak ban đầu trong Redis
            await redis_client.hset(f"game:{game_id}:streaks", player_id, str(curr_streak))

            # Giả lập start time và deadline để response_ms chính xác
            start_ms = 1_000_000
            dl_ms = start_ms + (time_limit_s * 1000)
            now_ms = start_ms + resp_ms

            # Cập nhật state game đang active với deadline trên
            await redis_client.hset(
                f"game:{game_id}:state",
                mapping={
                    "status": "question_active",
                    "q_index": "1",
                    "deadline": str(dl_ms),
                },
            )

            # Tính điểm bằng Python
            py_pts, py_new_streak = py_strategy.calculate(
                is_correct=is_corr,
                base_points=base_points,
                response_ms=resp_ms,
                time_limit_s=time_limit_s,
                current_streak=curr_streak,
            )

            # Tính điểm bằng Lua
            opts = ["opt_a"] if is_corr else ["opt_wrong"]
            lua_res = await lua_engine.execute_submit_answer(
                redis_client,
                game_id=game_id,
                player_id=player_id,
                q_index=1,
                selected_option_ids=opts,
                override_now_ms=now_ms,
            )

            assert lua_res.accepted is True
            assert lua_res.is_correct == is_corr
            # Điểm số và streak phải khớp nhau tuyệt đối từng đơn vị!
            assert lua_res.points_awarded == py_pts, (
                f"Lệch điểm tại case {idx}: resp_ms={resp_ms}, curr_streak={curr_streak}, "
                f"Lua={lua_res.points_awarded} vs Python={py_pts}"
            )
            assert lua_res.new_streak == py_new_streak, (
                f"Lệch streak tại case {idx}: Lua={lua_res.new_streak} vs Python={py_new_streak}"
            )

    @pytest.mark.asyncio
    async def test_cross_validation_binary_strategy(self, redis_client: Redis) -> None:
        py_strategy = BinaryScoringStrategy()
        game_id = uuid.uuid4()
        await setup_redis_game(redis_client, game_id=game_id, strategy_id="binary")

        time_limit_s = 15
        base_points = 500
        await redis_client.hset(
            f"game:{game_id}:q:1:meta",
            mapping={
                "points": str(base_points),
                "time_limit_s": str(time_limit_s),
                "type": "single",
            },
        )

        for idx in range(30):
            player_id = f"bin_p_{idx}"
            resp_ms = random.randint(0, 14000)
            curr_streak = idx % 4
            is_corr = idx % 5 != 0

            await redis_client.hset(f"game:{game_id}:streaks", player_id, str(curr_streak))
            start_ms = 1_000_000
            dl_ms = start_ms + (time_limit_s * 1000)
            now_ms = start_ms + resp_ms

            await redis_client.hset(
                f"game:{game_id}:state",
                mapping={"status": "question_active", "q_index": "1", "deadline": str(dl_ms)},
            )

            py_pts, py_new_streak = py_strategy.calculate(
                is_correct=is_corr,
                base_points=base_points,
                response_ms=resp_ms,
                time_limit_s=time_limit_s,
                current_streak=curr_streak,
            )

            opts = ["opt_a"] if is_corr else ["opt_x"]
            lua_res = await lua_engine.execute_submit_answer(
                redis_client,
                game_id=game_id,
                player_id=player_id,
                q_index=1,
                selected_option_ids=opts,
                override_now_ms=now_ms,
            )

            assert lua_res.accepted is True
            assert lua_res.points_awarded == py_pts
            assert lua_res.new_streak == py_new_streak


# =========================================================================
# 2. Test Chuyển đổi trạng thái (Transition): start, next, close, end, pause
# =========================================================================


class TestStateTransitionsLua:
    """Kiểm tra các hành vi chuyển trạng thái vòng đời trận và idempotency."""

    @pytest.mark.asyncio
    async def test_game_start(self, redis_client: Redis) -> None:
        game_id = uuid.uuid4()
        await setup_redis_game(redis_client, game_id=game_id)

        # 1. Bắt đầu trận từ LOBBY -> Thành công
        res = await lua_engine.execute_transition(
            redis_client,
            game_id=game_id,
            action="start",
            time_limit_s=10,
            override_now_ms=1000,
        )
        assert res.ok is True
        assert res.status == "question_active"
        assert res.q_index == 1
        assert res.deadline == 1000 + 10000
        assert res.deadline_version == 1

        # 2. Gọi lại start lần nữa khi không còn ở LOBBY -> Bị từ chối
        res2 = await lua_engine.execute_transition(
            redis_client,
            game_id=game_id,
            action="start",
        )
        assert res2.ok is False
        assert res2.error == "INVALID_STATE"

    @pytest.mark.asyncio
    async def test_double_click_protection_on_game_next(self, redis_client: Redis) -> None:
        game_id = uuid.uuid4()
        await setup_redis_game(redis_client, game_id=game_id, total_questions=3)

        # Đưa game về trạng thái question_result ở câu 1
        await redis_client.hset(
            f"game:{game_id}:state",
            mapping={"status": "question_result", "q_index": "1"},
        )

        # Host bấm next lần 1 (expected_q_index = 2) -> Thành công
        res1 = await lua_engine.execute_transition(
            redis_client,
            game_id=game_id,
            action="next",
            expected_q_index=2,
            time_limit_s=15,
        )
        assert res1.ok is True
        assert res1.status == "question_active"
        assert res1.q_index == 2

        # Host bấm đúp chuột (yêu cầu tiếp theo với expected_q_index = 2)
        # Trong khi game đã chuyển lên câu 2 -> Bị từ chối VERSION_MISMATCH, không nhảy câu 3!
        res2 = await lua_engine.execute_transition(
            redis_client,
            game_id=game_id,
            action="next",
            expected_q_index=2,
        )
        assert res2.ok is False
        assert res2.error in ("INVALID_STATE", "VERSION_MISMATCH")

    @pytest.mark.asyncio
    async def test_idempotent_close_question(self, redis_client: Redis) -> None:
        """Đóng câu hỏi phải idempotent: ARQ worker hết giờ và Host bấm đóng cùng lúc không gây lỗi."""
        game_id = uuid.uuid4()
        await setup_redis_game(redis_client, game_id=game_id)

        # Đặt game đang active ở câu 1
        await redis_client.hset(
            f"game:{game_id}:state",
            mapping={"status": "question_active", "q_index": "1", "deadline": "50000"},
        )

        # 1. Lần đóng thứ nhất (ví dụ ARQ worker chạy trước) -> Thành công
        c1 = await lua_engine.execute_transition(
            redis_client,
            game_id=game_id,
            action="close",
            expected_q_index=1,
        )
        assert c1.ok is True
        assert c1.status == "question_result"

        # 2. Lần đóng thứ hai (ví dụ Host bấm đóng hoặc job trễ) -> Vẫn thành công idempotent!
        c2 = await lua_engine.execute_transition(
            redis_client,
            game_id=game_id,
            action="close",
            expected_q_index=1,
        )
        assert c2.ok is True
        assert c2.status == "question_result"

    @pytest.mark.asyncio
    async def test_game_end_and_pause(self, redis_client: Redis) -> None:
        game_id = uuid.uuid4()
        await setup_redis_game(redis_client, game_id=game_id)

        # Pause
        p_res = await lua_engine.execute_transition(redis_client, game_id=game_id, action="pause")
        assert p_res.ok is True
        assert p_res.status == "paused"
        assert p_res.deadline_version == 1

        # End
        e_res = await lua_engine.execute_transition(redis_client, game_id=game_id, action="end")
        assert e_res.ok is True
        assert e_res.status == "finished"


# =========================================================================
# 3. Test Các luật kiểm soát và từ chối trong submit_answer
# =========================================================================


class TestSubmitAnswerRules:
    """Kiểm tra các trường hợp vi phạm: đã nộp, trễ hạn, sai câu, sai trạng thái."""

    @pytest.mark.asyncio
    async def test_duplicate_answer_rejected(self, redis_client: Redis) -> None:
        game_id = uuid.uuid4()
        player_id = "user_abc"
        await setup_redis_game(redis_client, game_id=game_id, player_ids=[player_id])

        # Đặt câu đang active
        t_res = await redis_client.time()
        now_ms = t_res[0] * 1000 + t_res[1] // 1000
        dl_ms = now_ms + 60000
        await redis_client.hset(
            f"game:{game_id}:state",
            mapping={"status": "question_active", "q_index": "1", "deadline": str(dl_ms)},
        )

        # Lần 1: Nộp thành công
        res1 = await lua_engine.execute_submit_answer(
            redis_client,
            game_id=game_id,
            player_id=player_id,
            q_index=1,
            selected_option_ids=["opt_a"],
        )
        assert res1.accepted is True

        # Lần 2: Nộp lại câu 1 -> Bị từ chối ALREADY_ANSWERED
        res2 = await lua_engine.execute_submit_answer(
            redis_client,
            game_id=game_id,
            player_id=player_id,
            q_index=1,
            selected_option_ids=["opt_a"],
        )
        assert res2.accepted is False
        assert res2.error_code == "ALREADY_ANSWERED"

    @pytest.mark.asyncio
    async def test_late_submission_rejected(self, redis_client: Redis) -> None:
        game_id = uuid.uuid4()
        player_id = "user_late"
        await setup_redis_game(redis_client, game_id=game_id, player_ids=[player_id])

        deadline = 5000
        await redis_client.hset(
            f"game:{game_id}:state",
            mapping={"status": "question_active", "q_index": "1", "deadline": str(deadline)},
        )

        # Gửi tại thời điểm now_ms = 5001 (> deadline 5000) -> Bị từ chối LATE
        res = await lua_engine.execute_submit_answer(
            redis_client,
            game_id=game_id,
            player_id=player_id,
            q_index=1,
            selected_option_ids=["opt_a"],
            override_now_ms=5001,
        )
        assert res.accepted is False
        assert res.error_code == "LATE"

    @pytest.mark.asyncio
    async def test_wrong_question_index_rejected(self, redis_client: Redis) -> None:
        game_id = uuid.uuid4()
        player_id = "user_lost"
        await setup_redis_game(redis_client, game_id=game_id, player_ids=[player_id])

        # Đang ở câu 1
        await redis_client.hset(
            f"game:{game_id}:state",
            mapping={"status": "question_active", "q_index": "1", "deadline": "9999999999"},
        )

        # Nộp câu 2 -> Bị từ chối WRONG_QUESTION
        res = await lua_engine.execute_submit_answer(
            redis_client,
            game_id=game_id,
            player_id=player_id,
            q_index=2,
            selected_option_ids=["opt_a"],
        )
        assert res.accepted is False
        assert res.error_code == "WRONG_QUESTION"

    @pytest.mark.asyncio
    async def test_multiple_choice_exact_match(self, redis_client: Redis) -> None:
        """Câu multiple choice: phải chọn đúng 100% tất cả các đáp án đúng."""
        game_id = uuid.uuid4()
        # Câu 2 là multiple, đáp án đúng: opt_a, opt_b
        await setup_redis_game(redis_client, game_id=game_id)
        t_res = await redis_client.time()
        now_ms = t_res[0] * 1000 + t_res[1] // 1000
        dl_ms = now_ms + 60000
        await redis_client.hset(
            f"game:{game_id}:state",
            mapping={"status": "question_active", "q_index": "2", "deadline": str(dl_ms)},
        )

        # Người 1: Chọn đủ opt_a, opt_b -> Đúng
        r1 = await lua_engine.execute_submit_answer(
            redis_client,
            game_id=game_id,
            player_id="p1",
            q_index=2,
            selected_option_ids=["opt_a", "opt_b"],
        )
        assert r1.accepted is True
        assert r1.is_correct is True

        # Người 2: Chọn thiếu (chỉ chọn opt_a) -> Sai (0 điểm)
        r2 = await lua_engine.execute_submit_answer(
            redis_client,
            game_id=game_id,
            player_id="p2",
            q_index=2,
            selected_option_ids=["opt_a"],
        )
        assert r2.accepted is True
        assert r2.is_correct is False
        assert r2.points_awarded == 0

        # Người 3: Chọn đủ nhưng thừa opt_c -> Sai (0 điểm)
        r3 = await lua_engine.execute_submit_answer(
            redis_client,
            game_id=game_id,
            player_id="p3",
            q_index=2,
            selected_option_ids=["opt_a", "opt_b", "opt_c"],
        )
        assert r3.accepted is True
        assert r3.is_correct is False
        assert r3.points_awarded == 0


# =========================================================================
# 4. Test 200 Đáp án Đồng thời (Concurrency) - Tiêu chí chốt Thứ Ba
# =========================================================================


class TestConcurrentAnswersSubmission:
    """Mô phỏng 200 người chơi gửi đáp án đồng thời vào cùng một câu hỏi."""

    @pytest.mark.asyncio
    async def test_200_concurrent_answers_atomicity(self, redis_client: Redis) -> None:
        game_id = uuid.uuid4()
        num_players = 200
        player_ids = [f"player_{i:03d}" for i in range(1, num_players + 1)]

        await setup_redis_game(redis_client, game_id=game_id, player_ids=player_ids)

        # Câu 1: time_limit = 15s, correct = opt_a
        time_limit_s = 15
        start_ms = 1_000_000
        deadline_ms = start_ms + (time_limit_s * 1000)

        await redis_client.hset(
            f"game:{game_id}:state",
            mapping={
                "status": "question_active",
                "q_index": "1",
                "deadline": str(deadline_ms),
            },
        )

        # Chuẩn bị dữ liệu cho 200 người:
        # - 150 người chọn đúng (opt_a) với response_ms khác nhau
        # - 50 người chọn sai (opt_b hoặc opt_c)
        tasks = []
        for i, pid in enumerate(player_ids):
            is_correct_choice = i < 150
            chosen_opts = ["opt_a"] if is_correct_choice else [random.choice(["opt_b", "opt_c"])]
            # Thời gian phản hồi phân tán trong khoảng 100ms -> 12000ms
            simulated_response_ms = random.randint(100, 12000)
            now_ms = start_ms + simulated_response_ms

            task = lua_engine.execute_submit_answer(
                redis_client,
                game_id=game_id,
                player_id=pid,
                q_index=1,
                selected_option_ids=chosen_opts,
                override_now_ms=now_ms,
            )
            tasks.append(task)

        # BẮT ĐẦU BẮN 200 REQUEST ĐỒNG THỜI
        results: list[lua_engine.SubmitAnswerResult] = await asyncio.gather(*tasks)

        # =====================================================================
        # KIỂM CHỨNG TÍNH TOÀN VẸN VÀ BẤT BIẾN (INVARIANTS)
        # =====================================================================

        # 1. Tất cả 200 request đều được Lua chấp nhận thành công (không có ai bị lỗi hệ thống)
        assert len(results) == num_players
        assert all(r.accepted is True for r in results)

        # 2. Số lượng người đúng/sai chính xác tuyệt đối
        correct_results = [r for r in results if r.is_correct is True]
        incorrect_results = [r for r in results if r.is_correct is False]
        assert len(correct_results) == 150
        assert len(incorrect_results) == 50

        # 3. Tất cả người trả lời sai đều 0 điểm
        assert all(r.points_awarded == 0 for r in incorrect_results)

        # 4. Tập answered_set có chính xác 200 player_id
        answered_count = await redis_client.scard(f"game:{game_id}:q:1:answered")
        assert answered_count == num_players

        # 5. [TIÊU CHÍ CHỐT 1]: Điểm trên bảng xếp hạng = tổng điểm đã ghi
        # Lấy toàn bộ điểm từ ZSET game:{id}:lb
        lb_scores_raw = await redis_client.zrange(f"game:{game_id}:lb", 0, -1, withscores=True)
        zset_total_score = sum(score for _, score in lb_scores_raw)
        recorded_total_score = sum(r.points_awarded for r in results)

        assert zset_total_score == recorded_total_score, (
            f"Điểm trên ZSET ({zset_total_score}) không khớp tổng điểm đã ghi ({recorded_total_score})!"
        )

        # 6. [TIÊU CHÍ CHỐT 2]: Tổng phân bố = số đáp án chấp nhận
        dist_hash = await redis_client.hgetall(f"game:{game_id}:q:1:dist")
        dist_sum = sum(int(v) for v in dist_hash.values())
        # Vì mỗi người chọn đúng 1 phương án nên tổng số lượt phân bố = 200
        assert dist_sum == num_players
        assert int(dist_hash.get("opt_a", 0)) == 150
        assert (int(dist_hash.get("opt_b", 0)) + int(dist_hash.get("opt_c", 0))) == 50

        # 7. [WRITE-BEHIND STREAM]: Số lượng bản ghi trong Stream answers = 200
        stream_len = await redis_client.xlen(f"game:{game_id}:answers")
        assert stream_len == num_players

        # 8. Hash lưu điểm từng câu (pts) lưu đủ 200 người
        pts_hash = await redis_client.hgetall(f"game:{game_id}:q:1:pts")
        assert len(pts_hash) == num_players

        # 9. Người chơi cuối cùng (người thứ 200) nhận được cờ all_answered = True
        all_answered_flags = [r.all_answered for r in results]
        assert any(flag is True for flag in all_answered_flags)
        assert all_answered_flags.count(True) >= 1
