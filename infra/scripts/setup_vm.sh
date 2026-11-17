#!/usr/bin/env bash
# ==============================================================================
# QuizArena — Script Cấu Hình Máy Chủ Mẫu (Oracle Cloud Always Free ARM)
# Hệ điều hành khuyến nghị: Ubuntu 24.04 / 22.04 LTS (AArch64 / Ampere A1)
# ==============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "LỖI: Script cấu hình máy chủ phải chạy dưới quyền root (sudo)!" >&2
  exit 1
fi

echo ">>> [1/7] Cập nhật gói hệ điều hành & cài đặt gói phụ trợ..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  git \
  ufw \
  iptables-persistent \
  netfilter-persistent \
  unattended-upgrades \
  update-notifier-common

echo ">>> [2/7] Cấu hình cập nhật bảo mật tự động (unattended-upgrades)..."
cat << 'EOF' > /etc/apt/apt.conf.d/20auto-upgrades
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl restart unattended-upgrades

echo ">>> [3/7] Gia cố SSH (SSH Hardening: Chỉ dùng Key, cấm Root & Mật khẩu)..."
mkdir -p /etc/ssh/sshd_config.d
cat << 'EOF' > /etc/ssh/sshd_config.d/99-quizarena.conf
# QuizArena SSH Hardening Policy
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
X11Forwarding no
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

# Kiểm tra cú pháp sshd trước khi reload tránh lockout
sshd -t
systemctl reload ssh || systemctl reload sshd

echo ">>> [4/7] Cài đặt Docker Engine & Docker Compose Plugin chính thức..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -y
apt-get install -y --no-install-recommends \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

systemctl enable docker
systemctl start docker

echo ">>> [5/7] Cấu hình tường lửa 2 lớp (Oracle Ubuntu iptables + UFW)..."
# Lưu ý đặc thù Oracle Cloud:
# 1. Oracle Security List trên Console mở ingress TCP 22, 80, 443.
# 2. Image Ubuntu của Oracle có sẵn chain INPUT với luật REJECT ở cuối.
# Cần chèn luật ACCEPT cho port 80 và 443 trước luật REJECT.
if iptables -L INPUT -n --line-numbers | grep -q "reject-with icmp-host-prohibited"; then
  echo "Phát hiện luật REJECT của Oracle Ubuntu. Đang bổ sung luật ACCEPT cho 80/443..."
  iptables -I INPUT 6 -p tcp --dport 80 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT || true
  iptables -I INPUT 6 -p tcp --dport 443 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT || true
  netfilter-persistent save || true
fi

# Cấu hình UFW
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH Port'
ufw allow 80/tcp comment 'HTTP Port'
ufw allow 443/tcp comment 'HTTPS Port'
ufw --force enable

echo ">>> [6/7] Khởi tạo người dùng deploy chuyên biệt & giới hạn lệnh SSH..."
if ! id -u deploy >/dev/null 2>&1; then
  useradd -m -s /bin/bash deploy
  usermod -aG docker deploy
  echo "Đã tạo user deploy thành công."
fi

DEPLOY_HOME="/home/deploy"
mkdir -p "$DEPLOY_HOME/.ssh"
chmod 700 "$DEPLOY_HOME/.ssh"
touch "$DEPLOY_HOME/.ssh/authorized_keys"
chmod 600 "$DEPLOY_HOME/.ssh/authorized_keys"

# Tạo wrapper kiểm tra an toàn lệnh deploy cho SSH key của GitHub Actions
cat << 'EOF' > "$DEPLOY_HOME/deploy_wrapper.sh"
#!/usr/bin/env bash
set -euo pipefail

# Wrapper giới hạn lệnh thực thi từ SSH key của CI/CD
# Chỉ cho phép chạy deploy.sh kèm Commit SHA (hex 7-40 ký tự) hoặc status
ORIGINAL_CMD="${SSH_ORIGINAL_COMMAND:-status}"

# Loại bỏ khoảng trắng thừa
CMD_CLEAN=$(echo "$ORIGINAL_CMD" | xargs)

if [[ "$CMD_CLEAN" =~ ^deploy\ ([a-f0-9]{7,40})$ ]]; then
  COMMIT_SHA="${BASH_REMATCH[1]}"
  echo "Xác thực lệnh deploy hợp lệ cho SHA: $COMMIT_SHA"
  exec /opt/quizarena/infra/scripts/deploy.sh "$COMMIT_SHA"
elif [[ "$CMD_CLEAN" == "status" ]]; then
  cd /opt/quizarena
  exec docker compose ps
elif [[ "$CMD_CLEAN" =~ ^rollback\ ([a-f0-9]{7,40})$ ]]; then
  COMMIT_SHA="${BASH_REMATCH[1]}"
  echo "Xác thực lệnh rollback hợp lệ cho SHA: $COMMIT_SHA"
  exec /opt/quizarena/infra/scripts/deploy.sh "$COMMIT_SHA"
elif [[ "$CMD_CLEAN" =~ ^renew(\ --dry-run)?$ ]]; then
  cd /opt/quizarena
  exec /opt/quizarena/infra/scripts/renew_ssl.sh ${BASH_REMATCH[1]:-}
else
  echo "LỖI AN NINH: Lệnh '$ORIGINAL_CMD' bị từ chối bởi QuizArena Deploy Wrapper!" >&2
  echo "Chỉ chấp nhận: 'deploy <sha>', 'rollback <sha>', hoặc 'status'." >&2
  exit 1
fi
EOF

chmod +x "$DEPLOY_HOME/deploy_wrapper.sh"
chown -R deploy:deploy "$DEPLOY_HOME"

echo ">>> [7/8] Thiết lập thư mục ứng dụng /opt/quizarena..."
mkdir -p /opt/quizarena
chown -R deploy:deploy /opt/quizarena

echo ">>> [8/8] Cấu hình systemd timer tự động gia hạn SSL (renew_ssl.sh)..."
cat << 'EOF' > /etc/systemd/system/quizarena-certbot-renew.service
[Unit]
Description=QuizArena Certbot Certificate Renewal
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=deploy
WorkingDirectory=/opt/quizarena
ExecStart=/opt/quizarena/infra/scripts/renew_ssl.sh
EOF

cat << 'EOF' > /etc/systemd/system/quizarena-certbot-renew.timer
[Unit]
Description=Run QuizArena Certbot Renewal twice daily
Documentation=https://eff-certbot.readthedocs.io/

[Timer]
OnCalendar=*-*-* 03,15:00:00
RandomizedDelaySec=3600
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload || true
systemctl enable --now quizarena-certbot-renew.timer || true

echo "======================================================================"
echo "  HOÀN TẤT THIẾT LẬP MÁY CHỦ PRODUCTION (PHASE 6)"
echo "  - SSH: Chỉ cho phép xác thực bằng Key (root và mật khẩu đã bị tắt)."
echo "  - Tường lửa: Chỉ mở 22/tcp, 80/tcp, 443/tcp."
echo "  - Docker: Docker Engine & Compose plugin đã sẵn sàng."
echo "  - Deploy User: user 'deploy' với wrapper hạn chế quyền nghiêm ngặt."
echo ""
echo "  CÁC BƯỚC TIẾP THEO CHO QUẢN TRỊ VIÊN:"
echo "  1. Thêm public key của GitHub Actions vào /home/deploy/.ssh/authorized_keys:"
echo "     command=\"/home/deploy/deploy_wrapper.sh\",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA..."
echo "  2. Clone repo vào /opt/quizarena với quyền user deploy."
echo "  3. Tạo file /opt/quizarena/.env (chmod 600) chứa production secrets."
echo "======================================================================"
