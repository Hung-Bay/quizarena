#!/usr/bin/env bash
# ==============================================================================
# QuizArena — Production Deploy & Rolling Update Script
# Sử dụng: ./deploy.sh <COMMIT_SHA>
# ==============================================================================
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Sử dụng: $0 <COMMIT_SHA>" >&2
  exit 1
fi

TARGET_SHA="$1"
if ! [[ "$TARGET_SHA" =~ ^[a-f0-9]{7,40}$ ]]; then
  echo "LỖI: Commit SHA '$TARGET_SHA' không hợp lệ!" >&2
  exit 1
fi

PROJECT_DIR="/opt/quizarena"
if [ ! -d "$PROJECT_DIR" ]; then
  # Fallback cho chạy local test
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

cd "$PROJECT_DIR"
echo ">>> [1/6] Bắt đầu deploy phiên bản SHA: $TARGET_SHA tại $PROJECT_DIR"

# Đảm bảo luôn truyền -f docker-compose.yml để không nạp nhầm docker-compose.override.yml
COMPOSE="docker compose -f docker-compose.yml"

# Kiểm tra an toàn cổng trước khi deploy
python3 infra/scripts/verify_compose_ports.py docker-compose.yml

# Đồng bộ repo về đúng commit SHA nếu là git working copy
if [ -d ".git" ]; then
  echo ">>> [2/6] Đồng bộ mã nguồn và cấu hình về SHA: $TARGET_SHA..."
  git fetch --depth 50 origin || true
  git checkout "$TARGET_SHA"
fi

export TAG="$TARGET_SHA"
echo ">>> [3/6] Pull các container image tương ứng với TAG=$TAG..."
$COMPOSE pull api realtime worker web nginx || true

echo ">>> [4/6] Chạy Alembic Database Migration (Nguyên tắc tương thích ngược)..."
# Chạy migrate trước. Nếu migration lỗi, dừng deploy ngay lập tức khi chưa động tới replica
$COMPOSE run --rm migrate

echo ">>> [5/6] Tiến hành Rolling Update ứng dụng..."
# 1. Update frontend web (không ảnh hưởng tới gateway hay WebSocket)
$COMPOSE up -d --no-deps web

# 2. Update API REST
$COMPOSE up -d --no-deps api

# 3. Update Realtime (dừng êm qua SIGTERM drain 30s)
$COMPOSE up -d --no-deps realtime

# 4. Update Worker
$COMPOSE up -d --no-deps worker

# 5. Reload cấu hình Nginx gateway (không ngắt kết nối WebSocket đang hoạt động)
$COMPOSE exec -T nginx nginx -s reload || $COMPOSE up -d --no-deps nginx

echo ">>> [6/6] Dọn dẹp image cũ và kiểm tra phiên bản hoạt động..."
# Dọn dẹp image cũ hơn 72 giờ để không làm đầy ổ đĩa VM Always Free
docker image prune -af --filter "until=72h" || true

echo "Đang kiểm tra endpoint /api/v1/version trên localhost..."
ACTIVE_VERSION=$(curl -sSf http://127.0.0.1/api/v1/version || echo '{"commit_sha":"unknown"}')
echo "Phiên bản phản hồi từ hệ thống: $ACTIVE_VERSION"

echo "======================================================================"
echo "  DEPLOY HOÀN TẤT THÀNH CÔNG CHO PHIÊN BẢN $TARGET_SHA"
echo "======================================================================"
