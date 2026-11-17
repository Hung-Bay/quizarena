import asyncio
import re

from typer.testing import CliRunner

from app.cli import app
from tests.conftest import db_ready, skip_or_fail

runner = CliRunner()


def strip_ansi(text: str) -> str:
    """Loại bỏ các mã thoát màu ANSI (do Rich / Typer sinh ra) để đối chiếu chuỗi thuần."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def test_help_liet_ke_lenh_seed():
    result = runner.invoke(app, ["--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert result.exit_code == 0
    output = strip_ansi(result.output)
    assert "seed" in output
    assert "create-admin" in output
    assert "smoke" in output


def test_smoke_help_hien_thi_day_du_tham_so():
    result = runner.invoke(app, ["smoke", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert result.exit_code == 0
    output = strip_ansi(result.output)
    assert "--url" in output
    assert "--sha" in output
    assert "--email" in output
    assert "--password" in output
    assert "--timeout" in output


def test_smoke_thanh_cong_khi_runner_tra_ve_true(monkeypatch):
    from app.game.smoke_runner import SmokeStepResult, SmokeTestReport

    async def mock_run(self):
        return SmokeTestReport(
            success=True,
            steps=[
                SmokeStepResult("Kiểm tra DNS & TLS", True, 12.3),
                SmokeStepResult("GET /api/v1/version", True, 8.5),
                SmokeStepResult("1 Bot chơi qua WebSocket", True, 250.0),
            ],
            active_version="1.0.0",
            active_sha="a1b2c3d4e5f6",
            active_env="production",
            total_duration_s=0.27,
        )

    monkeypatch.setattr("app.game.smoke_runner.SmokeTestRunner.run", mock_run)
    result = runner.invoke(app, ["smoke", "--url", "http://testserver", "--sha", "a1b2c3d4e5f6"])
    assert result.exit_code == 0
    output = strip_ansi(result.output)
    assert "QUIZARENA SMOKE TEST RUNNER" in output
    assert "SMOKE TEST THÀNH CÔNG RỰC RỠ" in output
    assert "a1b2c3d4e5f6" in output


def test_smoke_that_bai_khi_runner_tra_ve_false(monkeypatch):
    from app.game.smoke_runner import SmokeStepResult, SmokeTestReport

    async def mock_run(self):
        return SmokeTestReport(
            success=False,
            steps=[
                SmokeStepResult("Kiểm tra DNS & TLS", True, 10.0),
                SmokeStepResult("GET /api/v1/version", False, 15.0, "SHA mismatch"),
            ],
            active_version="1.0.0",
            active_sha="wrong_sha",
            active_env="production",
            error="Version SHA mismatch: expected=target_sha actual=wrong_sha",
            total_duration_s=0.03,
        )

    monkeypatch.setattr("app.game.smoke_runner.SmokeTestRunner.run", mock_run)
    result = runner.invoke(app, ["smoke", "--url", "http://testserver", "--sha", "target_sha"])
    assert result.exit_code == 1
    output = strip_ansi(result.output)
    assert "SMOKE TEST THẤT BẠI" in output


def test_create_admin_mat_khau_yeu_bi_tu_choi():
    result = runner.invoke(app, ["create-admin", "--email", "admin@example.com", "-p", "weak"])
    assert result.exit_code == 1
    assert "Mật khẩu phải dài từ 8 đến 128 ký tự" in result.output


def test_seed_chay_hai_lan_deu_thanh_cong_va_lan_hai_khong_tao_moi():
    reason = asyncio.run(db_ready())
    if reason:
        skip_or_fail(reason)
    first = runner.invoke(app, ["seed"])
    second = runner.invoke(app, ["seed"])
    assert first.exit_code == 0 and second.exit_code == 0
    assert "0 vai trò, 0 quyền, 0 lượt gán quyền" in second.output


def test_gemini_quota_in_thong_tin():
    result = runner.invoke(app, ["gemini-quota"])
    assert result.exit_code == 0
    assert "THÔNG TIN HẠN MỨC & CẤU HÌNH GEMINI" in result.output
    assert "Model sinh Quiz" in result.output
