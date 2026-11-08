"""Kiểm thử luồng Trận đấu Live Game (Thứ Hai): Tạo trận, Kiểm tra giới hạn, Snapshot Redis và 5 Khách Join phòng.

Tuân thủ nghiêm ngặt yêu cầu Phase 3:
- GM-1: POST /games tạo trận (PIN 6 số qua SET NX, snapshot quiz lưu Postgres + Redis, bóc tách sẵn correct_keys vào Set để Lua check atomic không parse JSON).
- GM-13: Giới hạn tối đa 20 trận đồng thời/host.
- GM-2: POST /games/join tham gia phòng chờ bằng PIN (nickname 2-20 ký tự, lọc từ cấm, unique nickname trong trận, chặn kick 10 phút, cap 500 khách, cấp guest token 3 giờ hoặc gắn user đã đăng nhập, khởi tạo Leaderboard ZSET điểm 0, publish lobby.update qua Redis Pub/Sub).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_redis, get_session
from app.core import security
from app.core.rbac import PermissionCode, RoleName
from app.game.enums import GameStatus, WSMessageType
from app.main_api import app
from app.models.game import GamePlayer, GameSession
from app.models.quiz import Question, QuestionOption, Quiz
from app.models.user import User

# =========================================================================
# In-memory Mock Redis hỗ trợ pipeline, string, hash, set, zset, pubsub
# =========================================================================


class MemoryPipeline:
    def __init__(self, redis_client: MemoryRedis) -> None:
        self.redis = redis_client
        self.commands: list[tuple[Any, tuple, dict]] = []

    def set(self, key: str, value: str, *args: Any, **kwargs: Any) -> MemoryPipeline:
        self.commands.append((self.redis.set, (key, value, *args), kwargs))
        return self

    def hset(self, key: str, *args: Any, **kwargs: Any) -> MemoryPipeline:
        self.commands.append((self.redis.hset, (key, *args), kwargs))
        return self

    def delete(self, *keys: str) -> MemoryPipeline:
        self.commands.append((self.redis.delete, keys, {}))
        return self

    def expire(self, key: str, seconds: int) -> MemoryPipeline:
        self.commands.append((self.redis.expire, (key, seconds), {}))
        return self

    def sadd(self, key: str, *members: str) -> MemoryPipeline:
        self.commands.append((self.redis.sadd, (key, *members), {}))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> MemoryPipeline:
        self.commands.append((self.redis.zadd, (key, mapping), {}))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for func, args, kwargs in self.commands:
            res = await func(*args, **kwargs)
            results.append(res)
        self.commands.clear()
        return results


class MemoryRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.published: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.strings:
            return False
        self.strings[key] = str(value)
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.strings:
                del self.strings[k]
                count += 1
            if k in self.hashes:
                del self.hashes[k]
                count += 1
            if k in self.sets:
                del self.sets[k]
                count += 1
            if k in self.zsets:
                del self.zsets[k]
                count += 1
        return count

    async def exists(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self.strings or k in self.hashes or k in self.sets or k in self.zsets:
                count += 1
        return count

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def hset(
        self,
        key: str,
        key_or_name: str | None = None,
        value: str | None = None,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        if key not in self.hashes:
            self.hashes[key] = {}
        if mapping:
            for k, v in mapping.items():
                self.hashes[key][k] = str(v)
            return len(mapping)
        if key_or_name is not None and value is not None:
            self.hashes[key][key_or_name] = str(value)
            return 1
        return 0

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hlen(self, key: str) -> int:
        return len(self.hashes.get(key, {}))

    async def sadd(self, key: str, *members: str) -> int:
        if key not in self.sets:
            self.sets[key] = set()
        prev = len(self.sets[key])
        self.sets[key].update(str(m) for m in members)
        return len(self.sets[key]) - prev

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        if key not in self.zsets:
            self.zsets[key] = {}
        for member, score in mapping.items():
            self.zsets[key][str(member)] = float(score)
        return len(mapping)

    async def zscore(self, key: str, member: str) -> float | None:
        return self.zsets.get(key, {}).get(str(member))

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    def pipeline(self) -> MemoryPipeline:
        return MemoryPipeline(self)


# =========================================================================
# In-memory Repository & Fake DB
# =========================================================================

quizzes_db: dict[uuid.UUID, Quiz] = {}
games_db: dict[uuid.UUID, GameSession] = {}
players_db: dict[uuid.UUID, GamePlayer] = {}
users_db: dict[uuid.UUID, User] = {}
memory_redis = MemoryRedis()


class FakeSession:
    """Giả lập AsyncSession của SQLAlchemy với async with session.begin()."""

    def begin(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _ctx():
            yield self

        return _ctx()

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Any, attrs: Any = None) -> None:
        pass

    def add(self, obj: Any) -> None:
        if isinstance(obj, GameSession):
            games_db[obj.id] = obj
        elif isinstance(obj, GamePlayer):
            players_db[obj.id] = obj

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


async def fake_get_session() -> AsyncIterator[FakeSession]:
    yield FakeSession()


async def fake_get_redis() -> MemoryRedis:
    return memory_redis


client = TestClient(app)


# Mock repositories
async def mock_get_quiz_by_id(
    session: Any, quiz_id: uuid.UUID, include_questions: bool = True
) -> Quiz | None:
    return quizzes_db.get(quiz_id)


async def mock_count_active_sessions_by_host(session: Any, host_id: uuid.UUID) -> int:
    active_statuses = (
        GameStatus.LOBBY.value,
        GameStatus.QUESTION_ACTIVE.value,
        GameStatus.QUESTION_RESULT.value,
    )
    return sum(1 for g in games_db.values() if g.host_id == host_id and g.status in active_statuses)


async def mock_create_session(session: Any, game: GameSession) -> GameSession:
    games_db[game.id] = game
    return game


async def mock_get_session_by_id(session: Any, session_id: uuid.UUID) -> GameSession | None:
    return games_db.get(session_id)


async def mock_create_player(session: Any, player: GamePlayer) -> GamePlayer:
    players_db[player.id] = player
    return player


async def mock_count_players_in_session(session: Any, session_id: uuid.UUID) -> int:
    return sum(1 for p in players_db.values() if p.session_id == session_id)


async def mock_get_user_by_id(session: Any, user_id: uuid.UUID) -> User | None:
    return users_db.get(user_id)


async def mock_get_user_roles(session: Any, user_id: uuid.UUID) -> list[str]:
    return [RoleName.PLAYER.value]


async def mock_get_user_permissions(session: Any, user_id: uuid.UUID) -> list[str]:
    return [PermissionCode.GAME_PLAY.value]


@pytest.fixture(autouse=True)
def _setup_mock_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    quizzes_db.clear()
    games_db.clear()
    players_db.clear()
    users_db.clear()
    memory_redis.strings.clear()
    memory_redis.hashes.clear()
    memory_redis.sets.clear()
    memory_redis.zsets.clear()
    memory_redis.published.clear()

    app.dependency_overrides[get_session] = fake_get_session
    app.dependency_overrides[get_redis] = fake_get_redis

    from app.repositories import game_repository, quiz_repository, user_repository

    monkeypatch.setattr(quiz_repository, "get_by_id", mock_get_quiz_by_id)
    monkeypatch.setattr(
        game_repository, "count_active_sessions_by_host", mock_count_active_sessions_by_host
    )
    monkeypatch.setattr(game_repository, "create_session", mock_create_session)
    monkeypatch.setattr(game_repository, "get_session_by_id", mock_get_session_by_id)
    monkeypatch.setattr(game_repository, "create_player", mock_create_player)
    monkeypatch.setattr(game_repository, "count_players_in_session", mock_count_players_in_session)
    monkeypatch.setattr(user_repository, "get_by_id", mock_get_user_by_id)
    monkeypatch.setattr(user_repository, "get_roles", mock_get_user_roles)
    monkeypatch.setattr(user_repository, "get_permissions", mock_get_user_permissions)


# Helper tạo Quiz hợp lệ với 3 câu hỏi
def create_sample_quiz(
    owner_id: uuid.UUID, status: str = "published", num_questions: int = 3
) -> Quiz:
    quiz_id = uuid.uuid4()
    quiz = Quiz(
        id=quiz_id,
        owner_id=owner_id,
        title="Đấu trường Tri thức",
        description="Quiz test live game",
        status=status,
        version=1,
    )
    quiz.questions = []
    for i in range(1, num_questions + 1):
        q = Question(
            id=uuid.uuid4(),
            quiz_id=quiz_id,
            position=i,
            type="single" if i % 2 != 0 else "multiple",
            content=f"Câu hỏi số {i} là gì?",
            points=1000,
            time_limit_s=15,
            explanation=f"Giải thích câu {i}",
        )
        opt1 = QuestionOption(
            id=uuid.uuid4(), question_id=q.id, position=1, content="A", is_correct=True
        )
        opt2 = QuestionOption(
            id=uuid.uuid4(), question_id=q.id, position=2, content="B", is_correct=False
        )
        opt3 = QuestionOption(
            id=uuid.uuid4(), question_id=q.id, position=3, content="C", is_correct=(i % 2 == 0)
        )
        q.options = [opt1, opt2, opt3]
        quiz.questions.append(q)

    quizzes_db[quiz_id] = quiz
    return quiz


def make_host_token(host_id: uuid.UUID) -> str:
    return security.create_access_token(
        user_id=host_id,
        roles=[RoleName.HOST.value],
        permissions=[PermissionCode.GAME_HOST_OWN.value, PermissionCode.QUIZ_WRITE_OWN.value],
        token_version=1,
    )


# =========================================================================
# Tests: POST /api/v1/games
# =========================================================================


class TestCreateGameEndpoint:
    """Kiểm thử API tạo trận đấu."""

    def test_create_game_success(self) -> None:
        host_id = uuid.uuid4()
        quiz = create_sample_quiz(host_id, status="published", num_questions=3)
        token = make_host_token(host_id)

        response = client.post(
            "/api/v1/games",
            json={"quiz_id": str(quiz.id), "strategy_id": "standard"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert "game_id" in data
        assert "pin" in data
        assert len(data["pin"]) == 6
        assert data["status"] == "lobby"
        assert data["total_questions"] == 3
        assert data["strategy_id"] == "standard"

        game_id = data["game_id"]
        pin = data["pin"]

        # Kiểm tra các key khởi tạo trên Redis
        # 1. pin:{pin} -> game_id
        assert memory_redis.strings.get(f"pin:{pin}") == game_id

        # 2. game:{id}:state
        state = memory_redis.hashes.get(f"game:{game_id}:state")
        assert state is not None
        assert state["pin"] == pin
        assert state["status"] == "lobby"
        assert state["total_questions"] == "3"
        assert state["host_id"] == str(host_id)

        # 3. game:{id}:quiz
        quiz_json = memory_redis.strings.get(f"game:{game_id}:quiz")
        assert quiz_json is not None
        snapshot = json.loads(quiz_json)
        assert len(snapshot["questions"]) == 3

        # 4. game:{id}:q:{n}:correct_keys được bóc tách sẵn vào Set (Lua atomic)
        # Câu 1 (single): opt1 is_correct
        q1_opts = snapshot["questions"][0]["options"]
        correct_opt_id_1 = str(q1_opts[0]["id"])
        assert memory_redis.sets.get(f"game:{game_id}:q:1:correct_keys") == {correct_opt_id_1}

        # 5. game:{id}:q:{n}:meta
        meta_1 = memory_redis.hashes.get(f"game:{game_id}:q:1:meta")
        assert meta_1 is not None
        assert meta_1["points"] == "1000"
        assert meta_1["time_limit_s"] == "15"

    def test_create_game_quiz_not_owned_by_host(self) -> None:
        host_a = uuid.uuid4()
        host_b = uuid.uuid4()
        quiz = create_sample_quiz(host_a, status="published")
        token_b = make_host_token(host_b)

        response = client.post(
            "/api/v1/games",
            json={"quiz_id": str(quiz.id)},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "QUIZ_NOT_FOUND"

    def test_create_game_quiz_not_published(self) -> None:
        host_id = uuid.uuid4()
        quiz = create_sample_quiz(host_id, status="draft")
        token = make_host_token(host_id)

        response = client.post(
            "/api/v1/games",
            json={"quiz_id": str(quiz.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "QUIZ_NOT_PUBLISHED"

    def test_create_game_insufficient_questions(self) -> None:
        host_id = uuid.uuid4()
        quiz = create_sample_quiz(host_id, status="published", num_questions=2)
        token = make_host_token(host_id)

        response = client.post(
            "/api/v1/games",
            json={"quiz_id": str(quiz.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INSUFFICIENT_QUESTIONS"

    def test_create_game_concurrent_limit_reached(self) -> None:
        host_id = uuid.uuid4()
        quiz = create_sample_quiz(host_id, status="published")
        token = make_host_token(host_id)

        # Giả lập host này đã có 20 trận đang active
        for _ in range(20):
            gid = uuid.uuid4()
            games_db[gid] = GameSession(
                id=gid,
                quiz_id=quiz.id,
                quiz_version=1,
                host_id=host_id,
                pin="123456",
                status=GameStatus.LOBBY.value,
                strategy_id="standard",
                quiz_snapshot={},
            )

        response = client.post(
            "/api/v1/games",
            json={"quiz_id": str(quiz.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "TOO_MANY_CONCURRENT_GAMES"


# =========================================================================
# Tests: GET /api/v1/games/{id}
# =========================================================================


class TestGetGameDetailEndpoint:
    """Kiểm thử API tra cứu chi tiết trận đấu."""

    def test_get_game_detail_success(self) -> None:
        host_id = uuid.uuid4()
        quiz = create_sample_quiz(host_id, status="published")
        token = make_host_token(host_id)

        res = client.post(
            "/api/v1/games",
            json={"quiz_id": str(quiz.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        game_id = res.json()["game_id"]

        detail_res = client.get(f"/api/v1/games/{game_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["game_id"] == game_id
        assert detail["status"] == "lobby"
        assert detail["player_count"] == 0

    def test_get_game_detail_not_found(self) -> None:
        random_id = uuid.uuid4()
        res = client.get(f"/api/v1/games/{random_id}")
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "GAME_NOT_FOUND"


# =========================================================================
# Tests: POST /api/v1/games/join
# =========================================================================


class TestJoinGameEndpoint:
    """Kiểm thử API tham gia phòng chờ bằng PIN cho 5 khách (Cuối ngày Thứ Hai)."""

    def _setup_open_game(self) -> tuple[str, str]:
        host_id = uuid.uuid4()
        quiz = create_sample_quiz(host_id, status="published")
        token = make_host_token(host_id)
        res = client.post(
            "/api/v1/games",
            json={"quiz_id": str(quiz.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        data = res.json()
        return data["game_id"], data["pin"]

    def test_5_guests_join_successfully_and_verify_redis_state(self) -> None:
        game_id, pin = self._setup_open_game()
        guest_names = ["Alice", "Bob", "Charlie", "David", "Eve"]
        joined_players = []

        for name in guest_names:
            response = client.post(
                "/api/v1/games/join",
                json={"pin": pin, "nickname": name},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["game_id"] == game_id
            assert data["nickname"] == name
            assert data["type"] == "guest"
            assert "token" in data
            assert "player_id" in data

            # Verify token là guest token hợp lệ giải mã được
            payload = security.decode_token(data["token"], expected_type="guest")
            assert payload["game_id"] == game_id
            assert payload["sub"] == f"guest:{data['player_id']}"

            joined_players.append(data)

        # 1. Kiểm tra 5 player_id phân biệt nhau
        player_ids = [p["player_id"] for p in joined_players]
        assert len(set(player_ids)) == 5

        # 2. Kiểm tra Redis ZSET game:{id}:lb khởi tạo đủ 5 người với 0 điểm
        lb_zset = memory_redis.zsets.get(f"game:{game_id}:lb")
        assert lb_zset is not None
        assert len(lb_zset) == 5
        for pid in player_ids:
            assert lb_zset.get(str(pid)) == 0.0

        # 3. Kiểm tra Redis Hash game:{id}:players lưu đủ 5 người (lowercase key)
        players_hash = memory_redis.hashes.get(f"game:{game_id}:players")
        assert players_hash is not None
        assert len(players_hash) == 5
        for name in guest_names:
            assert name.lower() in players_hash

        # 4. Kiểm tra Redis Pub/Sub phát đi 5 thông báo lobby.update
        messages = [
            json.loads(msg) for ch, msg in memory_redis.published if ch == f"game:{game_id}"
        ]
        assert len(messages) == 5
        assert all(m["type"] == WSMessageType.LOBBY_UPDATE.value for m in messages)
        assert messages[-1]["data"]["player_count"] == 5

    def test_join_game_duplicate_nickname_rejected(self) -> None:
        _, pin = self._setup_open_game()

        # Người 1 join với nickname "Alice"
        res1 = client.post("/api/v1/games/join", json={"pin": pin, "nickname": "Alice"})
        assert res1.status_code == 200

        # Người 2 cố join với "Alice" (hoặc "alice")
        res2 = client.post("/api/v1/games/join", json={"pin": pin, "nickname": "alice"})
        assert res2.status_code == 409
        assert res2.json()["error"]["code"] == "NICKNAME_TAKEN"

    def test_join_game_offensive_nickname_rejected(self) -> None:
        _, pin = self._setup_open_game()

        bad_names = ["admin", "root_user", "system_bot", "moderator_1", "fuck_you"]
        for bad in bad_names:
            res = client.post("/api/v1/games/join", json={"pin": pin, "nickname": bad})
            assert res.status_code == 400
            assert res.json()["error"]["code"] == "INVALID_NICKNAME"

    def test_join_game_nickname_length_validation(self) -> None:
        _, pin = self._setup_open_game()

        # Quá ngắn (< 2 ký tự)
        res_short = client.post("/api/v1/games/join", json={"pin": pin, "nickname": "A"})
        assert res_short.status_code == 422

        # Quá dài (> 20 ký tự)
        res_long = client.post("/api/v1/games/join", json={"pin": pin, "nickname": "A" * 21})
        assert res_long.status_code == 422

    def test_join_game_kicked_ban(self) -> None:
        game_id, pin = self._setup_open_game()

        # Giả lập người chơi "troll" đã bị Host kick (Redis key game:{id}:kicked:troll tồn tại)
        memory_redis.strings[f"game:{game_id}:kicked:troll"] = "1"

        res = client.post("/api/v1/games/join", json={"pin": pin, "nickname": "Troll"})
        assert res.status_code == 403
        assert res.json()["error"]["code"] == "KICKED_BAN"

    def test_join_game_full_room_rejected(self) -> None:
        game_id, pin = self._setup_open_game()

        # Giả lập phòng đã đầy 500 người trong Redis hash
        players_key = f"game:{game_id}:players"
        for i in range(500):
            memory_redis.hashes.setdefault(players_key, {})[f"user_{i}"] = json.dumps(
                {"id": str(uuid.uuid4())}
            )

        res = client.post("/api/v1/games/join", json={"pin": pin, "nickname": "NewPlayer"})
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "GAME_FULL"

    def test_join_game_wrong_pin_or_started(self) -> None:
        # 1. Sai PIN
        res_wrong = client.post("/api/v1/games/join", json={"pin": "999999", "nickname": "Player1"})
        assert res_wrong.status_code == 404
        assert res_wrong.json()["error"]["code"] == "GAME_NOT_FOUND"

        # 2. Trận đã bắt đầu (không ở LOBBY)
        game_id, pin = self._setup_open_game()
        memory_redis.hashes[f"game:{game_id}:state"]["status"] = GameStatus.QUESTION_ACTIVE.value

        res_started = client.post("/api/v1/games/join", json={"pin": pin, "nickname": "LatePlayer"})
        assert res_started.status_code == 400
        assert res_started.json()["error"]["code"] == "GAME_ALREADY_STARTED"

    def test_join_game_registered_user(self) -> None:
        game_id, pin = self._setup_open_game()
        user_id = uuid.uuid4()

        # Tạo user trong mock users_db
        user = User(
            id=user_id,
            email="player@example.com",
            display_name="ProPlayer",
            password_hash="fake",
            is_active=True,
            token_version=1,
        )
        users_db[user_id] = user

        user_token = security.create_access_token(
            user_id=user_id,
            roles=[RoleName.PLAYER.value],
            permissions=[PermissionCode.GAME_PLAY.value],
            token_version=1,
        )

        response = client.post(
            "/api/v1/games/join",
            json={"pin": pin, "nickname": "ProPlayer"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "player"
        assert data["nickname"] == "ProPlayer"

        # Kiểm tra user_id được gắn vào GamePlayer trong DB
        player = players_db.get(uuid.UUID(data["player_id"]))
        assert player is not None
        assert player.user_id == user_id
