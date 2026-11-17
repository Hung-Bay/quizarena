#!/usr/bin/env bash
# ==============================================================================
# QuizArena — Script Cấp Phát Chứng Chỉ Let's Encrypt ACME & Kích Hoạt HTTPS
# Sử dụng: ./init_ssl.sh <DOMAIN> <EMAIL> [--staging]
# ==============================================================================
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Sử dụng: $0 <DOMAIN> <EMAIL> [--staging]" >&2
  echo "Ví dụ:   $0 quizarena.online admin@quizarena.online --staging" >&2
  exit 1
fi

DOMAIN="$1"
EMAIL="$2"
STAGING_MODE=false

if [ "${3:-}" == "--staging" ]; then
  STAGING_MODE=true
fi

PROJECT_DIR="/opt/quizarena"
if [ ! -d "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

cd "$PROJECT_DIR"
echo "======================================================================"
echo "  KHỞI TẠO CHỨNG CHỈ HTTPS LET'S ENCRYPT CHO: $DOMAIN"
echo "  - Email quản trị : $EMAIL"
echo "  - Staging Mode   : $STAGING_MODE"
echo "======================================================================"

COMPOSE="docker compose -f docker-compose.yml"

# 1. Kiểm tra chứng chỉ đã tồn tại hay chưa trong volume certbot_etc
CERT_EXISTS=false
if docker volume inspect quizarena_certbot_etc >/dev/null 2>&1; then
  # Kiểm tra file bên trong volume qua container phụ trợ
  if docker run --rm -v quizarena_certbot_etc:/etc/letsencrypt alpine test -f /etc/letsencrypt/live/quizarena/fullchain.pem; then
    CERT_EXISTS=true
    echo ">>> Phát hiện chứng chỉ đã tồn tại trong volume quizarena_certbot_etc."
  fi
fi

# 2. Nếu chưa có chứng chỉ: Tạo chứng chỉ tự ký (Self-signed) để Nginx khởi động được cổng 443
if [ "$CERT_EXISTS" = false ]; then
  echo ">>> [1/5] Khởi tạo chứng chỉ tự ký tạm thời để Nginx có thể khởi động cổng 443..."
  python3 infra/scripts/generate_dummy_cert.py /tmp/dummy_quizarena "$DOMAIN"

  # Nạp dummy cert vào volume Docker certbot_etc
  docker run --rm \
    -v quizarena_certbot_etc:/etc/letsencrypt \
    -v /tmp/dummy_quizarena:/dummy:ro \
    alpine sh -c "mkdir -p /etc/letsencrypt/live/quizarena && cp /dummy/* /etc/letsencrypt/live/quizarena/"
  rm -rf /tmp/dummy_quizarena
fi

# 3. Khởi động Nginx và Web để phục vụ ACME challenge
echo ">>> [2/5] Khởi động Nginx và Web..."
$COMPOSE up -d nginx web

# Chờ Nginx sẵn sàng trên cổng 80
echo ">>> Chờ Nginx sẵn sàng (health check)..."
for i in {1..15}; do
  if curl -sf http://127.0.0.1/healthz >/dev/null 2>&1; then
    echo "Nginx đã sẵn sàng!"
    break
  fi
  sleep 1
done

# 4. Yêu cầu chứng chỉ từ Let's Encrypt qua Certbot
STAGING_ARG=""
if [ "$STAGING_MODE" = true ]; then
  echo ">>> [3/5] Yêu cầu cấp chứng chỉ THỬ NGHIỆM (--staging)..."
  STAGING_ARG="--staging"
else
  echo ">>> [3/5] Yêu cầu cấp chứng chỉ CHÍNH THỨC (Production)..."
fi

$COMPOSE run --rm certbot certonly \
  --webroot \
  -w /var/www/certbot \
  --cert-name quizarena \
  -d "$DOMAIN" \
  -d "grafana.$DOMAIN" \
  --email "$EMAIL" \
  --rsa-key-size 4096 \
  --agree-tos \
  --no-eff-email \
  --force-renewal \
  $STAGING_ARG

# 5. Reload Nginx để nạp chứng chỉ mới mà không ngắt kết nối
echo ">>> [4/5] Tải lại cấu hình Nginx (nginx -s reload)..."
$COMPOSE exec -T nginx nginx -s reload

# 6. Kiểm tra kết nối HTTPS và chuyển hướng 301
echo ">>> [5/5] Kiểm thử kết nối và chuyển hướng HTTP -> HTTPS 301..."
HTTP_REDIRECT=$(curl -sI -o /dev/null -w "%{http_code}" "http://127.0.0.1/" || true)
echo "Mã phản hồi HTTP Port 80: $HTTP_REDIRECT (Kỳ vọng: 301)"

HTTPS_CODE=$(curl -k -sI -o /dev/null -w "%{http_code}" "https://127.0.0.1/healthz" || true)
echo "Mã phản hồi HTTPS Port 443: $HTTPS_CODE (Kỳ vọng: 200)"

echo "======================================================================"
echo "  CẤP PHÁT CHỨNG CHỈ HTTPS THÀNH CÔNG!"
echo "  - Truy cập: https://$DOMAIN/"
echo "  - Grafana : https://$DOMAIN/grafana/"
echo "  - Kiểm tra SSL Labs: https://www.ssllabs.com/ssltest/analyze.html?d=$DOMAIN"
echo "======================================================================"
