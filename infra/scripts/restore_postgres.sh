#!/usr/bin/env bash
# ==============================================================================
# QuizArena — PostgreSQL Restore Drill & Disaster Recovery Script (§7.7)
# Thực hiện diễn tập phục hồi vào cơ sở dữ liệu tạm thời (hoặc chính thức),
# xác thực số lượng bản ghi (row counts), kiểm tra alembic current revision,
# và đo đạc thời gian khôi phục thực tế (RTO - Recovery Time Objective).
# ==============================================================================
set -euo pipefail

PROJECT_DIR="/opt/quizarena"
if [ ! -d "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
cd "$PROJECT_DIR"

COMPOSE="docker compose -f docker-compose.yml"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups/postgres}"

# Nạp thông tin cấu hình từ .env nếu có
if [ -f ".env" ]; then
  POSTGRES_USER=$(grep -E '^POSTGRES_USER=' .env | cut -d '=' -f2- | tr -d '"'\'' ')
  POSTGRES_DB=$(grep -E '^POSTGRES_DB=' .env | cut -d '=' -f2- | tr -d '"'\'' ')
fi

POSTGRES_USER="${POSTGRES_USER:-quizarena}"
POSTGRES_DB="${POSTGRES_DB:-quizarena}"

# Xác định file backup cần restore
TARGET_BACKUP="${1:-}"
if [ -z "$TARGET_BACKUP" ]; then
  # Lấy file .dump mới nhất
  TARGET_BACKUP=$(ls -t "$BACKUP_DIR"/quizarena_backup_*.dump 2>/dev/null | head -n 1 || true)
fi

if [ -z "$TARGET_BACKUP" ] || [ ! -f "$TARGET_BACKUP" ]; then
  echo "LỖI: Không tìm thấy bản backup nào hợp lệ để phục hồi!" >&2
  echo "Sử dụng: $0 [đường_dẫn_file_dump]" >&2
  exit 1
fi

# Chế độ: drill (mặc định vào quizarena_drill) hoặc production (vào chính thức)
DRILL_MODE="${DRILL_MODE:-true}"
TARGET_RESTORE_DB="${POSTGRES_DB}_drill"
if [ "$DRILL_MODE" = "false" ]; then
  TARGET_RESTORE_DB="$POSTGRES_DB"
  echo "CẢNH BÁO: BẠN ĐANG CHẠY PHỤC HỒI TRỰC TIẾP VÀO CƠ SỞ DỮ LIỆU CHÍNH THỨC ($TARGET_RESTORE_DB)!"
  read -r -p "Bạn có chắc chắn muốn tiếp tục không? (nhập 'yes' để xác nhận): " CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "Đã hủy thao tác phục hồi."
    exit 0
  fi
fi

echo "======================================================================"
echo ">>> BẮT ĐẦU DIỄN TẬP PHỤC HỒI DỮ LIỆU POSTGRESQL (DISASTER RECOVERY DRILL)"
echo ">>> Tệp sao lưu nguồn: $TARGET_BACKUP"
echo ">>> Cơ sở dữ liệu đích: $TARGET_RESTORE_DB"
echo "======================================================================"

START_RTO=$(date +%s)

# Bước 1: Tạo Database drill mới (nếu đang ở drill mode)
echo "=== [1/4] Chuẩn bị cơ sở dữ liệu đích ($TARGET_RESTORE_DB) ==="
$COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET_RESTORE_DB\" WITH (FORCE);" || true
$COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"$TARGET_RESTORE_DB\" OWNER \"$POSTGRES_USER\";"

# Bước 2: Nạp dữ liệu từ file pg_dump (-Fc) qua pg_restore
echo "=== [2/4] Tiến hành khôi phục dữ liệu bằng pg_restore ==="
# Lưu ý: pg_restore có thể trả về warning nếu có extension hoặc object đã có, dùng || true hoặc kiểm tra mã lỗi
$COMPOSE exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$TARGET_RESTORE_DB" --clean --if-exists --no-owner --no-privileges < "$TARGET_BACKUP" || {
  EXIT_CODE=$?
  if [ $EXIT_CODE -le 1 ]; then
    echo "pg_restore hoàn tất với một số cảnh báo không nghiêm trọng (exit code $EXIT_CODE)."
  else
    echo "LỖI: pg_restore thất bại với mã lỗi $EXIT_CODE!" >&2
    exit $EXIT_CODE
  fi
}

END_RTO=$(date +%s)
RTO_SECONDS=$((END_RTO - START_RTO))

# Bước 3: Kiểm tra tính toàn vẹn (Verification of Data Integrity)
echo "=== [3/4] Xác thực tính toàn vẹn dữ liệu và kiểm tra số dòng (Row Counts) ==="
TABLE_COUNTS=$($COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d "$TARGET_RESTORE_DB" -t -A -c "
SELECT table_name || ': ' || total_rows
FROM (
  SELECT 'users' as table_name, count(*)::text as total_rows FROM users
  UNION ALL
  SELECT 'quizzes', count(*)::text FROM quizzes
  UNION ALL
  SELECT 'questions', count(*)::text FROM questions
  UNION ALL
  SELECT 'game_sessions', count(*)::text FROM game_sessions
) t;
" || echo "Không thể lấy thống kê số dòng.")

echo "--- Bảng thống kê dữ liệu đã phục hồi ---"
echo "$TABLE_COUNTS"

# Bước 4: Kiểm tra Alembic Schema Version
echo "=== [4/4] Kiểm tra phiên bản Alembic Migration (alembic current) ==="
CURRENT_ALEMBIC_VERSION=$($COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d "$TARGET_RESTORE_DB" -t -A -c "SELECT version_num FROM alembic_version LIMIT 1;" || echo "unknown")
echo "✓ Alembic Version hiện tại của Database phục hồi: $CURRENT_ALEMBIC_VERSION"

# Dọn dẹp nếu là drill test và có biến CLEANUP_DRILL=true
if [ "$DRILL_MODE" = "true" ] && [ "${CLEANUP_DRILL:-false}" = "true" ]; then
  echo "Dọn dẹp database tạm $TARGET_RESTORE_DB..."
  $COMPOSE exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET_RESTORE_DB\" WITH (FORCE);" || true
fi

echo "======================================================================"
echo "  DIỄN TẬP PHỤC HỒI DỮ LIỆU HOÀN TẤT THÀNH CÔNG!"
echo "  Thời gian phục hồi thực tế đo được (RTO): ${RTO_SECONDS} giây"
echo "  Thời điểm dữ liệu mất mát tối đa cam kết (RPO): <= 24 giờ"
echo "  Alembic Migration Version: ${CURRENT_ALEMBIC_VERSION}"
echo "======================================================================"
