"""Unit tests cho bộ luật câu hỏi và điều kiện xuất bản (QZ-2, QZ-3).

Parametrize toàn bộ các trường hợp biên của:
- Loại câu hỏi: single, multiple, true_false
- Lựa chọn trùng lặp
- Thời gian trả lời (5-60s)
- Điểm thưởng (500, 1000, 2000)
- Điều kiện xuất bản (ít nhất 3 câu, mọi câu hợp lệ)
"""

import pytest

from app.services.quiz_rules import (
    validate_question_data,
    validate_quiz_for_publishing,
)

# =========================================================================
# 1. PARAMETRIZE LUẬT CÂU HỎI QZ-2
# =========================================================================


@pytest.mark.parametrize(
    "options, expected_valid, error_keyword",
    [
        # single: 2-4 lựa chọn, đúng 1 đáp án đúng
        (
            [{"content": "A", "is_correct": True}, {"content": "B", "is_correct": False}],
            True,
            None,
        ),
        (
            [
                {"content": "A", "is_correct": True},
                {"content": "B", "is_correct": False},
                {"content": "C", "is_correct": False},
                {"content": "D", "is_correct": False},
            ],
            True,
            None,
        ),
        # 0 đáp án đúng
        (
            [{"content": "A", "is_correct": False}, {"content": "B", "is_correct": False}],
            False,
            "duy nhất 1 đáp án đúng",
        ),
        # 2 đáp án đúng
        (
            [{"content": "A", "is_correct": True}, {"content": "B", "is_correct": True}],
            False,
            "duy nhất 1 đáp án đúng",
        ),
        # 1 lựa chọn (ít hơn 2)
        (
            [{"content": "A", "is_correct": True}],
            False,
            "từ 2 đến 4",
        ),
        # 5 lựa chọn (nhiều hơn 4)
        (
            [
                {"content": "A", "is_correct": True},
                {"content": "B", "is_correct": False},
                {"content": "C", "is_correct": False},
                {"content": "D", "is_correct": False},
                {"content": "E", "is_correct": False},
            ],
            False,
            "từ 2 đến 4",
        ),
    ],
)
def test_luat_cau_hoi_single(options, expected_valid, error_keyword):
    res = validate_question_data(
        question_type="single",
        content="Nội dung câu hỏi hợp lệ?",
        time_limit_s=20,
        points=1000,
        options=options,
    )
    assert res.is_valid == expected_valid
    if not expected_valid:
        assert any(error_keyword in err for err in res.errors)


@pytest.mark.parametrize(
    "options, expected_valid, error_keyword",
    [
        # multiple: 2-4 lựa chọn, >= 1 đáp án đúng
        (
            [
                {"content": "A", "is_correct": True},
                {"content": "B", "is_correct": True},
                {"content": "C", "is_correct": False},
            ],
            True,
            None,
        ),
        (
            [
                {"content": "A", "is_correct": True},
                {"content": "B", "is_correct": True},
                {"content": "C", "is_correct": True},
                {"content": "D", "is_correct": True},
            ],
            True,
            None,
        ),
        # 0 đáp án đúng
        (
            [{"content": "A", "is_correct": False}, {"content": "B", "is_correct": False}],
            False,
            "ít nhất 1 đáp án đúng",
        ),
        # 5 lựa chọn
        (
            [
                {"content": "A", "is_correct": True},
                {"content": "B", "is_correct": True},
                {"content": "C", "is_correct": False},
                {"content": "D", "is_correct": False},
                {"content": "E", "is_correct": False},
            ],
            False,
            "từ 2 đến 4",
        ),
    ],
)
def test_luat_cau_hoi_multiple(options, expected_valid, error_keyword):
    res = validate_question_data(
        question_type="multiple",
        content="Nội dung câu hỏi nhiều đáp án?",
        time_limit_s=30,
        points=2000,
        options=options,
    )
    assert res.is_valid == expected_valid
    if not expected_valid:
        assert any(error_keyword in err for err in res.errors)


@pytest.mark.parametrize(
    "options, expected_valid, error_keyword",
    [
        # true_false: đúng 2 lựa chọn Đúng/Sai, đúng 1 đúng
        (
            [{"content": "Đúng", "is_correct": True}, {"content": "Sai", "is_correct": False}],
            True,
            None,
        ),
        (
            [{"content": "True", "is_correct": False}, {"content": "False", "is_correct": True}],
            True,
            None,
        ),
        # Nội dung không phải Đúng/Sai
        (
            [{"content": "Có", "is_correct": True}, {"content": "Không", "is_correct": False}],
            False,
            "Đúng",
        ),
        # 3 lựa chọn
        (
            [
                {"content": "Đúng", "is_correct": True},
                {"content": "Sai", "is_correct": False},
                {"content": "Khác", "is_correct": False},
            ],
            False,
            "đúng 2 lựa chọn",
        ),
        # 2 đáp án đúng
        (
            [{"content": "Đúng", "is_correct": True}, {"content": "Sai", "is_correct": True}],
            False,
            "đúng 1 đáp án đúng",
        ),
    ],
)
def test_luat_cau_hoi_true_false(options, expected_valid, error_keyword):
    res = validate_question_data(
        question_type="true_false",
        content="Nội dung phát biểu đúng sai?",
        time_limit_s=15,
        points=500,
        options=options,
    )
    assert res.is_valid == expected_valid
    if not expected_valid:
        assert any(error_keyword in err for err in res.errors)


def test_lua_chon_trung_nhau_bi_tu_choi():
    options = [
        {"content": "Hà Nội", "is_correct": True},
        {"content": "hà nội", "is_correct": False},  # trùng không phân biệt hoa thường
    ]
    res = validate_question_data(
        question_type="single",
        content="Thủ đô?",
        time_limit_s=20,
        points=1000,
        options=options,
    )
    assert not res.is_valid
    assert any("trùng lặp" in err for err in res.errors)


@pytest.mark.parametrize(
    "time_limit_s, expected_valid",
    [
        (4, False),
        (5, True),
        (20, True),
        (60, True),
        (61, False),
    ],
)
def test_luat_thoi_gian_tra_loi(time_limit_s, expected_valid):
    options = [
        {"content": "A", "is_correct": True},
        {"content": "B", "is_correct": False},
    ]
    res = validate_question_data(
        question_type="single",
        content="Câu hỏi?",
        time_limit_s=time_limit_s,
        points=1000,
        options=options,
    )
    assert res.is_valid == expected_valid


@pytest.mark.parametrize(
    "points, expected_valid",
    [
        (100, False),
        (500, True),
        (700, False),
        (1000, True),
        (1500, False),
        (2000, True),
        (3000, False),
    ],
)
def test_luat_diem_thuong(points, expected_valid):
    options = [
        {"content": "A", "is_correct": True},
        {"content": "B", "is_correct": False},
    ]
    res = validate_question_data(
        question_type="single",
        content="Câu hỏi?",
        time_limit_s=20,
        points=points,
        options=options,
    )
    assert res.is_valid == expected_valid


def test_noi_dung_cau_hoi_rong_bi_tu_choi():
    options = [
        {"content": "A", "is_correct": True},
        {"content": "B", "is_correct": False},
    ]
    res = validate_question_data(
        question_type="single",
        content="   ",
        time_limit_s=20,
        points=1000,
        options=options,
    )
    assert not res.is_valid
    assert any("không được để trống" in err for err in res.errors)


def test_source_ref_vuot_qua_so_trang_tai_lieu():
    options = [
        {"content": "A", "is_correct": True},
        {"content": "B", "is_correct": False},
    ]
    # Tài liệu 20 trang nhưng source_ref là Trang 45
    res = validate_question_data(
        question_type="single",
        content="Nội dung",
        time_limit_s=20,
        points=1000,
        options=options,
        max_doc_pages=20,
        source_ref="Trang 45",
    )
    assert not res.is_valid
    assert any("nằm ngoài số trang" in err for err in res.errors)


# =========================================================================
# 2. KIỂM TRA ĐIỀU KIỆN XUẤT BẢN QUIZ (QZ-3, FR-QUIZ-05)
# =========================================================================


def test_xuat_ban_duoi_3_cau_bi_tu_choi():
    valid_q = {
        "type": "single",
        "content": "Câu hỏi?",
        "time_limit_s": 20,
        "points": 1000,
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": False},
        ],
    }
    # Chỉ có 2 câu
    errors = validate_quiz_for_publishing([valid_q, valid_q])
    assert len(errors) > 0
    assert any("ít nhất 3 câu hỏi" in err for err in errors)


def test_xuat_ban_3_cau_nhung_1_cau_loi_bi_tu_choi():
    valid_q = {
        "type": "single",
        "content": "Câu hỏi hợp lệ?",
        "time_limit_s": 20,
        "points": 1000,
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": False},
        ],
    }
    invalid_q = {
        "type": "single",
        "content": "Câu hỏi lỗi (2 đúng)?",
        "time_limit_s": 20,
        "points": 1000,
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": True},
        ],
    }
    errors = validate_quiz_for_publishing([valid_q, valid_q, invalid_q])
    assert len(errors) > 0
    assert any("Câu hỏi số 3" in err for err in errors)


def test_xuat_ban_3_cau_hop_le_thanh_cong():
    valid_q1 = {
        "type": "single",
        "content": "Câu hỏi 1?",
        "time_limit_s": 20,
        "points": 1000,
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": False},
        ],
    }
    valid_q2 = {
        "type": "multiple",
        "content": "Câu hỏi 2?",
        "time_limit_s": 30,
        "points": 2000,
        "options": [
            {"content": "A", "is_correct": True},
            {"content": "B", "is_correct": True},
            {"content": "C", "is_correct": False},
        ],
    }
    valid_q3 = {
        "type": "true_false",
        "content": "Câu hỏi 3?",
        "time_limit_s": 15,
        "points": 500,
        "options": [
            {"content": "Đúng", "is_correct": True},
            {"content": "Sai", "is_correct": False},
        ],
    }
    errors = validate_quiz_for_publishing([valid_q1, valid_q2, valid_q3])
    assert len(errors) == 0
