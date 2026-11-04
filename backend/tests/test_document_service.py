"""Kiểm thử tầng Document Service và Validation PDF (FR-AIGEN-01).

Kiểm tra:
- Dung lượng vượt quá 10 MB -> 422 (FILE_TOO_LARGE)
- Không bắt đầu bằng '%PDF-' -> 422 (INVALID_FILE_TYPE)
- PDF bị mã hóa/khóa mật khẩu -> 422 (PDF_ENCRYPTED)
- PDF hỏng không parse được -> 422 (INVALID_PDF)
- PDF 0 trang -> 422 (INVALID_PDF)
- PDF > 30 trang -> 422 (PDF_TOO_MANY_PAGES)
- PDF hợp lệ (1-30 trang) -> Trả về đúng số trang và mã băm SHA-256
- Lưu trữ tệp vào thư mục đích an toàn
"""

import hashlib
import io
import tempfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.core.errors import AppError
from app.services.document_service import (
    MAX_PDF_SIZE_BYTES,
    save_uploaded_file,
    validate_pdf_content,
)


def _generate_test_pdf(
    num_pages: int = 1,
    encrypted: bool = False,
    password: str = "secret",
) -> bytes:
    """Tạo file PDF giả lập nhanh phục vụ kiểm thử."""
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt(password)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_validate_valid_pdf():
    """PDF hợp lệ 5 trang phải trả về đúng số trang và sha256."""
    pdf_bytes = _generate_test_pdf(num_pages=5)
    pages, sha256 = validate_pdf_content(pdf_bytes)

    assert pages == 5
    assert sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert len(sha256) == 64


def test_reject_file_exceeding_10mb():
    """File lớn hơn 10 MB bị từ chối với lỗi FILE_TOO_LARGE."""
    fake_large_content = b"%PDF-" + b"0" * (MAX_PDF_SIZE_BYTES + 10)
    with pytest.raises(AppError) as exc_info:
        validate_pdf_content(fake_large_content)

    assert exc_info.value.code == "FILE_TOO_LARGE"
    assert exc_info.value.status_code == 422


def test_reject_invalid_magic_bytes():
    """File không bắt đầu bằng %PDF- (kể cả có đuôi .pdf) bị từ chối."""
    fake_exe = b"MZ\x90\x00\x03\x00\x00\x00"  # PE Executable
    with pytest.raises(AppError) as exc_info:
        validate_pdf_content(fake_exe)

    assert exc_info.value.code == "INVALID_FILE_TYPE"
    assert exc_info.value.status_code == 422


def test_reject_encrypted_pdf():
    """PDF được mã hóa hoặc yêu cầu mật khẩu bị từ chối."""
    pdf_bytes = _generate_test_pdf(num_pages=3, encrypted=True)
    with pytest.raises(AppError) as exc_info:
        validate_pdf_content(pdf_bytes)

    assert exc_info.value.code == "PDF_ENCRYPTED"
    assert exc_info.value.status_code == 422


def test_reject_corrupted_pdf():
    """PDF có magic bytes nhưng nội dung bị hỏng bị từ chối."""
    corrupted_pdf = b"%PDF-1.4\nCorrupted content completely invalid"
    with pytest.raises(AppError) as exc_info:
        validate_pdf_content(corrupted_pdf)

    assert exc_info.value.code == "INVALID_PDF"
    assert exc_info.value.status_code == 422


def test_reject_pdf_with_more_than_30_pages():
    """PDF có 31 trang bị từ chối với lỗi PDF_TOO_MANY_PAGES."""
    pdf_bytes = _generate_test_pdf(num_pages=31)
    with pytest.raises(AppError) as exc_info:
        validate_pdf_content(pdf_bytes)

    assert exc_info.value.code == "PDF_TOO_MANY_PAGES"
    assert exc_info.value.status_code == 422
    assert "31" in exc_info.value.message


def test_accept_boundary_30_pages():
    """PDF đúng 30 trang phải được chấp nhận."""
    pdf_bytes = _generate_test_pdf(num_pages=30)
    pages, sha256 = validate_pdf_content(pdf_bytes)
    assert pages == 30
    assert len(sha256) == 64


def test_save_uploaded_file():
    """Kiểm tra việc lưu file an toàn vào thư mục."""
    content = b"%PDF-test-content"
    sha256 = hashlib.sha256(content).hexdigest()

    with tempfile.TemporaryDirectory() as tmp_dir:
        saved_path = save_uploaded_file(content, tmp_dir, sha256)
        path_obj = Path(saved_path)

        assert path_obj.exists()
        assert path_obj.read_bytes() == content
        assert sha256[:16] in path_obj.name
