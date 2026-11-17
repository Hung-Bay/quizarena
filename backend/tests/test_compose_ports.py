import sys
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from infra.scripts.verify_compose_ports import verify_compose_ports  # noqa: E402


def test_docker_compose_only_nginx_publishes_ports():
    """Kiểm tra điểm liệt Phase 6: Chỉ duy nhất Nginx được publish cổng ra ngoài.

    Postgres, Redis, API, Realtime, Worker, Web, Prometheus, Grafana tuyệt đối
    không được khai báo `ports:`.
    """
    root_dir = Path(__file__).resolve().parent.parent.parent
    compose_path = root_dir / "docker-compose.yml"
    assert compose_path.exists(), f"Không tìm thấy file {compose_path}"

    with open(compose_path, encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    violations = verify_compose_ports(compose_data)
    assert not violations, "Phát hiện vi phạm an toàn cổng:\n" + "\n".join(violations)


def test_verify_compose_ports_detects_exposed_postgres():
    """Kiểm tra logic validator bắt được khi Postgres vô tình bị mở ports."""
    fake_compose = {
        "services": {
            "nginx": {"ports": ["80:80"]},
            "postgres": {"ports": ["5432:5432"]},
        }
    }
    violations = verify_compose_ports(fake_compose)
    assert len(violations) == 1
    assert "postgres" in violations[0]
    assert "LỖI ĐIỂM LIỆT" in violations[0]


def test_verify_compose_ports_detects_exposed_redis():
    """Kiểm tra logic validator bắt được khi Redis vô tình bị mở ports."""
    fake_compose = {
        "services": {
            "nginx": {"ports": ["80:80"]},
            "redis": {"ports": ["6379:6379"]},
        }
    }
    violations = verify_compose_ports(fake_compose)
    assert len(violations) == 1
    assert "redis" in violations[0]
    assert "LỖI ĐIỂM LIỆT" in violations[0]


def test_verify_compose_ports_detects_exposed_certbot():
    """Kiểm tra logic validator bắt được khi certbot vô tình bị mở ports."""
    fake_compose = {
        "services": {
            "nginx": {"ports": ["80:80", "443:443"]},
            "certbot": {"ports": ["8080:8080"]},
        }
    }
    violations = verify_compose_ports(fake_compose)
    assert len(violations) == 1
    assert "certbot" in violations[0]
    assert "LỖI ĐIỂM LIỆT" in violations[0]


def test_verify_compose_ports_detects_unsafe_nginx_port():
    """Kiểm tra logic validator bắt được khi Nginx publish cổng lạ ngoài 80 và 443."""
    fake_compose = {
        "services": {
            "nginx": {"ports": ["80:80", "8888:8888"]},
        }
    }
    violations = verify_compose_ports(fake_compose)
    assert len(violations) == 1
    assert "8888" in violations[0]
