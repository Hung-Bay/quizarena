import logging

from arq.connections import RedisSettings

from app.core.config import get_settings

# Logger con của "arq" để dùng cấu hình log mà arq đã bật sẵn.
log = logging.getLogger("arq.quizarena")


async def ping(ctx: dict, who: str = "world") -> str:
    """Job thử nghiệm: ghi log rồi trả kết quả."""
    log.info("ping nhận từ %s", who)
    return f"pong {who}"


async def startup(ctx: dict) -> None:
    log.info("worker khởi động")


async def shutdown(ctx: dict) -> None:
    log.info("worker dừng")


class WorkerSettings:
    functions = [ping]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        get_settings().redis_url.get_secret_value())
