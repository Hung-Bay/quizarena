"""Dịch vụ kiểm tra và xử lý tài liệu tải lên (PDF, TXT, MD).

Tuân thủ FR-AIGEN-01:
- Kiểm tra dung lượng tối đa 10 MB.
- Kiểm tra magic bytes thực tế thay vì tin đuôi mở rộng.
- Đếm số trang PDF (tối đa 30 trang), từ chối file bị mã hóa hoặc hỏng.
- Tính băm SHA-256 để định danh nội dung và hỗ trợ cache.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.errors import AppError

MAX_PDF_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_PDF_PAGES = 30
PDF_MAGIC_BYTES = b"%PDF-"


def validate_pdf_content(content: bytes) -> tuple[int, str]:
    """Kiểm tra nội dung PDF theo tiêu chuẩn FR-AIGEN-01.

    Args:
        content: Toàn bộ byte của file tải lên.

    Returns:
        tuple[int, str]: (số trang PDF, chuỗi băm sha256).

    Raises:
        AppError: nếu dung lượng > 10 MB, không phải PDF, mã hóa, hỏng hoặc > 30 trang.
    """
    # 1. Kiểm tra kích thước
    if len(content) > MAX_PDF_SIZE_BYTES:
        raise AppError(
            f"Kích thước tài liệu ({len(content)} bytes) vượt quá giới hạn tối đa 10 MB",
            code="FILE_TOO_LARGE",
            status_code=422,
        )

    # 2. Kiểm tra magic bytes
    if not content.startswith(PDF_MAGIC_BYTES):
        raise AppError(
            "Tập tin không phải định dạng PDF hợp lệ (sai magic bytes)",
            code="INVALID_FILE_TYPE",
            status_code=422,
        )

    # 3. Tính mã băm SHA-256
    sha256 = hashlib.sha256(content).hexdigest()

    # 4. Đọc cấu trúc PDF bằng pypdf
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise AppError(
                "Tài liệu PDF được mã hóa hoặc yêu cầu mật khẩu, hệ thống không thể xử lý",
                code="PDF_ENCRYPTED",
                status_code=422,
            )
        pages_count = len(reader.pages)
    except PdfReadError as exc:
        raise AppError(
            f"Tập tin PDF bị hỏng hoặc cấu trúc không hợp lệ: {exc}",
            code="INVALID_PDF",
            status_code=422,
        ) from exc
    except Exception as exc:
        if isinstance(exc, AppError):
            raise
        raise AppError(
            f"Không thể đọc tập tin PDF: {exc}",
            code="INVALID_PDF",
            status_code=422,
        ) from exc

    if pages_count < 1:
        raise AppError(
            "Tài liệu PDF không chứa trang nội dung nào",
            code="INVALID_PDF",
            status_code=422,
        )

    if pages_count > MAX_PDF_PAGES:
        raise AppError(
            f"Số trang PDF ({pages_count} trang) vượt quá giới hạn cho phép tối đa {MAX_PDF_PAGES} trang",
            code="PDF_TOO_MANY_PAGES",
            status_code=422,
        )

    return pages_count, sha256


def save_uploaded_file(content: bytes, upload_dir: str, sha256: str) -> str:
    """Lưu trữ tệp vào thư mục upload được chỉ định.

    Args:
        content: Nội dung byte cần ghi.
        upload_dir: Thư mục đích.
        sha256: Chuỗi băm SHA-256 để tạo tên tệp an toàn.

    Returns:
        str: Đường dẫn tuyệt đối đến tệp đã lưu.
    """
    target_dir = Path(upload_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    unique_suffix = uuid.uuid4().hex[:8]
    filename = f"{sha256[:16]}_{unique_suffix}.pdf"
    file_path = target_dir / filename

    with open(file_path, "wb") as f:
        f.write(content)

    return str(file_path.resolve())
