"""Kiểm thử đơn vị và tích hợp cho QuizArena Realtime Autoscaler.

Kiểm tra:
1. Công thức mong muốn: desired = ceil(current * metric / target), kẹp trong [min, max].
2. Scale-up: Lập tức khi tải tăng (reactive).
3. Scale-down: Cooldown >= 5 phút (300s) và step = -1 (mỗi lần chỉ bớt 1).
4. Guard Redis: Chặn scale down khi bất kỳ phòng nào có status = QUESTION_ACTIVE.
5. Guard Database: max_replicas * (pool_size + max_overflow) <= max_connections.
6. Concurrency / Lockfile: Bỏ qua chu kỳ khi file lock bị chiếm (đang deploy).
7. Emergency Switch: Dừng hành động khi file AUTOSCALER_DISABLED tồn tại.
8. Textfile metric export: Ghi atomic .prom cho Prometheus textfile collector.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Thêm đường dẫn project root để import infra.autoscaler.autoscaler
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from infra.autoscaler.autoscaler import (  # noqa: E402
    AutoscalerConfig,
    AutoscalerEngine,
    FileLock,
    calculate_desired_replicas,
    export_metrics_textfile,
)


# ==============================================================================
# Fakes & Mocks
# ==============================================================================
class FakeMetricProvider:
    def __init__(self, connections: int = 0) -> None:
        self.connections = connections

    def get_ws_connections(self) -> int:
        return self.connections


class FakeRedisGuard:
    def __init__(self, active: bool = False) -> None:
        self.active = active

    def has_active_question(self) -> bool:
        return self.active


class FakeDockerScaler:
    def __init__(self, initial_replicas: int = 2) -> None:
        self.replicas = initial_replicas
        self.scale_calls: list[tuple[str, int]] = []

    def get_current_replicas(self, service: str) -> int:
        return self.replicas

    def scale_service(self, service: str, count: int) -> bool:
        self.scale_calls.append((service, count))
        self.replicas = count
        return True


# ==============================================================================
# Test Cases
# ==============================================================================
class TestAutoscalerFormula:
    def test_calculate_desired_replicas_basic(self) -> None:
        """Công thức desired = ceil(current * metric / target)."""
        # Hiện tại 2 replica, 150 conn/replica mục tiêu
        # 100 kết nối -> 2 * 100 / 150 = 1.333 -> ceil = 2. Kẹp trong [2, 4] -> 2
        assert calculate_desired_replicas(2, 100, 150, 2, 4) == 2

        # 300 kết nối -> 2 * 300 / 150 = 4.0 -> ceil = 4. Kẹp trong [2, 4] -> 4
        assert calculate_desired_replicas(2, 300, 150, 2, 4) == 4

        # 160 kết nối -> 2 * 160 / 150 = 2.133 -> ceil = 3
        assert calculate_desired_replicas(2, 160, 150, 2, 4) == 3

        # Quá tải vượt max: 1000 kết nối -> ceil = 14 -> kẹp max = 4
        assert calculate_desired_replicas(2, 1000, 150, 2, 4) == 4

        # Rất thấp: 10 kết nối -> ceil = 1 -> kẹp min = 2
        assert calculate_desired_replicas(4, 10, 150, 2, 4) == 2

    def test_demo_calculation_case(self) -> None:
        """Kiểm tra case ghi chú trong tài liệu thiết kế:

        Demo target 75 để thấy bậc 2 -> 3 -> 4 khi bot vào.
        """
        # Target demo = 75, current = 2
        # 120 bots -> 2 * 120 / 75 = 3.2 -> ceil = 4
        assert calculate_desired_replicas(2, 120, 75, 2, 4) == 4

        # 80 bots -> 2 * 80 / 75 = 2.13 -> ceil = 3
        assert calculate_desired_replicas(2, 80, 75, 2, 4) == 3


class TestDatabaseConnectionConstraint:
    def test_valid_db_limits(self) -> None:
        cfg = AutoscalerConfig(
            max_replicas=4,
            postgres_pool_size=5,
            postgres_max_overflow=5,
            postgres_max_connections=100,
        )
        # 4 * (5 + 5) = 40 <= 100 -> Hợp lệ
        cfg.validate_database_limits()

    def test_invalid_db_limits_raises_error(self) -> None:
        cfg = AutoscalerConfig(
            max_replicas=15,
            postgres_pool_size=10,
            postgres_max_overflow=5,
            postgres_max_connections=100,
        )
        # 15 * (10 + 5) = 225 > 100 -> Vi phạm
        with pytest.raises(ValueError, match="vi phạm ràng buộc kết nối Database Postgres"):
            cfg.validate_database_limits()


class TestScaleUpAndScaleDownHysteresis:
    def test_scale_up_immediately(self, tmp_path: Path) -> None:
        """Khi tải tăng, scale up lập tức (không chờ cooldown)."""
        cfg = AutoscalerConfig(
            min_replicas=2,
            max_replicas=4,
            target_metric=100,
            lock_file=str(tmp_path / "deploy.lock"),
            disable_switch_file=str(tmp_path / "AUTOSCALER_DISABLED"),
            textfile_path=str(tmp_path / "replicas.prom"),
        )
        metric = FakeMetricProvider(connections=300)  # Cần 4 replicas
        redis_guard = FakeRedisGuard(active=False)
        scaler = FakeDockerScaler(initial_replicas=2)

        engine = AutoscalerEngine(cfg, metric, redis_guard, scaler)

        now = 1000.0
        replicas, msg = engine.evaluate_and_step(now=now)

        assert replicas == 4
        assert scaler.replicas == 4
        assert scaler.scale_calls == [("realtime", 4)]
        assert "SCALE-UP: 2 -> 4" in msg

    def test_scale_down_cooldown_and_step_minus_one(self, tmp_path: Path) -> None:
        """Khi tải giảm, phải chờ đủ 5 phút cooldown và mỗi lần chỉ giảm 1 replica."""
        cfg = AutoscalerConfig(
            min_replicas=2,
            max_replicas=4,
            target_metric=100,
            scale_down_cooldown=300.0,
            lock_file=str(tmp_path / "deploy.lock"),
            disable_switch_file=str(tmp_path / "AUTOSCALER_DISABLED"),
            textfile_path=str(tmp_path / "replicas.prom"),
        )
        metric = FakeMetricProvider(connections=50)  # Tải thấp, desired = 2
        redis_guard = FakeRedisGuard(active=False)
        scaler = FakeDockerScaler(initial_replicas=4)  # Đang có 4 replicas

        engine = AutoscalerEngine(cfg, metric, redis_guard, scaler)

        start_time = 1000.0
        # Lần 1: Bắt đầu phát hiện tải thấp, ghi nhận thời điểm candidate, chưa scale
        rep, msg = engine.evaluate_and_step(now=start_time)
        assert rep == 4
        assert scaler.replicas == 4
        assert len(scaler.scale_calls) == 0
        assert "Bắt đầu theo dõi điều kiện scale-down" in msg

        # Lần 2: Sau 120s (chưa đủ 300s cooldown) -> Giữ nguyên 4
        rep, msg = engine.evaluate_and_step(now=start_time + 120.0)
        assert rep == 4
        assert len(scaler.scale_calls) == 0
        assert "ĐANG CHỜ COOLDOWN" in msg

        # Lần 3: Sau 301s (đã qua 300s cooldown) -> Scale-down đúng -1 (từ 4 xuống 3)
        rep, msg = engine.evaluate_and_step(now=start_time + 301.0)
        assert rep == 3
        assert scaler.replicas == 3
        assert scaler.scale_calls == [("realtime", 3)]
        assert "SCALE-DOWN (Step -1): 4 -> 3" in msg

        # Lần 4: Lập tức sau đó tại 302s, bắt đầu chu kỳ cooldown mới để xuống 2
        rep, msg = engine.evaluate_and_step(now=start_time + 302.0)
        assert rep == 3
        assert "Bắt đầu theo dõi điều kiện scale-down" in msg

        # Lần 5: Sau thêm 300s nữa (tại 605s) -> Giảm tiếp từ 3 xuống 2
        rep, msg = engine.evaluate_and_step(now=start_time + 605.0)
        assert rep == 2
        assert scaler.replicas == 2
        assert scaler.scale_calls == [("realtime", 3), ("realtime", 2)]
        assert "SCALE-DOWN (Step -1): 3 -> 2" in msg


class TestGameActiveGuard:
    def test_scale_down_blocked_when_question_active(self, tmp_path: Path) -> None:
        """Nếu Redis báo còn phòng đang QUESTION_ACTIVE, không bao giờ scale-down."""
        cfg = AutoscalerConfig(
            min_replicas=2,
            max_replicas=4,
            target_metric=100,
            scale_down_cooldown=300.0,
            lock_file=str(tmp_path / "deploy.lock"),
            disable_switch_file=str(tmp_path / "AUTOSCALER_DISABLED"),
            textfile_path=str(tmp_path / "replicas.prom"),
        )
        metric = FakeMetricProvider(connections=20)  # Tải rất thấp
        redis_guard = FakeRedisGuard(active=True)  # Có trận đang chạy!
        scaler = FakeDockerScaler(initial_replicas=4)

        engine = AutoscalerEngine(cfg, metric, redis_guard, scaler)

        now = 1000.0
        rep, msg = engine.evaluate_and_step(now=now + 500.0)

        assert rep == 4
        assert scaler.replicas == 4
        assert len(scaler.scale_calls) == 0
        assert "CHẶN SCALE-DOWN" in msg
        assert "QUESTION_ACTIVE" in msg


class TestSafetyAndConcurrencySwitches:
    def test_emergency_disabled_switch(self, tmp_path: Path) -> None:
        """Tệp công tắc AUTOSCALER_DISABLED tồn tại -> Autoscaler tạm dừng."""
        switch_file = tmp_path / "AUTOSCALER_DISABLED"
        switch_file.touch()

        cfg = AutoscalerConfig(
            min_replicas=2,
            max_replicas=4,
            target_metric=100,
            disable_switch_file=str(switch_file),
            lock_file=str(tmp_path / "deploy.lock"),
            textfile_path=str(tmp_path / "replicas.prom"),
        )
        metric = FakeMetricProvider(connections=500)
        redis_guard = FakeRedisGuard(active=False)
        scaler = FakeDockerScaler(initial_replicas=2)

        engine = AutoscalerEngine(cfg, metric, redis_guard, scaler)
        rep, msg = engine.evaluate_and_step()

        assert rep == 2
        assert len(scaler.scale_calls) == 0
        assert "TẮT khẩn cấp" in msg

    def test_deploy_lock_skips_cycle(self, tmp_path: Path) -> None:
        """Lockfile đang được giữ bởi deploy.sh -> Autoscaler bỏ qua chu kỳ."""
        lock_file = tmp_path / "deploy.lock"
        holder_lock = FileLock(lock_file)
        assert holder_lock.acquire() is True

        try:
            cfg = AutoscalerConfig(
                min_replicas=2,
                max_replicas=4,
                target_metric=100,
                lock_file=str(lock_file),
                disable_switch_file=str(tmp_path / "AUTOSCALER_DISABLED"),
                textfile_path=str(tmp_path / "replicas.prom"),
            )
            metric = FakeMetricProvider(connections=500)
            redis_guard = FakeRedisGuard(active=False)
            scaler = FakeDockerScaler(initial_replicas=2)

            engine = AutoscalerEngine(cfg, metric, redis_guard, scaler)
            rep, msg = engine.evaluate_and_step()

            assert rep == 2
            assert len(scaler.scale_calls) == 0
            assert "bỏ qua chu kỳ autoscale" in msg
        finally:
            holder_lock.release()

    def test_dry_run_mode(self, tmp_path: Path) -> None:
        """Chế độ dry-run tính toán nhưng không gọi scale thực tế."""
        cfg = AutoscalerConfig(
            min_replicas=2,
            max_replicas=4,
            target_metric=100,
            dry_run=True,
            lock_file=str(tmp_path / "deploy.lock"),
            disable_switch_file=str(tmp_path / "AUTOSCALER_DISABLED"),
            textfile_path=str(tmp_path / "replicas.prom"),
        )
        metric = FakeMetricProvider(connections=350)
        redis_guard = FakeRedisGuard(active=False)
        scaler = FakeDockerScaler(initial_replicas=2)

        engine = AutoscalerEngine(cfg, metric, redis_guard, scaler)
        rep, msg = engine.evaluate_and_step()

        assert rep == 4
        # Scaler thực tế không bị gọi lệnh
        assert len(scaler.scale_calls) == 0


class TestTextfileMetricExport:
    def test_export_metrics_textfile_atomic(self, tmp_path: Path) -> None:
        """Ghi metric quizarena_replicas ra file textfile collector."""
        prom_file = tmp_path / "quizarena_replicas.prom"
        export_metrics_textfile(prom_file, "realtime", 3)

        assert prom_file.exists()
        content = prom_file.read_text(encoding="utf-8")
        assert 'quizarena_replicas{service="realtime"} 3' in content
        assert not prom_file.with_suffix(".tmp").exists()
