"""Kiểm thử ranh giới kiến trúc: Cấm import SDK Google GenAI bên ngoài Gateway Adapter (FR-AIGEN-12).

Tuân thủ nghiêm ngặt quy định ADR-002:
- Toàn bộ codebase (services, repositories, routers, worker) KHÔNG ĐƯỢC PHÉP import trực tiếp từ `google.genai`.
- Ngoại lệ duy nhất được cho phép là module adapter: `app/ai/gemini_gateway.py`.
- Mọi module khác chỉ được phụ thuộc vào interface trừu tượng `GeminiGatewayProtocol` hoặc `FakeGemini`.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_no_google_genai_import_outside_gateway() -> None:
    """Quét toàn bộ mã nguồn backend để đảm bảo không rò rỉ SDK google.genai ra ngoài gateway."""
    backend_app_dir = Path(__file__).resolve().parent.parent / "app"
    assert backend_app_dir.is_dir(), f"Không tìm thấy thư mục: {backend_app_dir}"

    import_pattern = re.compile(
        r"^\s*(from\s+google(?:\.genai)?\s+import|import\s+google(?:\.genai)?)", re.MULTILINE
    )

    violations: list[str] = []

    for py_file in backend_app_dir.rglob("*.py"):
        # Bỏ qua tệp adapter duy nhất được cấp phép
        rel_path = py_file.relative_to(backend_app_dir)
        if rel_path.as_posix() in ("ai/gemini_gateway.py",):
            continue

        content = py_file.read_text(encoding="utf-8")
        matches = import_pattern.findall(content)
        if matches:
            violations.append(
                f"{py_file.name} (tại app/{rel_path.as_posix()}) chứa import SDK: {matches}"
            )

    assert not violations, (
        f"Phát hiện {len(violations)} vi phạm ranh giới kiến trúc ADR-002/FR-AIGEN-12:\n"
        + "\n".join(violations)
        + "\nChỉ duy nhất 'app/ai/gemini_gateway.py' được phép import từ google.genai!"
    )
