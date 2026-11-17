#!/usr/bin/env bash
# ==============================================================================
# QuizArena — Script Gia Hạn Chứng Chỉ Let's Encrypt (Hỗ trợ --dry-run)
# Sử dụng: ./renew_ssl.sh [--dry-run]
# ==============================================================================
set -euo pipefail

DRY_RUN=false
if [ "${1:-}" == "--dry-run" ]; then
  DRY_RUN=true
fi

PROJECT_DIR="/opt/quizarena"
if [ ! -d "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

cd "$PROJECT_DIR"
COMPOSE="docker compose -f docker-compose.yml"

echo "======================================================================"
echo "  [$(date -u +"%Y-%m-%dT%H:%M:%SZ")] BẮT ĐẦU KIỂM TRA GIA HẠN CHỨNG CHỈ SSL"
echo "  - Dry run mode: $DRY_RUN"
echo "======================================================================"

if [ "$DRY_RUN" = true ]; then
  echo ">>> Thực thi Certbot renew ở chế độ thử nghiệm (--dry-run)..."
  $COMPOSE run --rm certbot renew --dry-run
  echo ">>> Kiểm tra gia hạn --dry-run THÀNH CÔNG!"
else
  echo ">>> Thực thi gia hạn chứng chỉ thật..."
  $COMPOSE run --rm certbot renew --webroot -w /var/www/certbot

  echo ">>> Tải lại cấu hình Nginx để cập nhật chứng chỉ mới..."
  $COMPOSE exec -T nginx nginx -s reload || true
  echo ">>> Gia hạn chứng chỉ hoàn tất!"
fi
