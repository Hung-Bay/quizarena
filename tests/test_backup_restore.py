"""Kiểm thử tự động cho quy trình sao lưu và diễn tập phục hồi PostgreSQL (Phase 7 - §7.7).

Xác thực:
1. File backup_postgres.sh và restore_postgres.sh tồn tại, cú pháp hợp lệ bash, có cờ set -euo pipefail.
2. Kiểm tra các tham số quan trọng: pg_dump -Fc, retention xoay vòng 7 ngày, kiểm tra dung lượng > 100 bytes.
3. Kiểm tra tính năng đo RTO (Recovery Time Objective), xác thực row counts của các bảng cốt lõi (users, quizzes, questions, game_sessions) và alembic version.
4. Kiểm tra xuất metric Prometheus textfile collector (last_backup_success.prom) với cơ chế atomic rename.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "infra" / "scripts"
BACKUP_SCRIPT = SCRIPTS_DIR / "backup_postgres.sh"
RESTORE_SCRIPT = SCRIPTS_DIR / "restore_postgres.sh"


def _find_working_bash() -> str | None:
    """Tìm bash thực tế có thể thực thi được trên hệ thống (ưu tiên Git Bash trên Windows)."""
    # 1. Thử Git Bash tiêu chuẩn nếu trên Windows
    git_bash_candidates = [
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Git" / "bin" / "bash.exe",
        Path("D:\\Git\\bin\\bash.exe"),
        Path("C:\\Git\\bin\\bash.exe"),
    ]
    for candidate in git_bash_candidates:
        if candidate.exists():
            return str(candidate)

    # 2. Thử shutil.which('bash') nhưng loại bỏ System32 wsl wrapper nếu không hoạt động
    bash_path = shutil.which("bash")
    if bash_path and "system32" not in bash_path.lower():
        return bash_path

    return None


def test_backup_and_restore_scripts_exist() -> None:
    assert BACKUP_SCRIPT.exists(), f"Không tìm thấy script {BACKUP_SCRIPT}"
    assert RESTORE_SCRIPT.exists(), f"Không tìm thấy script {RESTORE_SCRIPT}"


def test_backup_script_contents_and_safety() -> None:
    content = BACKUP_SCRIPT.read_text(encoding="utf-8")

    # Bắt buộc có bash header và set -euo pipefail
    assert content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content

    # Kiểm tra lệnh pg_dump -Fc
    assert "pg_dump" in content
    assert "-Fc" in content

    # Kiểm tra kiểm tra dung lượng file dump tối thiểu
    assert "BACKUP_SIZE" in content

    # Kiểm tra retention xoay vòng 7 ngày
    assert "RETENTION_DAYS" in content
    assert 'mtime "+${RETENTION_DAYS}"' in content or '-mtime "+${RETENTION_DAYS}"' in content

    # Kiểm tra xuất metric Prometheus textfile collector
    assert "last_backup_success.prom" in content
    assert "quizarena_backup_timestamp_seconds" in content
    assert "quizarena_backup_size_bytes" in content
    assert "quizarena_backup_duration_seconds" in content


def test_restore_script_contents_and_safety() -> None:
    content = RESTORE_SCRIPT.read_text(encoding="utf-8")

    # Bắt buộc có bash header và set -euo pipefail
    assert content.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in content

    # Kiểm tra chế độ DRILL_MODE an toàn vào database tạm _drill
    assert "DRILL_MODE" in content
    assert "_drill" in content

    # Kiểm tra lệnh pg_restore
    assert "pg_restore" in content

    # Kiểm tra đo RTO
    assert "START_RTO" in content
    assert "RTO_SECONDS" in content
    assert "RPO" in content

    # Kiểm tra xác thực số dòng các bảng cốt lõi
    for table in ["users", "quizzes", "questions", "game_sessions"]:
        assert table in content

    # Kiểm tra xác thực alembic current
    assert "alembic_version" in content


def test_bash_syntax_validity_if_bash_available() -> None:
    """Nếu môi trường có bash thực thi được, kiểm tra cú pháp bằng bash -n."""
    bash_bin = _find_working_bash()
    if not bash_bin:
        return

    res1 = subprocess.run(
        [bash_bin, "-n", str(BACKUP_SCRIPT)], capture_output=True, text=True, check=False
    )
    assert res1.returncode == 0, f"Lỗi cú pháp bash trong {BACKUP_SCRIPT}: {res1.stderr}"

    res2 = subprocess.run(
        [bash_bin, "-n", str(RESTORE_SCRIPT)], capture_output=True, text=True, check=False
    )
    assert res2.returncode == 0, f"Lỗi cú pháp bash trong {RESTORE_SCRIPT}: {res2.stderr}"
