import asyncio

from typer.testing import CliRunner

from app.cli import app
from tests.conftest import db_ready, skip_or_fail

runner = CliRunner()


def test_help_liet_ke_lenh_seed():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "seed" in result.output


def test_seed_chay_hai_lan_deu_thanh_cong_va_lan_hai_khong_tao_moi():
    reason = asyncio.run(db_ready())
    if reason:
        skip_or_fail(reason)
    first = runner.invoke(app, ["seed"])
    second = runner.invoke(app, ["seed"])
    assert first.exit_code == 0 and second.exit_code == 0
    assert "0 vai trò, 0 quyền, 0 lượt gán quyền" in second.output
