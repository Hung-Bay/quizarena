"""Bộ quy tắc dùng chung cho câu hỏi và bộ Quiz (QZ-2, QZ-3, FR-QUIZ-02, 03, 05).

Áp dụng đồng bộ cho:
- CRUD câu hỏi do host tạo thủ công
- Xác thực 2 lớp cho câu hỏi do AI Gemini sinh ra (Lớp 2 - Nghiệp vụ)
- Kiểm tra điều kiện tiên quyết khi Xuất bản Quiz (Publish)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

ALLOWED_QUESTION_TYPES = {"single", "multiple", "true_false"}
ALLOWED_POINTS = {500, 1000, 2000}
MIN_TIME_LIMIT_S = 5
MAX_TIME_LIMIT_S = 60
MIN_QUIZ_QUESTIONS_TO_PUBLISH = 3

TRUE_FALSE_PAIRS = [
    {"đúng", "sai"},
    {"true", "false"},
]


class QuizRuleViolation(Exception):
    """Ngoại lệ khi một câu hỏi hoặc bộ quiz vi phạm quy tắc nghiệp vụ."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or [message]


@dataclass
class OptionInput:
    """Cấu trúc dữ liệu trung gian cho một phương án lựa chọn."""

    content: str
    is_correct: bool


@dataclass
class QuestionValidationResult:
    """Kết quả kiểm tra tính hợp lệ của câu hỏi."""

    is_valid: bool
    errors: list[str]


def validate_question_data(
    *,
    question_type: str,
    content: str,
    time_limit_s: int,
    points: int,
    options: Sequence[Any],
    max_doc_pages: int | None = None,
    source_ref: str | None = None,
) -> QuestionValidationResult:
    """Kiểm tra một câu hỏi theo toàn bộ quy tắc QZ-2 / FR-QUIZ-02, 03.

    Args:
        question_type: 'single', 'multiple', hoặc 'true_false'
        content: Nội dung câu hỏi
        time_limit_s: Thời gian trả lời (5 - 60s)
        points: Điểm thưởng (500, 1000, 2000)
        options: Danh sách phương án (mỗi phần tử có thuộc tính hoặc dict content, is_correct)
        max_doc_pages: Số trang tài liệu tối đa (dùng khi kiểm tra source_ref từ file AI)
        source_ref: Trích dẫn trang nguồn (nếu có)
    """
    errors: list[str] = []

    # 1. Kiểm tra loại câu hỏi
    if question_type not in ALLOWED_QUESTION_TYPES:
        errors.append(
            f"Loại câu hỏi '{question_type}' không hợp lệ. Cho phép: {sorted(ALLOWED_QUESTION_TYPES)}"
        )

    # 2. Nội dung câu hỏi
    if not content or not content.strip():
        errors.append("Nội dung câu hỏi không được để trống")

    # 3. Giới hạn thời gian
    if not (MIN_TIME_LIMIT_S <= time_limit_s <= MAX_TIME_LIMIT_S):
        errors.append(
            f"Thời gian trả lời ({time_limit_s}s) phải từ {MIN_TIME_LIMIT_S} đến {MAX_TIME_LIMIT_S} giây"
        )

    # 4. Điểm thưởng
    if points not in ALLOWED_POINTS:
        errors.append(
            f"Điểm thưởng ({points}) không hợp lệ. Chỉ cho phép các mức: {sorted(ALLOWED_POINTS)}"
        )

    # 5. Chuẩn hóa danh sách lựa chọn
    parsed_options: list[OptionInput] = []
    for opt in options:
        if isinstance(opt, dict):
            c = str(opt.get("content", ""))
            corr = bool(opt.get("is_correct", False))
        else:
            c = str(getattr(opt, "content", ""))
            corr = bool(getattr(opt, "is_correct", False))
        parsed_options.append(OptionInput(content=c.strip(), is_correct=corr))

    # 6. Kiểm tra lựa chọn trùng lặp
    cleaned_contents = [o.content.lower() for o in parsed_options if o.content]
    if len(cleaned_contents) != len(set(cleaned_contents)):
        errors.append("Các lựa chọn trả lời không được có nội dung trùng lặp")

    # 7. Kiểm tra theo từng loại câu hỏi cụ thể
    correct_count = sum(1 for o in parsed_options if o.is_correct)

    if question_type == "single":
        if not (2 <= len(parsed_options) <= 4):
            errors.append(
                f"Câu hỏi loại 'single' phải có từ 2 đến 4 lựa chọn (hiện có: {len(parsed_options)})"
            )
        if correct_count != 1:
            errors.append(
                f"Câu hỏi loại 'single' phải có duy nhất 1 đáp án đúng (hiện có: {correct_count})"
            )

    elif question_type == "multiple":
        if not (2 <= len(parsed_options) <= 4):
            errors.append(
                f"Câu hỏi loại 'multiple' phải có từ 2 đến 4 lựa chọn (hiện có: {len(parsed_options)})"
            )
        if correct_count < 1:
            errors.append(
                f"Câu hỏi loại 'multiple' phải có ít nhất 1 đáp án đúng (hiện có: {correct_count})"
            )

    elif question_type == "true_false":
        if len(parsed_options) != 2:
            errors.append(
                f"Câu hỏi 'true_false' phải có đúng 2 lựa chọn (hiện có: {len(parsed_options)})"
            )
        else:
            contents_set = {o.content.lower() for o in parsed_options}
            is_valid_pair = any(contents_set == valid_pair for valid_pair in TRUE_FALSE_PAIRS)
            if not is_valid_pair:
                errors.append(
                    "Câu hỏi 'true_false' phải có 2 lựa chọn là 'Đúng' và 'Sai' (hoặc 'True' và 'False')"
                )
        if correct_count != 1:
            errors.append(
                f"Câu hỏi 'true_false' phải có đúng 1 đáp án đúng (hiện có: {correct_count})"
            )

    # 8. Kiểm tra source_ref nếu có ràng buộc số trang tài liệu (cho AI pipeline AG-5)
    if max_doc_pages is not None and source_ref:
        # Tìm số nguyên đầu tiên trong source_ref (ví dụ: "Trang 45" -> 45)
        import re

        match = re.search(r"\d+", source_ref)
        if match:
            page_num = int(match.group(0))
            if page_num > max_doc_pages or page_num < 1:
                errors.append(
                    f"Trang tham chiếu source_ref ({page_num}) nằm ngoài số trang tài liệu ({max_doc_pages})"
                )

    return QuestionValidationResult(is_valid=len(errors) == 0, errors=errors)


def validate_quiz_for_publishing(questions: Sequence[Any]) -> list[str]:
    """Kiểm tra điều kiện xuất bản Quiz (QZ-3, FR-QUIZ-05).

    - Quiz phải có ít nhất 3 câu hỏi.
    - Mọi câu hỏi đều phải thỏa mãn toàn bộ luật QZ-2.
    """
    errors: list[str] = []

    if len(questions) < MIN_QUIZ_QUESTIONS_TO_PUBLISH:
        errors.append(
            f"Quiz chỉ được xuất bản khi có ít nhất {MIN_QUIZ_QUESTIONS_TO_PUBLISH} câu hỏi (hiện có: {len(questions)})"
        )

    for idx, q in enumerate(questions, start=1):
        q_type = getattr(q, "type", "") if hasattr(q, "type") else q.get("type", "")
        q_content = getattr(q, "content", "") if hasattr(q, "content") else q.get("content", "")
        time_limit = (
            getattr(q, "time_limit_s", 20)
            if hasattr(q, "time_limit_s")
            else q.get("time_limit_s", 20)
        )
        points = getattr(q, "points", 1000) if hasattr(q, "points") else q.get("points", 1000)
        options = getattr(q, "options", []) if hasattr(q, "options") else q.get("options", [])
        source_ref = (
            getattr(q, "source_ref", None)
            if hasattr(q, "source_ref")
            else q.get("source_ref", None)
        )

        res = validate_question_data(
            question_type=q_type,
            content=q_content,
            time_limit_s=time_limit,
            points=points,
            options=options,
            source_ref=source_ref,
        )
        if not res.is_valid:
            for err in res.errors:
                errors.append(f"Câu hỏi số {idx}: {err}")

    return errors
