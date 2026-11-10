"""Xử lý thông điệp WebSocket hai chiều theo WS_PROTOCOL.md (FR-GAME-03..22).

Phân quyền chặt chẽ:
- Lệnh Host (game.start, game.next, game.end, player.kick): Chỉ cho phép client có cờ is_host.
  Player gửi lệnh host bị từ chối bằng envelope error mã FORBIDDEN (FR-GAME-14).
- Lệnh Player (answer.submit): Host không được nộp bài.
  Gọi Lua submit_answer atomic và trả ngay answer.ack (accepted, reason), tuyệt đối không lộ đúng/sai (FR-GAME-07, 09).
- question.start tuyệt đối không chứa đáp án đúng hoặc trường is_correct (FR-GAME-08).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from redis.asyncio import Redis

from app.game import clock, lua_engine
from app.game.enums import WSMessageType
from app.game.schemas import (
    AnswerAckData,
    QuestionOptionPublic,
    QuestionStartData,
    WSEnvelope,
)
from app.realtime.connection_manager import ClientConnection, ConnectionManager
from app.realtime.snapshot import build_game_snapshot

log = logging.getLogger("app.realtime.handlers")


class MessageHandler:
    """Điều phối và xử lý các loại thông điệp WebSocket."""

    def __init__(self, manager: ConnectionManager, redis: Redis) -> None:
        self.manager = manager
        self.redis = redis

    async def handle_message(self, conn: ClientConnection, raw_text: str) -> None:
        """Parse envelope, kiểm tra phiên bản và định tuyến đến hàm xử lý tương ứng."""
        try:
            payload = json.loads(raw_text)
        except Exception:
            await self._send_error(
                conn, code="BAD_MESSAGE", message="Thông điệp không phải JSON hợp lệ"
            )
            return

        if not isinstance(payload, dict):
            await self._send_error(
                conn, code="BAD_MESSAGE", message="Thông điệp phải là JSON Object"
            )
            return

        msg_type = payload.get("type")
        if not msg_type or not isinstance(msg_type, str):
            await self._send_error(
                conn, code="BAD_MESSAGE", message="Thiếu hoặc sai kiểu trường 'type'"
            )
            return

        v = payload.get("v", 1)
        if v != 1:
            await self._send_error(
                conn,
                code="UNSUPPORTED_VERSION",
                message=f"Phiên bản giao thức v={v} không được hỗ trợ",
            )
            return

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            await self._send_error(
                conn, code="BAD_MESSAGE", message="Trường 'data' phải là JSON Object"
            )
            return

        # Định tuyến theo type
        if msg_type == WSMessageType.HEARTBEAT.value:
            await self._handle_heartbeat(conn, data)
        elif msg_type == WSMessageType.GAME_START.value:
            await self._handle_game_start(conn, data)
        elif msg_type == WSMessageType.GAME_NEXT.value:
            await self._handle_game_next(conn, data)
        elif msg_type == WSMessageType.GAME_END.value:
            await self._handle_game_end(conn, data)
        elif msg_type == WSMessageType.PLAYER_KICK.value:
            await self._handle_player_kick(conn, data)
        elif msg_type == WSMessageType.ANSWER_SUBMIT.value:
            await self._handle_answer_submit(conn, data)
        elif msg_type == "snapshot.request":
            await self._handle_snapshot_request(conn)
        else:
            await self._send_error(
                conn, code="BAD_MESSAGE", message=f"Loại thông điệp '{msg_type}' không được hỗ trợ"
            )

    async def _send_error(self, conn: ClientConnection, *, code: str, message: str) -> None:
        """Gửi thông điệp lỗi trực tiếp cho client."""
        env = {
            "type": WSMessageType.ERROR.value,
            "v": 1,
            "data": {
                "code": code,
                "message": message,
            },
        }
        await self.manager.send_personal(conn.ws, env)

    async def _handle_heartbeat(self, conn: ClientConnection, data: dict[str, Any]) -> None:
        """Cập nhật thời gian hoạt động cuối (presence) của client (FR-GAME-18)."""
        await self.redis.set(f"game:{conn.game_id}:presence:{conn.client_id}", "1", ex=45)

    async def _handle_snapshot_request(self, conn: ClientConnection) -> None:
        """Client yêu cầu nhận lại snapshot trạng thái."""
        snap = await build_game_snapshot(
            self.redis,
            game_id=conn.game_id,
            client_id=conn.client_id,
            is_host=conn.is_host,
        )
        await self.manager.send_personal(conn.ws, snap)

    async def _handle_game_start(self, conn: ClientConnection, data: dict[str, Any]) -> None:
        """Host bắt đầu trận đấu: Chuyển LOBBY -> QUESTION_ACTIVE câu 1 (FR-GAME-06, 14)."""
        if not conn.is_host:
            await self._send_error(
                conn, code="FORBIDDEN", message="Chỉ Host mới có quyền bắt đầu trận đấu"
            )
            return

        game_id = conn.game_id

        # 1. Đọc thời gian quy định cho câu hỏi 1
        q1_meta = await self.redis.hgetall(f"game:{game_id}:q:1:meta")  # type: ignore[misc]
        time_limit_s = int(q1_meta.get("time_limit_s", 15)) if q1_meta else 15

        # 2. Gọi Lua transition start atomic
        res = await lua_engine.execute_transition(
            self.redis,
            game_id=game_id,
            action="start",
            time_limit_s=time_limit_s,
        )

        if not res.ok:
            await self._send_error(
                conn,
                code=res.error or "INVALID_STATE",
                message=f"Không thể bắt đầu trận đấu: {res.error}",
            )
            return

        # 3. Lấy nội dung câu hỏi 1 và tạo QuestionStartData (KHÔNG LỘ is_correct - FR-GAME-08)
        question_start = await self._build_question_start_payload(
            game_id=game_id,
            q_index=1,
            server_time=res.server_time,
            deadline=res.deadline,
        )
        if not question_start:
            await self._send_error(
                conn, code="DATA_NOT_FOUND", message="Không tìm thấy dữ liệu câu hỏi 1"
            )
            return

        # 4. Broadcast question.start tới tất cả người chơi trong phòng
        broadcast_envelope = {
            "type": WSMessageType.QUESTION_START.value,
            "v": 1,
            "data": question_start.model_dump(mode="json"),
        }
        await self.manager.broadcast_game(game_id, broadcast_envelope)
        log.info("Host bắt đầu trận đấu %s, phát câu hỏi 1", game_id)

        # 5. Lập lịch Game Clock tự động đóng câu khi hết giờ (FR-GAME-15, ADR-003)
        await clock.schedule_question_deadline_timer(
            self.redis,
            game_id=game_id,
            q_index=1,
            deadline_ms=res.deadline,
        )

    async def _handle_game_next(self, conn: ClientConnection, data: dict[str, Any]) -> None:
        """Host chuyển sang câu hỏi tiếp theo (FR-GAME-14)."""
        if not conn.is_host:
            await self._send_error(
                conn, code="FORBIDDEN", message="Chỉ Host mới có quyền chuyển câu hỏi"
            )
            return

        game_id = conn.game_id
        expected_q = data.get("expected_q_index", 0)

        # 1. Đọc trạng thái hiện tại để biết câu tiếp theo
        state = await self.redis.hgetall(f"game:{game_id}:state")  # type: ignore[misc]
        current_q = int(state.get("q_index", 0))
        next_q = current_q + 1

        next_q_meta = await self.redis.hgetall(f"game:{game_id}:q:{next_q}:meta")  # type: ignore[misc]
        time_limit_s = int(next_q_meta.get("time_limit_s", 15)) if next_q_meta else 15

        # 2. Gọi Lua transition next atomic (chống bấm đúp bằng expected_q)
        res = await lua_engine.execute_transition(
            self.redis,
            game_id=game_id,
            action="next",
            expected_q_index=int(expected_q) if expected_q else next_q,
            time_limit_s=time_limit_s,
        )

        if not res.ok:
            error_code = res.error or "TRANSITION_FAILED"
            msg = "Lệnh chuyển câu bị từ chối"
            if error_code == "VERSION_MISMATCH":
                msg = "Lệnh next bị trùng lặp hoặc sai thứ tự câu (chống bấm đúp)"
            elif error_code == "NO_MORE_QUESTIONS":
                msg = "Đã hết câu hỏi trong bộ đề"
            elif error_code == "INVALID_STATE":
                msg = "Trận đấu đang không ở màn hình kết quả câu hỏi"

            await self._send_error(conn, code=error_code, message=msg)
            return

        # 3. Tạo QuestionStartData cho câu tiếp theo (TUYỆT ĐỐI KHÔNG chứa is_correct)
        question_start = await self._build_question_start_payload(
            game_id=game_id,
            q_index=next_q,
            server_time=res.server_time,
            deadline=res.deadline,
        )
        if not question_start:
            await self._send_error(
                conn, code="DATA_NOT_FOUND", message=f"Không tìm thấy dữ liệu câu hỏi {next_q}"
            )
            return

        # 4. Broadcast question.start
        broadcast_envelope = {
            "type": WSMessageType.QUESTION_START.value,
            "v": 1,
            "data": question_start.model_dump(mode="json"),
        }
        await self.manager.broadcast_game(game_id, broadcast_envelope)
        log.info("Game %s chuyển sang câu hỏi %d", game_id, next_q)

        # 5. Lập lịch Game Clock tự động đóng câu khi hết giờ (FR-GAME-15, ADR-003)
        await clock.schedule_question_deadline_timer(
            self.redis,
            game_id=game_id,
            q_index=next_q,
            deadline_ms=res.deadline,
        )

    async def _handle_game_end(self, conn: ClientConnection, data: dict[str, Any]) -> None:
        """Host kết thúc trận đấu sớm (FR-GAME-14)."""
        if not conn.is_host:
            await self._send_error(
                conn, code="FORBIDDEN", message="Chỉ Host mới có quyền kết thúc trận đấu"
            )
            return

        game_id = conn.game_id
        res = await lua_engine.execute_transition(self.redis, game_id=game_id, action="end")
        if not res.ok and res.status != "finished":
            await self._send_error(
                conn, code=res.error or "INVALID_STATE", message="Không thể kết thúc trận đấu"
            )
            return

        # Lấy top 3 Podium và tổng số người chơi
        top_players = await self.redis.zrevrange(  # type: ignore[misc]
            f"game:{game_id}:lb", 0, 2, withscores=True
        )
        podium: list[dict[str, Any]] = []
        for idx, (pid_str, score_val) in enumerate(top_players, start=1):
            nick = (
                await self.redis.hget(f"game:{game_id}:nicknames", pid_str)  # type: ignore[misc]
                or "Người chơi"
            )
            podium.append(
                {
                    "rank": idx,
                    "player_id": pid_str,
                    "nickname": nick,
                    "score": int(score_val),
                    "streak": 0,
                }
            )
        total_p = await self.redis.zcard(f"game:{game_id}:lb")  # type: ignore[misc]

        broadcast_envelope = {
            "type": WSMessageType.GAME_FINISHED.value,
            "v": 1,
            "data": {
                "game_id": game_id,
                "podium": podium,
                "total_players": total_p,
            },
        }
        await self.manager.broadcast_game(game_id, broadcast_envelope)
        log.info("Host đã kết thúc trận đấu %s, phát game.finished", game_id)

    async def _handle_player_kick(self, conn: ClientConnection, data: dict[str, Any]) -> None:
        """Host loại một người chơi khỏi trận đấu (FR-GAME-05)."""
        if not conn.is_host:
            await self._send_error(
                conn, code="FORBIDDEN", message="Chỉ Host mới có quyền loại người chơi"
            )
            return

        game_id = conn.game_id
        target_pid = data.get("player_id")
        if not target_pid:
            await self._send_error(conn, code="BAD_MESSAGE", message="Thiếu 'player_id'")
            return

        target_pid_str = str(target_pid)

        # 1. Tra cứu nickname người chơi
        nickname = await self.redis.hget(f"game:{game_id}:nicknames", target_pid_str)  # type: ignore[misc]

        # 2. Thiết lập cấm tham gia lại trong 10 phút (600 giây - GM-2)
        pipe = self.redis.pipeline()
        pipe.set(f"game:{game_id}:kicked_ids:{target_pid_str}", "1", ex=600)
        if nickname:
            nick_lower = nickname.lower()
            pipe.set(f"game:{game_id}:kicked:{nick_lower}", "1", ex=600)
            pipe.hdel(f"game:{game_id}:players", nick_lower)
        pipe.zrem(f"game:{game_id}:lb", target_pid_str)
        await pipe.execute()

        # 3. Phát lệnh kick tới toàn bộ replica (ngắt kết nối WebSocket người đó với mã 4002)
        await self.manager.kick_player_cluster(game_id, target_pid_str)

        # 4. Cập nhật và phát lobby.update cho những người còn lại
        players_raw = await self.redis.hgetall(f"game:{game_id}:players")  # type: ignore[misc]
        player_count = len(players_raw)

        update_envelope = {
            "type": WSMessageType.LOBBY_UPDATE.value,
            "v": 1,
            "data": {
                "game_id": game_id,
                "player_count": player_count,
                "kicked_player_id": target_pid_str,
            },
        }
        await self.manager.broadcast_game(game_id, update_envelope)
        log.info("Host kick người chơi %s khỏi game %s", target_pid_str, game_id)

    async def _handle_answer_submit(self, conn: ClientConnection, data: dict[str, Any]) -> None:
        """Người chơi nộp đáp án câu hỏi (FR-GAME-07, 09, 13).

        Quy tắc nghiêm ngặt:
        - Host không được nộp bài.
        - Gọi Lua submit_answer atomic (kiểm tra trạng thái, deadline, idempotency).
        - Trả ngay answer.ack cho client.
        - TUYỆT ĐỐI KHÔNG BÁO ĐÚNG/SAI Ở BƯỚC NÀY.
        """
        if conn.is_host:
            await self._send_error(conn, code="FORBIDDEN", message="Host không tham gia nộp bài")
            return

        game_id = conn.game_id
        player_id = conn.client_id

        # Hỗ trợ cả q_index và question_index
        q_index_raw = data.get("q_index") if "q_index" in data else data.get("question_index")
        if q_index_raw is None:
            await self._send_error(conn, code="BAD_MESSAGE", message="Thiếu 'q_index'")
            return

        try:
            q_index = int(q_index_raw)
        except ValueError:
            await self._send_error(conn, code="BAD_MESSAGE", message="'q_index' phải là số nguyên")
            return

        # Hỗ trợ cả selected_option_ids và option_ids
        selected_options = data.get("selected_option_ids") or data.get("option_ids") or []
        if isinstance(selected_options, (str, int)):
            selected_options = [str(selected_options)]
        elif not isinstance(selected_options, list):
            await self._send_error(
                conn, code="BAD_MESSAGE", message="'selected_option_ids' phải là danh sách"
            )
            return

        # Thực thi kịch bản chấm điểm và ghi nhận atomic trên Redis
        res = await lua_engine.execute_submit_answer(
            self.redis,
            game_id=game_id,
            player_id=player_id,
            q_index=q_index,
            selected_option_ids=[str(opt) for opt in selected_options],
        )

        # Trả về answer.ack xác nhận đã ghi nhận (hoặc lý do từ chối)
        # Tuyệt đối KHÔNG chứa điểm số hay is_correct (FR-GAME-07, 09)
        ack_data = AnswerAckData(
            accepted=res.accepted,
            q_index=q_index,
            reason=res.error_code,
        )

        ack_envelope = WSEnvelope(
            type=WSMessageType.ANSWER_ACK.value,
            v=1,
            data=ack_data.model_dump(mode="json"),
        )
        await self.manager.send_personal(conn.ws, ack_envelope.model_dump(mode="json"))

        # Nếu tất cả người chơi trong phòng đã nộp bài (all_answered) -> đóng câu hỏi sớm ngay lập tức!
        if res.all_answered:
            log.info(
                "Tất cả người chơi đã nộp bài câu %d trận %s -> kích hoạt đóng sớm (FR-GAME-15)",
                q_index,
                game_id,
            )
            await clock.close_and_broadcast_question(
                self.redis,
                game_id=game_id,
                q_index=q_index,
            )

    async def _build_question_start_payload(
        self,
        *,
        game_id: str,
        q_index: int,
        server_time: int,
        deadline: int,
    ) -> QuestionStartData | None:
        """Đọc Quiz snapshot và dựng QuestionStartData KHÔNG CHỨA is_correct (FR-GAME-08)."""
        raw_quiz = await self.redis.get(f"game:{game_id}:quiz")
        if not raw_quiz:
            return None

        quiz = json.loads(raw_quiz)
        questions = quiz.get("questions", [])
        if not (1 <= q_index <= len(questions)):
            return None

        q_data = questions[q_index - 1]
        public_options: list[QuestionOptionPublic] = []
        for opt in q_data.get("options", []):
            # Tuyệt đối chỉ lấy id, position, content. Không bao giờ gửi is_correct
            public_options.append(
                QuestionOptionPublic(
                    id=uuid.UUID(opt["id"]),
                    position=int(opt["position"]),
                    content=str(opt["content"]),
                )
            )

        return QuestionStartData(
            q_index=q_index,
            total_questions=len(questions),
            question_id=uuid.UUID(q_data["id"]),
            content=str(q_data["content"]),
            type=str(q_data.get("type", "single")),
            points=int(q_data.get("points", 1000)),
            time_limit_s=int(q_data.get("time_limit_s", 15)),
            server_time=server_time,
            deadline=deadline,
            options=public_options,
        )
