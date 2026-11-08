"""Các Enum định nghĩa trạng thái trận và loại thông điệp WebSocket (GM-1, GM-14)."""

from enum import StrEnum


class GameStatus(StrEnum):
    """Trạng thái vòng đời của trận đấu (State Machine)."""

    LOBBY = "lobby"
    QUESTION_ACTIVE = "question_active"
    QUESTION_RESULT = "question_result"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class WSMessageType(StrEnum):
    """Danh mục các loại thông điệp WebSocket (v=1) theo docs/WS_PROTOCOL.md."""

    # Server -> Client
    LOBBY_UPDATE = "lobby.update"
    QUESTION_START = "question.start"
    ANSWER_ACK = "answer.ack"
    QUESTION_RESULT = "question.result"
    LEADERBOARD = "leaderboard"
    GAME_FINISHED = "game.finished"
    ERROR = "error"
    SERVER_DRAINING = "server.draining"
    SNAPSHOT = "snapshot"

    # Client -> Server
    ANSWER_SUBMIT = "answer.submit"
    HEARTBEAT = "heartbeat"
    GAME_START = "game.start"
    GAME_NEXT = "game.next"
    GAME_PAUSE = "game.pause"
    GAME_END = "game.end"
    PLAYER_KICK = "player.kick"


class WSErrorCode(StrEnum):
    """Mã lỗi cho gói tin type='error' hoặc lý do từ chối trong answer.ack."""

    BAD_MESSAGE = "BAD_MESSAGE"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    FORBIDDEN = "FORBIDDEN"
    INVALID_STATE = "INVALID_STATE"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_ACTIVE = "NOT_ACTIVE"
    ALREADY_ANSWERED = "ALREADY_ANSWERED"
    GAME_NOT_FOUND = "GAME_NOT_FOUND"
    GAME_ALREADY_STARTED = "GAME_ALREADY_STARTED"
    GAME_FULL = "GAME_FULL"
    NICKNAME_TAKEN = "NICKNAME_TAKEN"
    KICKED_BAN = "KICKED_BAN"
    LATE = "LATE"
