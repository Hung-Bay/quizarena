"""Bộ luật tính điểm theo mẫu thiết kế Strategy (GM-6).

Quy tắc:
1. Thống nhất quy tắc làm tròn nửa lên (half-up) đồng nhất giữa Python và Lua:
   round_half_up(x) = math.floor(x + 0.5).
2. Luật Standard:
   - Sai: 0 điểm, reset streak = 0.
   - Đúng:
     points = base_points * (1 - 0.5 * (response_ms / time_limit_ms))
     new_streak = current_streak + 1
     Nếu new_streak >= 3: points = points * 1.1 (+10% streak bonus)
     Làm tròn nửa lên.
3. Câu multiple: Chỉ tính điểm khi chọn ĐÚNG TOÀN BỘ (tất cả đáp án đúng đều được chọn,
   không chọn bất kỳ đáp án sai nào). Nếu sai lệch bất kỳ phương án nào: 0 điểm.
"""

from __future__ import annotations

import math
from typing import Protocol


def round_half_up(n: float) -> int:
    """Làm tròn nửa lên (half-up) đồng nhất chính xác với Lua math.floor(x + 0.5)."""
    return math.floor(n + 0.5)


class ScoringStrategy(Protocol):
    """Giao diện chuẩn cho các chiến lược tính điểm (Strategy Pattern)."""

    def calculate(
        self,
        *,
        is_correct: bool,
        base_points: int,
        response_ms: int,
        time_limit_s: int,
        current_streak: int = 0,
    ) -> tuple[int, int]:
        """Tính toán điểm và chuỗi đúng mới.

        Trả về:
            (points_awarded, new_streak)
        """
        ...


class StandardScoringStrategy:
    """Chiến lược tính điểm chuẩn theo thời gian phản hồi và streak bonus (GM-6)."""

    def calculate(
        self,
        *,
        is_correct: bool,
        base_points: int,
        response_ms: int,
        time_limit_s: int,
        current_streak: int = 0,
    ) -> tuple[int, int]:
        if not is_correct:
            return 0, 0

        new_streak = current_streak + 1
        time_limit_ms = max(time_limit_s * 1000, 1)
        # Giới hạn tỷ lệ thời gian trong khoảng [0.0, 1.0]
        t_ratio = min(max(response_ms / time_limit_ms, 0.0), 1.0)

        # Công thức: points * (1 - 0.5 * t_ratio)
        raw_score = float(base_points) * (1.0 - 0.5 * t_ratio)

        # Thưởng chuỗi liên tiếp (streak >= 3 -> +10%)
        if new_streak >= 3:
            raw_score *= 1.1

        points_awarded = round_half_up(raw_score)
        return points_awarded, new_streak


class BinaryScoringStrategy:
    """Chiến lược tính điểm nhị phân (Đúng là trọn điểm, không phụ thuộc thời gian)."""

    def calculate(
        self,
        *,
        is_correct: bool,
        base_points: int,
        response_ms: int,
        time_limit_s: int,
        current_streak: int = 0,
    ) -> tuple[int, int]:
        if not is_correct:
            return 0, 0

        new_streak = current_streak + 1
        raw_score = float(base_points)
        if new_streak >= 3:
            raw_score *= 1.1

        return round_half_up(raw_score), new_streak


_STRATEGIES: dict[str, ScoringStrategy] = {
    "standard": StandardScoringStrategy(),
    "binary": BinaryScoringStrategy(),
}


def get_scoring_strategy(strategy_id: str = "standard") -> ScoringStrategy:
    """Lấy chiến lược tính điểm theo strategy_id (mặc định 'standard')."""
    return _STRATEGIES.get(strategy_id, _STRATEGIES["standard"])


def check_answer_correctness(
    question_type: str,
    selected_option_ids: set[str],
    correct_option_ids: set[str],
) -> bool:
    """Kiểm tra tính đúng sai của câu trả lời.

    - single / true_false: tập lựa chọn phải trùng khớp tập đáp án đúng (1 phần tử).
    - multiple: phải chọn đúng và đủ 100% tất cả các đáp án đúng, không chọn thừa đáp án sai.
    """
    if not selected_option_ids or not correct_option_ids:
        return False
    return selected_option_ids == correct_option_ids
