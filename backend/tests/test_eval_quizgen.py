"""Kiểm thử tự động cho Quiz Generator AI Evaluation Runner (Phase 7 - §10.3).

Xác thực:
1. File evals/samples có đủ 5 tài liệu mẫu đa dạng (Tiếng Việt, Tiếng Anh, Bảng số liệu).
2. Runner hoạt động chuẩn xác với FakeGemini (môi trường test không tốn quota API).
3. Đo lường đầy đủ 5 tiêu chí:
   - % JSON hợp lệ
   - % Câu hỏi vượt qua kiểm tra luật nghiệp vụ Lớp 2
   - % Câu hỏi Host giữ nguyên
   - Độ trễ trung bình
   - Token tiêu thụ trung bình
4. Xuất file kết quả JSON có cấu trúc EvalSummary đầy đủ thông tin (commit_sha, model_name, v.v.).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.fake import FakeGemini
from app.game.eval_runner import QuizGenEvaluator


def test_samples_exist_and_diverse() -> None:
    samples_dir = Path("evals/samples")
    assert samples_dir.exists(), "Thư mục evals/samples không tồn tại"
    txt_files = list(samples_dir.glob("*.txt"))
    assert len(txt_files) >= 5, f"Cần tối thiểu 5 tài liệu mẫu, hiện có {len(txt_files)}"

    # Phải có tài liệu tiếng Việt, tiếng Anh và bảng biểu
    file_names = [f.name.lower() for f in txt_files]
    assert any("vietnamese" in name for name in file_names), "Thiếu tài liệu tiếng Việt"
    assert any("english" in name for name in file_names), "Thiếu tài liệu tiếng Anh"
    assert any("table" in name for name in file_names), "Thiếu tài liệu dạng bảng số liệu"


@pytest.mark.asyncio
async def test_eval_runner_with_fake_gemini(tmp_path: Path) -> None:
    samples_dir = Path("evals/samples")
    out_dir = tmp_path / "results"

    fake_gw = FakeGemini()
    evaluator = QuizGenEvaluator(samples_dir=samples_dir, gateway=fake_gw, output_dir=out_dir)

    summary = await evaluator.run_evaluation(num_questions_per_doc=3)

    assert summary.total_documents >= 5
    assert summary.total_questions_requested == summary.total_documents * 3
    assert summary.total_questions_generated > 0
    assert summary.json_validity_pct == 100.0
    assert summary.business_pass_pct >= 90.0
    assert summary.host_keep_pct >= 85.0
    assert summary.avg_latency_ms >= 0.0
    assert summary.avg_tokens_per_doc >= 0.0

    # Kiểm tra file JSON đã được xuất
    result_files = list(out_dir.glob("*.json"))
    assert len(result_files) == 1
    content = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert content["eval_id"] == summary.eval_id
    assert content["json_validity_pct"] == 100.0
    assert len(content["details"]) == summary.total_documents
