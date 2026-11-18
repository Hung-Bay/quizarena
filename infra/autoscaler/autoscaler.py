"""QuizArena Realtime Autoscaler.

Tự động co giãn số lượng replica của dịch vụ realtime theo tải kết nối WebSocket.
Tuân thủ đầy đủ các tiêu chuẩn kỹ thuật Phase 7:
1. Công thức: desired = ceil(current * metric / target), giới hạn trong [min_replicas, max_replicas]
2. Scale-up: Ngay lập tức khi tải tăng (reactive).
3. Scale-down: Chỉ khi tải thấp liên tục >= cooldown_seconds (mặc định 300s = 5 phút),
   và mỗi lần chỉ giảm đúng 1 replica (step = -1) để tránh rung lắc (hysteresis).
4. Guard: Không bao giờ scale-down nếu còn trận đấu đang trong trạng thái QUESTION_ACTIVE (Redis check).
5. Guard: Kiểm tra ràng buộc tài nguyên Database:
   max_replicas * (pool_size + max_overflow) <= max_connections của Postgres.
6. File Lock (flock): Dùng chung lockfile với deploy.sh (/var/lock/quizarena_deploy.lock hoặc fallback),
   tự động bỏ qua chu kỳ nếu hệ thống đang deploy rolling update.
7. Emergency Switch: Dừng khẩn cấp nếu tồn tại file công tắc (mặc định AUTOSCALER_DISABLED).
8. Textfile Collector: Xuất metric `quizarena_replicas{service="realtime"}` ra file .prom tạm rồi atomic rename.
9. Hỗ trợ cờ CLI: --dry-run, --interval, --target, --min, --max, --once.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("autoscaler")


# ==============================================================================
# Cross-platform File Lock
# ==============================================================================
class FileLock:
    """Khóa tệp tương thích Linux (fcntl.flock) và Windows (msvcrt), hỗ trợ fallback non-blocking."""

    def __init__(self, lock_file: str | Path) -> None:
        self.lock_file = Path(lock_file)
        self._fd: int | None = None
        self._is_locked = False

    def acquire(self) -> bool:
        """Thử lấy khóa non-blocking. Trả về True nếu thành công, False nếu tệp đang bị khóa."""
        try:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)

            if sys.platform != "win32":
                import fcntl

                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._is_locked = True
                    return True
                except (BlockingIOError, OSError):
                    os.close(self._fd)
                    self._fd = None
                    return False
            else:
                import msvcrt

                try:
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                    self._is_locked = True
                    return True
                except OSError:
                    os.close(self._fd)
                    self._fd = None
                    return False
        except Exception as e:
            logger.warning("Không thể mở lockfile %s: %s", self.lock_file, e)
            return False

    def release(self) -> None:
        """Giải phóng khóa."""
        if not self._is_locked or self._fd is None:
            return
        try:
            if sys.platform != "win32":
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
            else:
                import msvcrt

                try:
                    msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        except Exception as e:
            logger.debug("Lỗi khi giải phóng khóa: %s", e)
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            self._is_locked = False

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


# ==============================================================================
# Interfaces & Adapters
# ==============================================================================
class MetricProvider(Protocol):
    def get_ws_connections(self) -> int:
        """Lấy tổng số kết nối WebSocket đang hoạt động."""
        ...


class RedisGuard(Protocol):
    def has_active_question(self) -> bool:
        """Kiểm tra xem có bất kỳ trận đấu nào đang ở trạng thái question_active hay không."""
        ...


class DockerComposeScaler(Protocol):
    def get_current_replicas(self, service: str) -> int:
        """Lấy số replica hiện tại của service."""
        ...

    def scale_service(self, service: str, count: int) -> bool:
        """Thực thi lệnh scale service."""
        ...


class PrometheusMetricProvider:
    """Truy vấn metric WebSocket connections từ Prometheus HTTP API."""

    def __init__(self, prometheus_url: str = "http://localhost:9090") -> None:
        self.prometheus_url = prometheus_url.rstrip("/")

    def get_ws_connections(self) -> int:
        import json
        import urllib.parse
        import urllib.request

        query = "sum(ws_connections) or vector(0)"
        url = f"{self.prometheus_url}/api/v1/query?query={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "QuizArena-Autoscaler"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("status") == "success":
                    result = data.get("data", {}).get("result", [])
                    if result:
                        val = result[0].get("value", [0, 0])[1]
                        return int(float(val))
        except Exception as e:
            logger.warning("Không thể đọc metric từ Prometheus (%s): %s", url, e)
        return 0


class RealRedisGuard:
    """Kiểm tra Redis xem có game nào đang ở trạng thái question_active."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self.redis_url = redis_url

    def has_active_question(self) -> bool:
        try:
            import redis

            r = redis.from_url(self.redis_url, decode_responses=True, socket_timeout=3)
            # Quét tất cả key game:*:state
            for key in r.scan_iter("game:*:state", count=100):
                status = r.hget(key, "status")
                if status == "question_active":
                    return True
            return False
        except Exception as e:
            logger.warning("Không thể kiểm tra Redis status: %s. Kích hoạt guard an toàn.", e)
            # An toàn: nếu không kết nối được Redis, giả định có trận đang diễn ra để tránh scale down làm mất dữ liệu
            return True


class SubprocessDockerScaler:
    """Điều khiển Docker Compose thông qua subprocess."""

    def __init__(
        self, compose_file: str = "docker-compose.yml", project_dir: str | Path = "."
    ) -> None:
        self.compose_file = compose_file
        self.project_dir = Path(project_dir).resolve()

    def get_current_replicas(self, service: str = "realtime") -> int:
        import subprocess

        cmd = [
            "docker",
            "compose",
            "-f",
            self.compose_file,
            "ps",
            "--filter",
            "status=running",
            "-q",
            service,
        ]
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
            return max(len(lines), 1)
        except Exception as e:
            logger.warning("Không thể lấy số replica hiện tại qua Docker CLI: %s", e)
            return 2

    def scale_service(self, service: str, count: int) -> bool:
        import subprocess

        cmd = [
            "docker",
            "compose",
            "-f",
            self.compose_file,
            "up",
            "-d",
            "--no-recreate",
            f"--scale={service}={count}",
            service,
        ]
        logger.info("Thực thi lệnh scale: %s", " ".join(cmd))
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if res.returncode != 0:
                logger.error("Lỗi docker compose scale: %s", res.stderr)
                return False
            return True
        except Exception as e:
            logger.error("Ngoại lệ khi thực thi docker compose scale: %s", e)
            return False


# ==============================================================================
# Autoscaler Core Configuration & Logic
# ==============================================================================
@dataclass
class AutoscalerConfig:
    service_name: str = "realtime"
    target_metric: int = 150  # WebSocket connections / replica
    min_replicas: int = 2
    max_replicas: int = 4
    scale_down_cooldown: float = 300.0  # 5 phút (300 giây)
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 5
    postgres_max_connections: int = 100
    lock_file: str = "/var/lock/quizarena_deploy.lock"
    disable_switch_file: str = "AUTOSCALER_DISABLED"
    textfile_path: str = "/var/lib/node_exporter/textfile_collector/quizarena_replicas.prom"
    dry_run: bool = False
    check_interval: float = 15.0

    def validate_database_limits(self) -> None:
        """Kiểm tra điều kiện an toàn: max_replicas * (pool_size + max_overflow) <= max_connections."""
        max_possible_connections = self.max_replicas * (
            self.postgres_pool_size + self.postgres_max_overflow
        )
        if max_possible_connections > self.postgres_max_connections:
            raise ValueError(
                f"Cấu hình autoscaler vi phạm ràng buộc kết nối Database Postgres: "
                f"max_replicas ({self.max_replicas}) * "
                f"(pool_size={self.postgres_pool_size} + max_overflow={self.postgres_max_overflow}) = "
                f"{max_possible_connections} > max_connections ({self.postgres_max_connections})."
            )


def calculate_desired_replicas(
    current_replicas: int,
    current_metric: int,
    target_metric: int,
    min_replicas: int,
    max_replicas: int,
) -> int:
    """Công thức: desired = ceil(current * metric / target), kẹp trong [min, max]."""
    if target_metric <= 0:
        return min_replicas
    if current_replicas <= 0:
        current_replicas = min_replicas

    # desired = ceil(current * metric / target)
    raw_desired = math.ceil(current_replicas * (current_metric / target_metric))
    # Giới hạn trong [min, max]
    clamped = max(min_replicas, min(max_replicas, raw_desired))
    return clamped


def export_metrics_textfile(filepath: str | Path, service: str, replicas: int) -> None:
    """Ghi metric Prometheus quizarena_replicas{service} vào textfile collector qua atomic rename."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    content = (
        f"# HELP quizarena_replicas Số lượng container replica đang hoạt động của service\n"
        f"# TYPE quizarena_replicas gauge\n"
        f'quizarena_replicas{{service="{service}"}} {replicas}\n'
    )
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


class AutoscalerEngine:
    def __init__(
        self,
        config: AutoscalerConfig,
        metric_provider: MetricProvider,
        redis_guard: RedisGuard,
        scaler: DockerComposeScaler,
    ) -> None:
        self.config = config
        self.metric_provider = metric_provider
        self.redis_guard = redis_guard
        self.scaler = scaler

        self.config.validate_database_limits()

        # State theo dõi cooldown scale down
        self.last_scale_time: float = time.time()
        self.scale_down_candidate_since: float | None = None

    def is_disabled(self) -> bool:
        """Kiểm tra công tắc dừng khẩn cấp (file disable)."""
        switch_path = Path(self.config.disable_switch_file)
        return switch_path.exists()

    def evaluate_and_step(self, now: float | None = None) -> tuple[int, str]:
        """Thực thi một chu kỳ đánh giá scaling logic.

        Trả về (replicas_sau_khi_danh_gia, ly_do_hoac_trang_thai)
        """
        current_time = now if now is not None else time.time()

        # 1. Kiểm tra Emergency Switch
        if self.is_disabled():
            msg = f"Autoscaler đang bị TẮT khẩn cấp bởi tệp {self.config.disable_switch_file}."
            logger.warning(msg)
            return (self.scaler.get_current_replicas(self.config.service_name), msg)

        # 2. Kiểm tra Deploy Lock
        lock = FileLock(self.config.lock_file)
        if not lock.acquire():
            msg = (
                "Hệ thống đang chạy deploy rolling update (lockfile bận), bỏ qua chu kỳ autoscale."
            )
            logger.info(msg)
            return (self.scaler.get_current_replicas(self.config.service_name), msg)

        try:
            current_replicas = self.scaler.get_current_replicas(self.config.service_name)
            ws_connections = self.metric_provider.get_ws_connections()

            # Tính toán desired replicas theo công thức
            desired = calculate_desired_replicas(
                current_replicas=current_replicas,
                current_metric=ws_connections,
                target_metric=self.config.target_metric,
                min_replicas=self.config.min_replicas,
                max_replicas=self.config.max_replicas,
            )

            # Trường hợp 1: Scale-up (ngay lập tức)
            if desired > current_replicas:
                self.scale_down_candidate_since = None
                action_msg = (
                    f"SCALE-UP: {current_replicas} -> {desired} (WS Connections={ws_connections}, "
                    f"Target={self.config.target_metric})"
                )
                logger.info(action_msg)
                if not self.config.dry_run:
                    success = self.scaler.scale_service(self.config.service_name, desired)
                    if success:
                        current_replicas = desired
                        self.last_scale_time = current_time
                        export_metrics_textfile(
                            self.config.textfile_path, self.config.service_name, current_replicas
                        )
                else:
                    logger.info("[DRY-RUN] Giả lập scale-up lên %d replicas", desired)
                    current_replicas = desired
                return (current_replicas, action_msg)

            # Trường hợp 2: Muốn Scale-down (cần cooldown và kiểm tra game state)
            elif desired < current_replicas:
                # 2.1 Kiểm tra Redis Game State Guard: không bao giờ scale down khi có phòng QUESTION_ACTIVE
                if self.redis_guard.has_active_question():
                    self.scale_down_candidate_since = None
                    action_msg = (
                        f"CHẶN SCALE-DOWN: Phát hiện trận đấu đang trong trạng thái QUESTION_ACTIVE. "
                        f"Giữ nguyên {current_replicas} replicas."
                    )
                    logger.info(action_msg)
                    return (current_replicas, action_msg)

                # 2.2 Đánh giá Cooldown (>= 5 phút = 300s)
                if self.scale_down_candidate_since is None:
                    self.scale_down_candidate_since = current_time
                    action_msg = (
                        f"Bắt đầu theo dõi điều kiện scale-down: Desired={desired} < Current={current_replicas}. "
                        f"Cần ổn định trong {self.config.scale_down_cooldown}s."
                    )
                    logger.info(action_msg)
                    return (current_replicas, action_msg)

                elapsed_cooldown = current_time - self.scale_down_candidate_since
                if elapsed_cooldown < self.config.scale_down_cooldown:
                    action_msg = (
                        f"ĐANG CHỜ COOLDOWN SCALE-DOWN: Đã duy trì {elapsed_cooldown:.1f}s / "
                        f"{self.config.scale_down_cooldown}s. Giữ nguyên {current_replicas} replicas."
                    )
                    logger.debug(action_msg)
                    return (current_replicas, action_msg)

                # Cooldown hoàn tất: Giảm đúng 1 replica (bớt 1 mỗi lần)
                new_replicas = current_replicas - 1
                action_msg = (
                    f"SCALE-DOWN (Step -1): {current_replicas} -> {new_replicas} "
                    f"(Duy trì tải thấp trong {elapsed_cooldown:.1f}s, WS Connections={ws_connections})"
                )
                logger.info(action_msg)

                # Reset bộ đếm cooldown cho bước giảm tiếp theo
                self.scale_down_candidate_since = None
                self.last_scale_time = current_time

                if not self.config.dry_run:
                    success = self.scaler.scale_service(self.config.service_name, new_replicas)
                    if success:
                        current_replicas = new_replicas
                        export_metrics_textfile(
                            self.config.textfile_path, self.config.service_name, current_replicas
                        )
                else:
                    logger.info("[DRY-RUN] Giả lập scale-down xuống %d replicas", new_replicas)
                    current_replicas = new_replicas
                return (current_replicas, action_msg)

            # Trường hợp 3: Tải ổn định, desired == current
            else:
                self.scale_down_candidate_since = None
                action_msg = f"ỔN ĐỊNH: {current_replicas} replicas (WS={ws_connections}, Target={self.config.target_metric})"
                logger.debug(action_msg)
                if not self.config.dry_run:
                    try:
                        export_metrics_textfile(
                            self.config.textfile_path, self.config.service_name, current_replicas
                        )
                    except Exception as e:
                        logger.debug("Không thể ghi textfile metrics: %s", e)
                return (current_replicas, action_msg)

        finally:
            lock.release()


# ==============================================================================
# CLI Entrypoint
# ==============================================================================
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QuizArena Realtime Service Autoscaler")
    parser.add_argument("--service", default="realtime", help="Tên Docker service cần scale")
    parser.add_argument(
        "--target", type=int, default=150, help="Ngưỡng kết nối WS mục tiêu trên mỗi replica"
    )
    parser.add_argument("--min", type=int, default=2, help="Số replica tối thiểu")
    parser.add_argument("--max", type=int, default=4, help="Số replica tối đa")
    parser.add_argument(
        "--cooldown", type=float, default=300.0, help="Thời gian chờ scale-down (giây)"
    )
    parser.add_argument(
        "--interval", type=float, default=15.0, help="Chu kỳ kiểm tra định kỳ (giây)"
    )
    parser.add_argument(
        "--prometheus-url", default="http://localhost:9090", help="URL máy chủ Prometheus"
    )
    parser.add_argument("--redis-url", default="redis://localhost:6379/0", help="URL kết nối Redis")
    parser.add_argument(
        "--lock-file", default="/var/lock/quizarena_deploy.lock", help="Đường dẫn tệp khóa deploy"
    )
    parser.add_argument(
        "--disable-file", default="AUTOSCALER_DISABLED", help="Tệp công tắc tắt khẩn cấp autoscaler"
    )
    parser.add_argument(
        "--textfile-path",
        default="/var/lib/node_exporter/textfile_collector/quizarena_replicas.prom",
        help="Đường dẫn file metric textfile collector",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chế độ kiểm tra quyết định tính toán mà không thực hiện scale thực tế",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Chạy một chu kỳ duy nhất rồi thoát (thích hợp test/cron)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [Autoscaler] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = AutoscalerConfig(
        service_name=args.service,
        target_metric=args.target,
        min_replicas=args.min,
        max_replicas=args.max,
        scale_down_cooldown=args.cooldown,
        check_interval=args.interval,
        lock_file=args.lock_file,
        disable_switch_file=args.disable_file,
        textfile_path=args.textfile_path,
        dry_run=args.dry_run,
    )

    try:
        config.validate_database_limits()
    except ValueError as e:
        logger.error("LỖI CẤU HÌNH TÀI NGUYÊN: %s", e)
        return 1

    metric_provider = PrometheusMetricProvider(prometheus_url=args.prometheus_url)
    redis_guard = RealRedisGuard(redis_url=args.redis_url)
    scaler = SubprocessDockerScaler()

    engine = AutoscalerEngine(
        config=config,
        metric_provider=metric_provider,
        redis_guard=redis_guard,
        scaler=scaler,
    )

    logger.info(
        "Khởi động QuizArena Autoscaler: service=%s, target=%d, min=%d, max=%d, cooldown=%.1fs, dry_run=%s",
        config.service_name,
        config.target_metric,
        config.min_replicas,
        config.max_replicas,
        config.scale_down_cooldown,
        config.dry_run,
    )

    if args.once:
        replicas, msg = engine.evaluate_and_step()
        logger.info("Kết quả đánh giá chu kỳ đơn: %s (Replicas=%d)", msg, replicas)
        return 0

    try:
        while True:
            try:
                engine.evaluate_and_step()
            except Exception as e:
                logger.error("Lỗi trong chu kỳ autoscale: %s", e, exc_info=True)
            time.sleep(config.check_interval)
    except KeyboardInterrupt:
        logger.info("Dừng Autoscaler theo yêu cầu người dùng.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
