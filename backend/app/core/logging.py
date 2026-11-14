"""Log JSON một dòng, tự gắn request_id, replica_id, game_id, user_id (NF-7)."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# ContextVars cho ngữ cảnh request và game:
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
game_id_ctx: ContextVar[str | None] = ContextVar("game_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)

# Replica ID cho container / instance hiện tại
_DEFAULT_REPLICA_ID = (
    os.environ.get("REPLICA_ID") or os.environ.get("HOSTNAME") or f"replica-{os.getpid()}"
)

# Các thuộc tính có sẵn của LogRecord: không đưa vào JSON như "trường thêm".
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}
_HANDLER_MARK = "_quizarena_json"


def get_request_id() -> str | None:
    return request_id_ctx.get()


def get_game_id() -> str | None:
    return game_id_ctx.get()


def get_user_id() -> str | None:
    return user_id_ctx.get()


def get_replica_id() -> str:
    return _DEFAULT_REPLICA_ID


class JsonFormatter(logging.Formatter):
    def __init__(self, replica_id: str | None = None) -> None:
        super().__init__()
        self.replica_id = replica_id or _DEFAULT_REPLICA_ID

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "replica_id": self.replica_id,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id

        game_id = getattr(record, "game_id", None) or game_id_ctx.get()
        if game_id:
            payload["game_id"] = game_id

        user_id = getattr(record, "user_id", None) or user_id_ctx.get()
        if user_id:
            payload["user_id"] = user_id

        # Trường thêm: log.info("request", extra={"status": 200}) → {"status": 200}
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_") and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", replica_id: str | None = None) -> None:
    """Đưa mọi log (kể cả của uvicorn) ra stdout dạng JSON. Gọi lại nhiều lần vẫn an toàn."""
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARK, False)]

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(replica_id=replica_id))
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn tự cài handler riêng → bỏ để dùng chung handler JSON ở root.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Log truy cập đã do AccessLogMiddleware ghi (có request_id, thời gian xử lý) → tắt bản của uvicorn.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
