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

# ==============================================================================
# Bảo vệ tương tranh (Concurrency Protection via flock)
# Dùng chung file lock với Autoscaler để tránh xung đột cập nhật replica
# ==============================================================================
LOCK_FILE="/var/lock/quizarena_deploy.lock"
mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null || LOCK_FILE="$PROJECT_DIR/.quizarena_deploy.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "LỖI: Một tiến trình deploy khác đang chạy hoặc lockfile đang bị giữ ($LOCK_FILE)." >&2
  exit 1
fi

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

# 3. Update Realtime (Rolling Update mượt mà qua Scale song song & Dừng tuần tự)
echo "Lấy danh sách các container realtime cũ đang phục vụ..."
OLD_REALTIME_IDS=$($COMPOSE ps -q realtime || true)

CURRENT_COUNT=$(echo "$OLD_REALTIME_IDS" | grep -c . || echo "1")
if [ "$CURRENT_COUNT" -eq 0 ]; then
  CURRENT_COUNT=1
fi
NEW_COUNT=$((CURRENT_COUNT * 2))

echo "Scale realtime lên $NEW_COUNT replicas (replica mới dùng image mới, replica cũ giữ nguyên)..."
$COMPOSE up -d --no-deps --scale realtime="$NEW_COUNT" --no-recreate realtime

# Chờ 3 giây để replica mới hoàn tất khởi động và sẵn sàng nhận kết nối
sleep 3

# Dừng tuần tự từng replica cũ theo ID để kích hoạt graceful drain 30s
for CID in $OLD_REALTIME_IDS; do
  if [ -n "$CID" ]; then
    echo "Dừng replica realtime cũ $CID (kích hoạt server.draining, timeout 30s)..."
    docker stop -t 30 "$CID" || true
    sleep 2
  fi
done

# Đưa scale trở lại số lượng replica chuẩn
$COMPOSE up -d --no-deps --scale realtime="$CURRENT_COUNT" --no-recreate realtime

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

if echo "$ACTIVE_VERSION" | grep -q "$TARGET_SHA"; then
  echo "✓ Xác nhận: Hệ thống đang phục vụ đúng phiên bản $TARGET_SHA."
else
  echo "CẢNH BÁO: Chưa thấy commit SHA $TARGET_SHA trong /api/v1/version (có thể replica đang khởi động)."
fi

echo "======================================================================"
echo "  DEPLOY HOÀN TẤT THÀNH CÔNG CHO PHIÊN BẢN $TARGET_SHA"
echo "======================================================================"
