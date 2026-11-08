"""Service nghiệp vụ quản lý vòng đời trận đấu Live Game (GM-1, GM-2, GM-13)."""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.errors import AppError
from app.game.enums import GameStatus, WSMessageType
from app.models.game import GamePlayer, GameSession
from app.models.quiz import Quiz
from app.models.user import User
from app.repositories import game_repository, quiz_repository, user_repository

log = logging.getLogger("app.game.service")

MAX_PLAYERS_PER_GAME = 500
MAX_CONCURRENT_GAMES_PER_HOST = 20
PIN_TTL_SECONDS = 10800  # 3 giờ
KICK_BAN_SECONDS = 600  # 10 phút

BAD_WORDS = {
    "admin",
    "root",
    "system",
    "moderator",
    "fuck",
    "shit",
    "bitch",
    "asshole",
    "dkm",
    "vcl",
    "đm",
    "dm",
    "cl",
    "vl",
    "lồn",
    "buồi",
    "cặc",
}


def _is_offensive_nickname(nickname: str) -> bool:
    """Kiểm tra biệt danh có chứa từ ngữ cấm hoặc mạo danh hệ thống (GM-2)."""
    norm = nickname.lower().replace(" ", "").replace("_", "").replace("-", "")
    for bad in BAD_WORDS:
        if bad in norm:
            return True
    return False


def _build_quiz_snapshot(quiz: Quiz) -> dict[str, Any]:
    """Tạo snapshot JSON bất biến cho toàn bộ nội dung Quiz (QZ-4, GM-1)."""
    questions_list = []
    # Sắp xếp câu hỏi theo position tăng dần
    sorted_questions = sorted(quiz.questions, key=lambda q: q.position)

    for q in sorted_questions:
        sorted_options = sorted(q.options, key=lambda opt: opt.position)
        options_list = [
            {
                "id": str(opt.id),
                "position": opt.position,
                "content": opt.content,
                "is_correct": bool(opt.is_correct),
            }
            for opt in sorted_options
        ]
        questions_list.append(
            {
                "id": str(q.id),
                "position": q.position,
                "type": q.type,
                "content": q.content,
                "points": q.points,
                "time_limit_s": q.time_limit_s,
                "explanation": q.explanation,
                "options": options_list,
            }
        )

    return {
        "quiz_id": str(quiz.id),
        "title": quiz.title,
        "description": quiz.description,
        "version": quiz.version,
        "total_questions": len(questions_list),
        "questions": questions_list,
    }


async def create_game(
    session: AsyncSession,
    redis: Redis,
    *,
    host_id: uuid.UUID,
    quiz_id: uuid.UUID,
    strategy_id: str = "standard",
) -> GameSession:
    """Tạo một trận đấu live mới từ Quiz đã xuất bản (GM-1, GM-13)."""
    # 1. Kiểm tra Quiz thuộc sở hữu của Host
    quiz = await quiz_repository.get_by_id(session, quiz_id, include_questions=True)
    if not quiz or quiz.owner_id != host_id:
        raise AppError("Không tìm thấy bộ Quiz", code="QUIZ_NOT_FOUND", status_code=404)

    # 2. Kiểm tra điều kiện xuất bản
    if quiz.status != "published":
        raise AppError(
            "Chỉ có thể mở trận đấu từ Quiz đã được xuất bản",
            code="QUIZ_NOT_PUBLISHED",
            status_code=400,
        )

    if not quiz.questions or len(quiz.questions) < 3:
        raise AppError(
            "Quiz phải có ít nhất 3 câu hỏi hợp lệ để tổ chức trận đấu (QZ-3)",
            code="INSUFFICIENT_QUESTIONS",
            status_code=400,
        )

    # 3. Kiểm tra giới hạn 20 trận đồng thời/host (GM-13)
    active_count = await game_repository.count_active_sessions_by_host(session, host_id)
    if active_count >= MAX_CONCURRENT_GAMES_PER_HOST:
        raise AppError(
            f"Host chỉ được phép mở tối đa {MAX_CONCURRENT_GAMES_PER_HOST} trận đấu đồng thời",
            code="TOO_MANY_CONCURRENT_GAMES",
            status_code=400,
        )

    # 4. Sinh mã PIN 6 số ngẫu nhiên duy nhất trong các trận đang mở (Redis SET NX)
    game_id = uuid.uuid4()
    allocated_pin: str | None = None

    for _ in range(15):
        candidate_pin = f"{random.randint(100000, 999999)}"
        # SET pin:{pin} {game_id} NX EX 10800
        ok = await redis.set(
            f"pin:{candidate_pin}",
            str(game_id),
            nx=True,
            ex=PIN_TTL_SECONDS,
        )
        if ok:
            allocated_pin = candidate_pin
            break

    if not allocated_pin:
        raise AppError(
            "Không thể cấp mã PIN cho trận đấu, vui lòng thử lại",
            code="PIN_ALLOCATION_FAILED",
            status_code=500,
        )

    # 5. Tạo Quiz Snapshot bất biến
    snapshot = _build_quiz_snapshot(quiz)

    # 6. Lưu vào PostgreSQL
    now = datetime.now(UTC)
    game = GameSession(
        id=game_id,
        quiz_id=quiz.id,
        quiz_version=quiz.version,
        host_id=host_id,
        pin=allocated_pin,
        status=GameStatus.LOBBY.value,
        strategy_id=strategy_id,
        quiz_snapshot=snapshot,
        created_at=now,
    )
    await game_repository.create_session(session, game)

    # 7. Khởi tạo trạng thái và snapshot trên Redis
    pipe = redis.pipeline()

    # Hash trạng thái trận
    state_key = f"game:{game_id}:state"
    pipe.hset(
        state_key,
        mapping={
            "game_id": str(game_id),
            "pin": allocated_pin,
            "host_id": str(host_id),
            "quiz_id": str(quiz.id),
            "quiz_title": quiz.title,
            "quiz_version": str(quiz.version),
            "status": GameStatus.LOBBY.value,
            "strategy_id": strategy_id,
            "q_index": "0",
            "total_questions": str(len(snapshot["questions"])),
            "deadline": "0",
            "deadline_version": "0",
        },
    )
    pipe.expire(state_key, PIN_TTL_SECONDS)

    # Quiz snapshot JSON trên Redis (chỉ server đọc)
    quiz_key = f"game:{game_id}:quiz"
    pipe.set(quiz_key, json.dumps(snapshot), ex=PIN_TTL_SECONDS)

    # Bóc tách sẵn đáp án đúng của từng câu vào Set để Lua check atomic không cần parse JSON
    for idx, q_item in enumerate(snapshot["questions"], start=1):
        correct_opts = [opt["id"] for opt in q_item["options"] if opt["is_correct"]]
        correct_key = f"game:{game_id}:q:{idx}:correct_keys"
        pipe.delete(correct_key)
        if correct_opts:
            pipe.sadd(correct_key, *correct_opts)
        pipe.expire(correct_key, PIN_TTL_SECONDS)

        # Hash lưu metadata câu hỏi: points, time_limit_s, type
        meta_key = f"game:{game_id}:q:{idx}:meta"
        pipe.hset(
            meta_key,
            mapping={
                "id": q_item["id"],
                "points": str(q_item["points"]),
                "time_limit_s": str(q_item["time_limit_s"]),
                "type": q_item["type"],
            },
        )
        pipe.expire(meta_key, PIN_TTL_SECONDS)

    await pipe.execute()
    log.info("Khởi tạo trận đấu thành công: id=%s pin=%s host=%s", game_id, allocated_pin, host_id)
    return game


async def get_game_detail(
    session: AsyncSession,
    redis: Redis,
    game_id: uuid.UUID,
) -> dict[str, Any]:
    """Lấy thông tin chi tiết một trận đấu."""
    game = await game_repository.get_session_by_id(session, game_id)
    if not game:
        raise AppError("Không tìm thấy trận đấu", code="GAME_NOT_FOUND", status_code=404)

    # Tra cứu Redis để có số người chơi và index câu hỏi realtime
    state = await redis.hgetall(f"game:{game_id}:state")  # type: ignore[misc]
    current_q_index = int(state.get("q_index", 0)) if state else 0
    player_count = await redis.hlen(f"game:{game_id}:players")  # type: ignore[misc]

    return {
        "game_id": game.id,
        "pin": game.pin,
        "status": state.get("status", game.status) if state else game.status,
        "quiz_id": game.quiz_id,
        "quiz_title": game.quiz_snapshot.get("title", ""),
        "host_id": game.host_id,
        "total_questions": game.quiz_snapshot.get("total_questions", 0),
        "current_question_index": current_q_index,
        "player_count": player_count,
        "created_at": game.created_at,
        "started_at": game.started_at,
        "ended_at": game.ended_at,
    }


async def join_game(
    session: AsyncSession,
    redis: Redis,
    *,
    pin: str,
    nickname: str,
    current_user: User | None = None,
    current_token: str | None = None,
    client_ip: str = "127.0.0.1",
) -> tuple[str, str, uuid.UUID, uuid.UUID, str]:
    """Người chơi tham gia phòng chờ bằng mã PIN (GM-2, AU-9, AU-10).

    Trả về:
        (token, token_type, game_id, player_id, nickname)
    """
    clean_nickname = nickname.strip()
    if len(clean_nickname) < 2 or len(clean_nickname) > 20:
        raise AppError(
            "Biệt danh phải có độ dài từ 2 đến 20 ký tự",
            code="INVALID_NICKNAME",
            status_code=400,
        )

    if _is_offensive_nickname(clean_nickname):
        raise AppError(
            "Biệt danh chứa từ ngữ không phù hợp hoặc vi phạm chính sách",
            code="INVALID_NICKNAME",
            status_code=400,
        )

    # 1. Tra cứu game_id từ PIN
    game_id_str = await redis.get(f"pin:{pin}")
    if not game_id_str:
        raise AppError(
            "Mã PIN không tồn tại hoặc trận đấu đã kết thúc", code="GAME_NOT_FOUND", status_code=404
        )

    game_id = uuid.UUID(game_id_str)

    # 2. Kiểm tra trạng thái trận
    state = await redis.hgetall(f"game:{game_id}:state")  # type: ignore[misc]
    status = state.get("status", GameStatus.LOBBY.value) if state else GameStatus.LOBBY.value
    nick_lower = clean_nickname.lower()

    # 3. Kiểm tra xem người này có bị Host kick trước đó không (chặn 10 phút, GM-2)
    is_kicked = await redis.exists(f"game:{game_id}:kicked:{nick_lower}")
    if is_kicked:
        raise AppError(
            "Bạn đã bị Host loại khỏi trận đấu này và không thể tham gia lại trong 10 phút",
            code="KICKED_BAN",
            status_code=403,
        )

    # 4. Kiểm tra nickname đã tồn tại trong trận chưa
    players_key = f"game:{game_id}:players"
    existing_player_raw = await redis.hget(players_key, nick_lower)  # type: ignore[misc]

    if existing_player_raw:
        # Nếu đã có người dùng nickname này:
        existing_info = json.loads(existing_player_raw)
        # Nếu chính user đã đăng nhập này tham gia lại -> Cho phép tái sử dụng (reconnect)
        if current_user and existing_info.get("user_id") == str(current_user.id):
            player_id = uuid.UUID(existing_info["id"])
            roles = await user_repository.get_roles(session, current_user.id)
            perms = await user_repository.get_permissions(session, current_user.id)
            if "game:play" not in perms:
                perms.append("game:play")
            token = security.create_access_token(
                user_id=current_user.id,
                roles=roles,
                permissions=perms,
                token_version=current_user.token_version,
            )
            return token, "player", game_id, player_id, clean_nickname

        # Ngược lại, báo lỗi trùng nickname
        raise AppError(
            f"Biệt danh '{clean_nickname}' đã được sử dụng trong trận này. Vui lòng chọn tên khác.",
            code="NICKNAME_TAKEN",
            status_code=409,
        )

    # 5. Nếu trận đã bắt đầu (không còn ở lobby) và không phải người cũ kết nối lại -> Từ chối
    if status != GameStatus.LOBBY.value:
        raise AppError(
            "Trận đấu đã bắt đầu, phòng không nhận thêm người chơi mới",
            code="GAME_ALREADY_STARTED",
            status_code=400,
        )

    # 6. Kiểm tra giới hạn 500 người chơi (GM-13)
    player_count = await redis.hlen(players_key)  # type: ignore[misc]
    if player_count >= MAX_PLAYERS_PER_GAME:
        raise AppError(
            f"Trận đấu đã đạt giới hạn tối đa {MAX_PLAYERS_PER_GAME} người chơi",
            code="GAME_FULL",
            status_code=400,
        )

    # 7. Tạo người chơi mới trong PostgreSQL (bảo vệ bằng UNIQUE session_id, nickname)
    player_id = uuid.uuid4()
    player = GamePlayer(
        id=player_id,
        session_id=game_id,
        user_id=current_user.id if current_user else None,
        guest_id=str(player_id) if not current_user else None,
        nickname=clean_nickname,
        joined_at=datetime.now(UTC),
    )
    await game_repository.create_player(session, player)

    # 8. Lưu người chơi vào Redis
    player_data = {
        "id": str(player_id),
        "nickname": clean_nickname,
        "user_id": str(current_user.id) if current_user else None,
        "online": True,
        "streak": 0,
        "score": 0,
        "joined_at": datetime.now(UTC).isoformat(),
    }

    pipe = redis.pipeline()
    # Lưu vào hash người chơi
    pipe.hset(players_key, nick_lower, json.dumps(player_data))
    pipe.expire(players_key, PIN_TTL_SECONDS)
    # Khởi tạo điểm trên Leaderboard ZSET: 0 điểm
    pipe.zadd(f"game:{game_id}:lb", {str(player_id): 0})
    pipe.expire(f"game:{game_id}:lb", PIN_TTL_SECONDS)
    # Lưu mapping player_id -> nickname
    pipe.hset(f"game:{game_id}:nicknames", str(player_id), clean_nickname)
    pipe.expire(f"game:{game_id}:nicknames", PIN_TTL_SECONDS)
    await pipe.execute()

    # 9. Phát thông báo realtime lobby.update qua Redis Pub/Sub
    lobby_msg = {
        "type": WSMessageType.LOBBY_UPDATE.value,
        "v": 1,
        "data": {
            "game_id": str(game_id),
            "pin": pin,
            "player_count": player_count + 1,
            "new_player": {
                "player_id": str(player_id),
                "nickname": clean_nickname,
            },
        },
    }
    await redis.publish(f"game:{game_id}", json.dumps(lobby_msg))

    # 10. Tạo Token phù hợp (AU-9: Guest token sống 3 giờ, AU-10: giữ access token nếu đã có)
    if current_user:
        if current_token:
            return current_token, "player", game_id, player_id, clean_nickname
        roles = await user_repository.get_roles(session, current_user.id)
        perms = await user_repository.get_permissions(session, current_user.id)
        if "game:play" not in perms:
            perms.append("game:play")
        token = security.create_access_token(
            user_id=current_user.id,
            roles=roles,
            permissions=perms,
            token_version=current_user.token_version,
        )
        return token, "player", game_id, player_id, clean_nickname

    # Guest Token
    token = security.create_guest_token(game_id=game_id, guest_id=player_id)
    return token, "guest", game_id, player_id, clean_nickname
