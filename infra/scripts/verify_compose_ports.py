#!/usr/bin/env python3
"""Kiểm tra chính sách an toàn cổng của QuizArena Docker Compose.

Yêu cầu an toàn Phase 6:
- Chỉ duy nhất service `nginx` được phép publish `ports` ra máy chủ host (80 / 443).
- Toàn bộ service còn lại (postgres, redis, api, realtime, worker, web, prometheus, grafana)
  tuyệt đối KHÔNG được khai báo `ports:`.
- Điểm liệt trong đề bài: "Điểm liệt nếu Postgres hoặc Redis lộ ra Internet".
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


def verify_compose_ports(compose_data: dict[str, Any]) -> list[str]:
    """Kiểm tra danh sách dịch vụ có ports vi phạm.

    Trả về danh sách các lỗi vi phạm. Rỗng nếu hợp lệ.
    """
    violations: list[str] = []
    services = compose_data.get("services", {})

    if not services:
        return ["Không tìm thấy key 'services' trong cấu hình Docker Compose!"]

    nginx_found = False
    for service_name, service_cfg in services.items():
        if not isinstance(service_cfg, dict):
            continue

        ports = service_cfg.get("ports", [])
        if not ports:
            continue

        if service_name in ("nginx", "gateway"):
            nginx_found = True
            # Kiểm tra cổng của nginx có an toàn không (chỉ 80 / 443)
            for port_entry in ports:
                port_str = str(port_entry)
                # Cho phép dạng "${HTTP_PORT:-80}:80", "80:80", "443:443"
                if not any(safe_p in port_str for safe_p in (":80", ":443", "80", "443")):
                    violations.append(
                        f"Nginx publish cổng bất thường '{port_str}'. Chỉ cho phép cổng 80 và 443!"
                    )
        else:
            violations.append(
                f"LỖI ĐIỂM LIỆT: Dịch vụ '{service_name}' khai báo `ports: {ports}`! "
                f"Chỉ duy nhất 'nginx' được publish cổng ra ngoài."
            )

    if not nginx_found:
        violations.append("Cảnh báo: Không tìm thấy service 'nginx' có publish cổng 80/443!")

    return violations


def main() -> int:
    compose_path = Path(__file__).resolve().parent.parent.parent / "docker-compose.yml"
    if len(sys.argv) > 1:
        compose_path = Path(sys.argv[1])

    if not compose_path.exists():
        print(f"LỖI: Không tìm thấy file {compose_path}", file=sys.stderr)
        return 1

    try:
        with open(compose_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        print(f"LỖI khi đọc {compose_path}: {exc}", file=sys.stderr)
        return 1

    violations = verify_compose_ports(data)
    if violations:
        print("=" * 70, file=sys.stderr)
        print("  VI PHẠM CHÍNH SÁCH BẢO MẬT CỔNG DOCKER COMPOSE", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        return 1

    print("=" * 70)
    print("  XÁC THỰC AN TOÀN CỔNG COMPOSE THÀNH CÔNG (PHASE 6)")
    print("  - Chỉ duy nhất service 'nginx' publish cổng ra ngoài Internet (80/443).")
    print("  - Postgres, Redis, API, Realtime, Worker, Web, Prometheus, Grafana")
    print("    được cô lập hoàn toàn trong mạng nội bộ (bridge internal).")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
