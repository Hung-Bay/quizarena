#!/usr/bin/env bash
# ==============================================================================
# QuizArena — SSH Deploy Wrapper (Least Privilege Execution)
# Giới hạn khóa SSH của GitHub Actions chỉ được phép chạy các lệnh deploy hợp lệ.
# ==============================================================================
set -euo pipefail

ORIGINAL_CMD="${SSH_ORIGINAL_COMMAND:-status}"

# Loại bỏ khoảng trắng thừa ở đầu/cuối
CMD_CLEAN=$(echo "$ORIGINAL_CMD" | xargs)

PROJECT_DIR="/opt/quizarena"
if [ ! -d "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

if [[ "$CMD_CLEAN" =~ ^deploy\ ([a-f0-9]{7,40})$ ]]; then
  COMMIT_SHA="${BASH_REMATCH[1]}"
  echo ">>> [WRAPPER] Xác thực lệnh deploy hợp lệ cho SHA: $COMMIT_SHA"
  exec "$PROJECT_DIR/infra/scripts/deploy.sh" "$COMMIT_SHA"

elif [[ "$CMD_CLEAN" =~ ^rollback\ ([a-f0-9]{7,40})$ ]]; then
  COMMIT_SHA="${BASH_REMATCH[1]}"
  echo ">>> [WRAPPER] Xác thực lệnh rollback hợp lệ cho SHA: $COMMIT_SHA"
  exec "$PROJECT_DIR/infra/scripts/deploy.sh" "$COMMIT_SHA"

elif [[ "$CMD_CLEAN" == "status" ]]; then
  echo ">>> [WRAPPER] Kiểm tra trạng thái hệ thống dịch vụ..."
  cd "$PROJECT_DIR"
  exec docker compose -f docker-compose.yml ps

elif [[ "$CMD_CLEAN" =~ ^renew(\ --dry-run)?$ ]]; then
  ARG="${BASH_REMATCH[1]:-}"
  echo ">>> [WRAPPER] Thực thi gia hạn chứng chỉ SSL${ARG:+ ($ARG)}..."
  cd "$PROJECT_DIR"
  exec "$PROJECT_DIR/infra/scripts/renew_ssl.sh" $ARG

else
  echo "LỖI AN NINH: Lệnh '$ORIGINAL_CMD' bị từ chối bởi QuizArena Deploy Wrapper!" >&2
  echo "Chỉ chấp nhận: 'deploy <sha>', 'rollback <sha>', 'status', hoặc 'renew [--dry-run]'." >&2
  exit 1
fi
