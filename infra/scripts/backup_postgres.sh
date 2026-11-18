#!/usr/bin/env bash
# ==============================================================================
# QuizArena — PostgreSQL Automated Backup Script (§7.7)
# Thực hiện pg_dump định dạng nén Custom (-Fc), lưu trữ xoay vòng 7 bản,
# tùy chọn mã hóa OpenSSL trước khi đẩy offsite rclone,
# và xuất metric Prometheus textfile collector cho alert DatabaseBackupTooOld.
# ==============================================================================
set -euo pipefail

# Thư mục gốc project
PROJECT_DIR="/opt/quizarena"
if [ ! -d "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
cd "$PROJECT_DIR"

BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
COMPOSE="docker compose -f docker-compose.yml"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILENAME="quizarena_backup_${TIMESTAMP}.dump"
BACKUP_FILEPATH="${BACKUP_DIR}/${BACKUP_FILENAME}"

mkdir -p "$BACKUP_DIR"
mkdir -p "$TEXTFILE_DIR" 2>/dev/null || true

echo "=== [1/5] Bắt đầu sao lưu PostgreSQL vào ${BACKUP_FILEPATH} ==="
START_TIME=$(date +%s)

# Nạp thông tin cấu hình từ .env nếu có
if [ -f ".env" ]; then
  # Đọc biến an toàn không nạp lệnh shell lạ
  POSTGRES_USER=$(grep -E '^POSTGRES_USER=' .env | cut -d '=' -f2- | tr -d '"'\'' ')
  POSTGRES_DB=$(grep -E '^POSTGRES_DB=' .env | cut -d '=' -f2- | tr -d '"'\'' ')
fi

POSTGRES_USER="${POSTGRES_USER:-quizarena}"
POSTGRES_DB="${POSTGRES_DB:-quizarena}"

# Thực hiện pg_dump -Fc qua docker compose exec không mở cổng
$COMPOSE exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$BACKUP_FILEPATH"

BACKUP_SIZE=$(stat -c%s "$BACKUP_FILEPATH" 2>/dev/null || stat -f%z "$BACKUP_FILEPATH" 2>/dev/null || wc -c < "$BACKUP_FILEPATH")
if [ "$BACKUP_SIZE" -le 100 ]; then
  echo "LỖI: Bản sao lưu rỗng hoặc kích thước quá nhỏ (${BACKUP_SIZE} bytes)!" >&2
  rm -f "$BACKUP_FILEPATH"
  exit 1
fi
echo "✓ Đã tạo bản dump thành công (${BACKUP_SIZE} bytes)."

# Kiểm tra tính toàn vẹn bước đầu qua pg_restore --list
if $COMPOSE exec -T postgres pg_restore --list < "$BACKUP_FILEPATH" >/dev/null 2>&1; then
  echo "✓ Kiểm tra TOC dump hợp lệ (pg_restore --list passed)."
else
  echo "CẢNH BÁO: Kiểm tra TOC không thành công, kiểm tra lại file dump."
fi

# Tùy chọn: Mã hóa nếu có BACKUP_ENCRYPTION_KEY
if [ -n "${BACKUP_ENCRYPTION_KEY:-}" ]; then
  echo "=== [2/5] Tiến hành mã hóa AES-256-CBC bản sao lưu ==="
  ENCRYPTED_FILEPATH="${BACKUP_FILEPATH}.enc"
  openssl enc -aes-256-cbc -salt -pbkdf2 -pass "pass:${BACKUP_ENCRYPTION_KEY}" -in "$BACKUP_FILEPATH" -out "$ENCRYPTED_FILEPATH"
  echo "✓ Bản sao lưu đã được mã hóa: ${ENCRYPTED_FILEPATH}"
fi

# Tùy chọn: Đẩy offsite qua rclone nếu có cấu hình
if command -v rclone >/dev/null 2>&1 && [ -n "${RCLONE_REMOTE:-}" ]; then
  echo "=== [3/5] Đồng bộ offsite lên cloud storage qua rclone ==="
  TARGET_UPLOAD="${ENCRYPTED_FILEPATH:-$BACKUP_FILEPATH}"
  rclone copy "$TARGET_UPLOAD" "${RCLONE_REMOTE}:quizarena-backups/" || echo "CẢNH BÁO: Upload rclone thất bại, giữ lại file local."
else
  echo "=== [3/5] Bỏ qua rclone offsite (chưa cấu hình RCLONE_REMOTE hoặc chưa cài rclone) ==="
fi

# Xoay vòng giữ 7 bản sao lưu gần nhất (Retention)
echo "=== [4/5] Xoay vòng bản sao lưu (Giữ tối đa ${RETENTION_DAYS} ngày) ==="
find "$BACKUP_DIR" -type f \( -name "quizarena_backup_*.dump" -o -name "quizarena_backup_*.dump.enc" \) -mtime "+${RETENTION_DAYS}" -exec rm -f {} + || true

# Xuất metric Prometheus textfile collector (atomic rename)
echo "=== [5/5] Cập nhật metric Prometheus last_backup_success.prom ==="
END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

PROM_TMP="${TEXTFILE_DIR}/last_backup_success.prom.tmp"
PROM_FILE="${TEXTFILE_DIR}/last_backup_success.prom"

cat <<EOF > "$PROM_TMP"
# HELP quizarena_backup_timestamp_seconds Thời điểm Unix timestamp lần backup PostgreSQL thành công gần nhất
# TYPE quizarena_backup_timestamp_seconds gauge
quizarena_backup_timestamp_seconds ${END_TIME}
# HELP quizarena_backup_size_bytes Kích thước bản backup PostgreSQL gần nhất
# TYPE quizarena_backup_size_bytes gauge
quizarena_backup_size_bytes ${BACKUP_SIZE}
# HELP quizarena_backup_duration_seconds Thời gian thực thi backup tính bằng giây
# TYPE quizarena_backup_duration_seconds gauge
quizarena_backup_duration_seconds ${DURATION}
EOF

mv -f "$PROM_TMP" "$PROM_FILE" 2>/dev/null || cp -f "$PROM_TMP" "$PROM_FILE" 2>/dev/null || true
rm -f "$PROM_TMP" 2>/dev/null || true

echo "======================================================================"
echo "  SAO LƯU POSTGRESQL HOÀN TẤT THÀNH CÔNG (${DURATION}s, ${BACKUP_SIZE} bytes)"
echo "  Tệp: ${BACKUP_FILEPATH}"
echo "======================================================================"
