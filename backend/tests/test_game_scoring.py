"""Unit test cho Game Scoring Strategy & Round Half-up (GM-6, Quyết định Thứ Hai).

Kiểm thử:
- Hàm round_half_up đồng nhất chính xác với Lua math.floor(x + 0.5), khắc phục banker's rounding của Python.
- StandardScoringStrategy: tính điểm theo độ trễ thời gian, suy giảm 50% ở deadline, streak bonus +10% từ streak 3.
- BinaryScoringStrategy: đúng trọn điểm bất kể thời gian, streak bonus +10% từ streak 3.
- check_answer_correctness: luật đúng sai cho single/multiple choice (multiple đòi hỏi chính xác 100%).
- get_scoring_strategy: fallback về standard khi strategy_id không xác định.
"""

from app.game.scoring import (
    BinaryScoringStrategy,
    StandardScoringStrategy,
    check_answer_correctness,
    get_scoring_strategy,
    round_half_up,
)


class TestRoundHalfUp:
    """Kiểm tra hàm làm tròn nửa lên (half-up) so với banker's rounding."""

    def test_half_up_boundary_values(self) -> None:
        # Số kết thúc bằng .5
        assert round_half_up(0.5) == 1
        assert round_half_up(1.5) == 2
        # Điểm khác biệt quan trọng với Python round(2.5) == 2:
        assert round_half_up(2.5) == 3
        assert round(2.5) == 2  # Minh họa Python round mặc định làm tròn chẵn
        assert round_half_up(3.5) == 4
        assert round_half_up(4.5) == 5

    def test_half_up_below_and_above(self) -> None:
        assert round_half_up(1.49) == 1
        assert round_half_up(1.51) == 2
        assert round_half_up(100.0) == 100
        assert round_half_up(0.0) == 0


class TestStandardScoringStrategy:
    """Kiểm tra thuật toán Standard: points = base * (1 - 0.5 * (t/T)) kèm streak bonus."""

    def setup_method(self) -> None:
        self.strategy = StandardScoringStrategy()

    def test_incorrect_answer_yields_zero_and_resets_streak(self) -> None:
        # Dù trước đó streak đang cao (ví dụ 10), trả lời sai phải về 0 điểm và reset streak = 0
        points, streak = self.strategy.calculate(
            is_correct=False,
            base_points=1000,
            response_ms=500,
            time_limit_s=10,
            current_streak=10,
        )
        assert points == 0
        assert streak == 0

    def test_immediate_correct_answer_yields_full_points(self) -> None:
        # t = 0 -> t_ratio = 0 -> 1000 * (1 - 0) = 1000
        points, streak = self.strategy.calculate(
            is_correct=True,
            base_points=1000,
            response_ms=0,
            time_limit_s=10,
            current_streak=0,
        )
        assert points == 1000
        assert streak == 1

    def test_halfway_response_yields_75_percent_points(self) -> None:
        # t = 5000ms / 10000ms = 0.5 -> 1000 * (1 - 0.5 * 0.5) = 750
        points, streak = self.strategy.calculate(
            is_correct=True,
            base_points=1000,
            response_ms=5000,
            time_limit_s=10,
            current_streak=0,
        )
        assert points == 750
        assert streak == 1

    def test_deadline_response_yields_50_percent_points(self) -> None:
        # t = 10000ms / 10000ms = 1.0 -> 1000 * (1 - 0.5 * 1.0) = 500
        points, streak = self.strategy.calculate(
            is_correct=True,
            base_points=1000,
            response_ms=10000,
            time_limit_s=10,
            current_streak=0,
        )
        assert points == 500
        assert streak == 1

    def test_over_deadline_clamped_to_50_percent(self) -> None:
        # t > deadline -> tỷ lệ bị kẹp ở 1.0 -> không bao giờ âm hoặc dưới 50%
        points, streak = self.strategy.calculate(
            is_correct=True,
            base_points=1000,
            response_ms=15000,
            time_limit_s=10,
            current_streak=0,
        )
        assert points == 500
        assert streak == 1

    def test_streak_progression_and_bonus(self) -> None:
        # current_streak = 0 -> new_streak = 1 (chưa thưởng)
        p1, s1 = self.strategy.calculate(
            is_correct=True, base_points=1000, response_ms=0, time_limit_s=10, current_streak=0
        )
        assert s1 == 1
        assert p1 == 1000

        # current_streak = 1 -> new_streak = 2 (chưa thưởng)
        p2, s2 = self.strategy.calculate(
            is_correct=True, base_points=1000, response_ms=0, time_limit_s=10, current_streak=1
        )
        assert s2 == 2
        assert p2 == 1000

        # current_streak = 2 -> new_streak = 3 (thưởng +10%: 1000 * 1.1 = 1100)
        p3, s3 = self.strategy.calculate(
            is_correct=True, base_points=1000, response_ms=0, time_limit_s=10, current_streak=2
        )
        assert s3 == 3
        assert p3 == 1100

        # current_streak = 3 -> new_streak = 4 (tiếp tục thưởng +10%: 1000 * 1.1 = 1100)
        p4, s4 = self.strategy.calculate(
            is_correct=True, base_points=1000, response_ms=0, time_limit_s=10, current_streak=3
        )
        assert s4 == 4
        assert p4 == 1100

        # Thưởng streak khi trả lời ở giữa (750 * 1.1 = 825)
        p_mid, s_mid = self.strategy.calculate(
            is_correct=True, base_points=1000, response_ms=5000, time_limit_s=10, current_streak=2
        )
        assert s_mid == 3
        assert p_mid == 825


class TestBinaryScoringStrategy:
    """Kiểm tra chiến lược Binary: đúng là ăn trọn điểm."""

    def setup_method(self) -> None:
        self.strategy = BinaryScoringStrategy()

    def test_incorrect_answer_yields_zero(self) -> None:
        points, streak = self.strategy.calculate(
            is_correct=False, base_points=500, response_ms=100, time_limit_s=10, current_streak=5
        )
        assert points == 0
        assert streak == 0

    def test_correct_answer_yields_full_regardless_of_time(self) -> None:
        points1, _ = self.strategy.calculate(
            is_correct=True, base_points=500, response_ms=0, time_limit_s=10, current_streak=0
        )
        points2, _ = self.strategy.calculate(
            is_correct=True, base_points=500, response_ms=9999, time_limit_s=10, current_streak=0
        )
        assert points1 == 500
        assert points2 == 500

    def test_streak_bonus_in_binary(self) -> None:
        points, streak = self.strategy.calculate(
            is_correct=True, base_points=500, response_ms=5000, time_limit_s=10, current_streak=2
        )
        assert streak == 3
        # 500 * 1.1 = 550
        assert points == 550


class TestCheckAnswerCorrectness:
    """Kiểm tra tính đúng sai theo câu single và multiple choice."""

    def test_single_choice(self) -> None:
        correct = {"opt_a"}
        assert check_answer_correctness("single", {"opt_a"}, correct) is True
        assert check_answer_correctness("single", {"opt_b"}, correct) is False
        assert check_answer_correctness("single", set(), correct) is False

    def test_multiple_choice_exact_match_rule(self) -> None:
        # Luật: Phải chọn đúng 100% tất cả các đáp án đúng, không chọn thừa đáp án sai
        correct = {"opt_a", "opt_c"}

        # Chọn đúng và đủ cả hai đáp án
        assert check_answer_correctness("multiple", {"opt_a", "opt_c"}, correct) is True

        # Chọn thiếu một đáp án đúng (chỉ chọn opt_a)
        assert check_answer_correctness("multiple", {"opt_a"}, correct) is False

        # Chọn đủ đáp án đúng nhưng chọn thừa một đáp án sai
        assert check_answer_correctness("multiple", {"opt_a", "opt_c", "opt_b"}, correct) is False

        # Chọn hoàn toàn sai
        assert check_answer_correctness("multiple", {"opt_b", "opt_d"}, correct) is False

        # Không chọn gì
        assert check_answer_correctness("multiple", set(), correct) is False


class TestGetScoringStrategy:
    """Kiểm tra lookup strategy theo id."""

    def test_lookup_strategies(self) -> None:
        assert isinstance(get_scoring_strategy("standard"), StandardScoringStrategy)
        assert isinstance(get_scoring_strategy("binary"), BinaryScoringStrategy)
        # Fallback về standard nếu truyền id không hợp lệ
        assert isinstance(get_scoring_strategy("unknown_strat"), StandardScoringStrategy)
