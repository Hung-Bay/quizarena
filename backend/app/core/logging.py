"""Log JSON một dòng, tự gắn request_id (NF-7)."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# request_id của request đang xử lý. Middleware đặt giá trị, formatter đọc ra.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Các thuộc tính có sẵn của LogRecord: không đưa vào JSON như "trường thêm".
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}
_HANDLER_MARK = "_quizarena_json"


def get_request_id() -> str | None:
    return request_id_ctx.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        # Trường thêm: log.info("request", extra={"status": 200}) → {"status": 200}
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Đưa mọi log (kể cả của uvicorn) ra stdout dạng JSON. Gọi lại nhiều lần vẫn an toàn."""
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARK, False)]

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
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
