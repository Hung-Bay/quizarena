"""CLI quản trị (Typer).

Chạy:  uv run quizarena seed          hoặc        uv run python -m app.cli seed
       uv run quizarena create-admin --email admin@quizarena.io.vn
Trong container:                      python -m app.cli seed
"""

import asyncio
import re
import sys

# Đảm bảo UTF-8 trên Windows console chống lỗi charmap UnicodeEncodeError
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

import typer

from app.ai.gemini_gateway import create_gemini_gateway
from app.ai.schemas import AiGenerationParams
from app.core.config import get_settings
from app.core.db import create_engine, create_session_factory
from app.core.rbac import RoleName
from app.core.security import hash_password_async
from app.repositories import user_repository as users
from app.services.seed import SeedResult, seed_rbac

app = typer.Typer(help="QuizArena — công cụ quản trị", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Công cụ dòng lệnh của QuizArena."""
    # Callback rỗng này ép Typer dùng dạng `quizarena <lệnh>` ngay cả khi mới có một lệnh.


async def _seed() -> SeedResult:
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session, session.begin():
            return await seed_rbac(session)  # session.begin(): tự commit, lỗi thì tự rollback
    finally:
        await engine.dispose()


@app.command()
def seed() -> None:
    """Nạp vai trò và quyền theo ma trận §3.2. Chạy nhiều lần vẫn an toàn."""
    result = asyncio.run(_seed())
    typer.echo(
        f"Đã tạo mới: {result.roles} vai trò, {result.permissions} quyền, "
        f"{result.role_permissions} lượt gán quyền."
    )


async def _create_admin(email: str, password: str) -> str:
    engine = create_engine()
    try:
        async with create_session_factory(engine)() as session, session.begin():
            await seed_rbac(session)
            email_normalized = email.strip().lower()
            admin_role_id = await users.role_id_by_name(session, RoleName.ADMIN.value)
            assert admin_role_id is not None

            user = await users.get_by_email(session, email_normalized)
            if user is None:
                pw_hash = await hash_password_async(password)
                user = await users.create(
                    session,
                    email=email_normalized,
                    password_hash=pw_hash,
                    display_name=email_normalized.split("@")[0],
                )
                await users.add_role(session, user.id, admin_role_id)
                return f"Đã tạo tài khoản admin thành công: {email_normalized}"

            user_roles = await users.get_roles(session, user.id)
            if RoleName.ADMIN.value not in user_roles:
                await users.add_role(session, user.id, admin_role_id)
                return f"Đã cấp quyền ADMIN cho tài khoản {email_normalized}"
            return f"Tài khoản {email_normalized} đã có quyền ADMIN từ trước."
    finally:
        await engine.dispose()


@app.command("create-admin")
def create_admin(
    email: str = typer.Option(..., "--email", "-e", help="Email tài khoản quản trị"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Mật khẩu (nếu bỏ trống sẽ được nhắc nhập)"
    ),
) -> None:
    """Tạo tài khoản quản trị viên mới hoặc cấp vai trò ADMIN cho người dùng sẵn có (§3.8)."""
    if not password:
        password = typer.prompt("Mật khẩu", hide_input=True, confirmation_prompt=True)

    if not (8 <= len(password) <= 128) or not (
        re.search(r"[A-Za-z]", password) and re.search(r"\d", password)
    ):
        typer.echo(
            "Lỗi: Mật khẩu phải dài từ 8 đến 128 ký tự và gồm cả chữ lẫn số.",
            err=True,
        )
        raise typer.Exit(code=1)

    msg = asyncio.run(_create_admin(email=email, password=password))
    typer.echo(msg)


@app.command("gemini-quota")
def gemini_quota(
    test: bool = typer.Option(
        False, "--test", "-t", help="Thực hiện 1 lệnh gọi test sinh câu hỏi qua Gemini Gateway"
    ),
) -> None:
    """In thông tin hạn mức Gemini, cấu hình rate limit và kiểm tra kết nối (§3.8)."""
    settings = get_settings()
    gateway = create_gemini_gateway(settings)
    mode = getattr(settings, "gemini_gateway_mode", "fake")

    typer.echo("=== THÔNG TIN HẠN MỨC & CẤU HÌNH GEMINI ===")
    typer.echo(f"Chế độ Gateway        : {mode.upper()}")
    typer.echo(f"Model sinh Quiz       : {settings.gemini_model_quiz}")
    typer.echo(f"Model viết báo cáo    : {settings.gemini_model_report}")
    typer.echo(f"Giới hạn RPM cấu hình : {settings.gemini_rpm} req/phút")
    typer.echo(f"Giới hạn RPD cấu hình : {settings.gemini_rpd} req/ngày (theo giờ Pacific)")
    typer.echo(f"Hạn mức host/ngày     : {settings.ai_generations_per_host_per_day} lần/ngày")

    # Kiểm tra kết nối model
    info = gateway.get_model_info()
    typer.echo(f"Trạng thái kết nối    : {info.get('status')}")
    if info.get("status") == "connected":
        typer.echo(f"Display Name          : {info.get('display_name')}")
        input_limit = info.get("input_token_limit")
        output_limit = info.get("output_token_limit")
        typer.echo(
            f"Input Token Limit     : {input_limit:,}"
            if input_limit
            else "Input Token Limit     : N/A"
        )
        typer.echo(
            f"Output Token Limit    : {output_limit:,}"
            if output_limit
            else "Output Token Limit    : N/A"
        )
    elif info.get("error"):
        typer.echo(f"Chi tiết lỗi kết nối  : {info.get('error')}")

    if test:
        typer.echo("\n--- Đang thực hiện kiểm thử sinh câu hỏi qua Gateway ---")
        params = AiGenerationParams(
            num_questions=5,
            difficulty="easy",
            language="vi",
            allowed_types=["single"],
            note="Kiểm tra kết nối CLI",
        )
        try:
            res = asyncio.run(
                gateway.generate_quiz_from_text(
                    text_content="Việt Nam là một quốc gia nằm ở khu vực Đông Nam Á, thủ đô là Hà Nội.",
                    params=params,
                )
            )
            typer.echo(f"Thành công! Tiêu đề: {res.draft.title}")
            typer.echo(f"Số câu hỏi sinh ra : {len(res.draft.questions)}")
            typer.echo(f"Câu hỏi 1          : {res.draft.questions[0].content}")
            for opt in res.draft.questions[0].options:
                mark = "[X]" if opt.is_correct else "[ ]"
                typer.echo(f"  {mark} {opt.content}")
            typer.echo(
                f"Token sử dụng      : input={res.usage.input_tokens}, "
                f"output={res.usage.output_tokens}, total={res.usage.total_tokens}"
            )
            typer.echo(f"Model thực thi     : {res.model_name}")
        except Exception as err:
            typer.echo(f"Lỗi kiểm thử: {err}", err=True)
            raise typer.Exit(code=1) from err


@app.command("bots")
def run_bots(
    pin: str = typer.Option(..., "--pin", "-p", help="Mã PIN 6 số của phòng thi"),
    count: int = typer.Option(50, "--count", "-c", help="Số lượng bot tham gia (tối đa 50)"),
    base_url: str = typer.Option(
        "http://localhost:8000", "--url", "-u", help="Địa chỉ API QuizArena (REST)"
    ),
    ws_url: str | None = typer.Option(
        None, "--ws-url", "-w", help="Địa chỉ WebSocket (mặc định tự suy ra từ base_url)"
    ),
    jitter_min: float = typer.Option(
        0.5, "--jitter-min", help="Độ trễ tối thiểu khi trả lời (giây)"
    ),
    jitter_max: float = typer.Option(2.0, "--jitter-max", help="Độ trễ tối đa khi trả lời (giây)"),
    prefix: str = typer.Option("Bot", "--prefix", help="Tiền tố tên hiển thị của bot"),
) -> None:
    """Khởi chạy bầy bot đồng thời tham gia phòng thi live và nộp bài tự động (FR-GAME-01..06, 14, 16)."""
    from app.game.bot_runner import BotConfig, BotManager

    if count < 1 or count > 500:
        typer.echo("Lỗi: Số lượng bot phải nằm trong khoảng từ 1 đến 500.", err=True)
        raise typer.Exit(code=1)

    pin_clean = pin.strip()
    if not (len(pin_clean) == 6 and pin_clean.isdigit()):
        typer.echo("Lỗi: Mã PIN phải gồm đúng 6 chữ số.", err=True)
        raise typer.Exit(code=1)

    config = BotConfig(
        pin=pin_clean,
        count=count,
        base_url=base_url,
        ws_url=ws_url,
        jitter_min=jitter_min,
        jitter_max=jitter_max,
        prefix=prefix,
    )

    typer.echo("==================================================")
    typer.echo(f"  QUIZARENA BOT RUNNER — PIN: {pin_clean}")
    typer.echo(f"  Số bot        : {count}")
    typer.echo(f"  Target URL    : {base_url}")
    typer.echo(f"  Jitter trả lời: {jitter_min}s - {jitter_max}s")
    typer.echo("==================================================")
    typer.echo("Đang kết nối bot và lắng nghe trận đấu... (Nhấn Ctrl+C để dừng sớm)\n")

    manager = BotManager(config)

    try:
        stats = asyncio.run(manager.start())
    except KeyboardInterrupt:
        typer.echo("\n[!] Nhận tín hiệu dừng từ người dùng. Đang đóng kết nối bot...")
        asyncio.run(manager.stop())
        stats = manager.stats

    typer.echo("\n================ KẾT QUẢ HOẠT ĐỘNG BOT ================")
    typer.echo(f"Tổng số bot dự kiến      : {stats.total_bots}")
    typer.echo(f"Số bot đã join (REST)    : {stats.joined}")
    typer.echo(f"Số bot kết nối WS        : {stats.connected}")
    typer.echo(f"Số lượt nộp bài          : {stats.answers_submitted}")
    typer.echo(f"Số lượt server chấp nhận : {stats.answers_accepted}")
    typer.echo(f"Số bot hoàn thành trận   : {stats.completed}")
    if stats.errors:
        typer.echo(f"Tổng số lỗi phát sinh    : {len(stats.errors)}")
        typer.echo(f"Lỗi đầu tiên ghi nhận    : {stats.errors[0]}")
    typer.echo("=======================================================\n")


if __name__ == "__main__":
    app()
