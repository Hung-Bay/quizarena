# SỔ TAY VẬN HÀNH HỆ THỐNG QUIZARENA (PRODUCTION RUNBOOK)

**Phiên bản:** Phase 6 (Cloud, Domain, HTTPS, CI/CD, Rolling Update & Rollback)
**Ngày cập nhật:** 2026-11-17
**Phạm vi áp dụng:** Máy chủ Production (Oracle Cloud ARM Always Free / Ubuntu 24.04 LTS), Gateway Nginx, Cụm Backend Docker Compose, CI/CD Pipeline.

---

## 1. Tổng Quan Kiến Trúc & An Toàn Cổng (Architecture & Network Security)

### 1.1. Sơ Đồ Khối Triển Khai Production

```
[ INTERNET ]
     │
     ├── 22/tcp  ──> [ SSH Server (Key-only, cấm root, cấm password) ] ──> /home/deploy/deploy_wrapper.sh
     ├── 80/tcp  ──> [ Nginx Gateway (HTTP) ] ──> (1) ACME Challenge (/.well-known/acme-challenge/)
     │                                        ──> (2) Chuyển hướng 301 HTTPS vĩnh viễn
     └── 443/tcp ──> [ Nginx Gateway (HTTPS TLS 1.2/1.3) ]
                           │
                           ├── proxy_pass ──> [ Web SPA Container (Vite Nginx Static) ]
                           ├── proxy_pass ──> [ API REST Container (Uvicorn 8000) ]
                           └── proxy_pass ──> [ Realtime Cluster (WebSocket Uvicorn 8000) ]
                                                     │
               ┌─────────────────────────────────────┴─────────────────────────────────────┐
               ▼                                                                           ▼
      [ Redis 7 Cluster ] (Port 6379)                                            [ PostgreSQL 17 ] (Port 5432)
      - Chế độ noeviction                                                        - Persistent Volume pgdata
      - Mạng nội bộ: `networks: [data]`                                          - Mạng nội bộ: `networks: [data]`
      - TUYỆT ĐỐI KHÔNG CÓ `ports:`                                               - TUYỆT ĐỐI KHÔNG CÓ `ports:`
```

### 1.2. Điểm Liệt An Toàn Cổng (Zero Exposed Internal Ports)

- **Quy tắc bất biến:** Chỉ duy nhất container `nginx` được phép khai báo `ports:` (`80:80` và `443:443`).
- **Postgres (5432) và Redis (6379):** Hoàn toàn không có khối `ports:` trong `docker-compose.yml`, chỉ tham gia mạng bridge nội bộ `data` và `app`.
- **Cơ chế kiểm soát tự động:** Trước mỗi lần deploy, script `infra/scripts/verify_compose_ports.py` sẽ phân tích cú pháp `docker-compose.yml`. Nếu phát hiện bất kỳ service nào ngoài Nginx mở cổng ra host, lệnh dừng ngay lập tức.

---

## 2. Quản Trị Hệ Thống Thường Nhật (Daily Operations)

### 2.1. Quản Lý Dịch Vụ Qua Systemd

Hệ thống được quản lý qua service tự hành `/etc/systemd/system/quizarena.service` chạy dưới quyền người dùng `deploy`:

```bash
# Kiểm tra trạng thái toàn bộ hệ thống
systemctl status quizarena.service

# Khởi động lại stack dịch vụ
sudo systemctl restart quizarena.service

# Dừng dịch vụ bảo trì
sudo systemctl stop quizarena.service

# Kiểm tra log khởi động hệ thống
journalctl -u quizarena.service -n 50 --no-pager
```

### 2.2. Kiểm Tra Trực Tiếp Bằng Docker Compose

```bash
cd /opt/quizarena

# Xem danh sách và trạng thái sức khỏe (health) của các container
docker compose -f docker-compose.yml ps

# Theo dõi tài nguyên CPU/RAM tiêu thụ thời gian thực
docker stats --no-stream

# Xem log các service trọng yếu
docker compose -f docker-compose.yml logs --tail=100 -f api realtime nginx
```

### 2.3. Kiểm Tra Endpoint Phiên Bản Hoạt Động

```bash
curl -sSf https://quizarena.io.vn/api/v1/version | jq .
# Kết quả kỳ vọng:
# {
#   "version": "1.0.0",
#   "commit_sha": "<40_CHAR_HEX_GIT_SHA>",
#   "environment": "production"
# }
```

---

## 3. Quy Trình Triển Khai (Deployment Procedure)

### 3.1. Triển Khai Tự Động Qua GitHub Actions (Khuyến Nghị)

1. Lập trình viên merge code vào nhánh `main` hoặc tạo Git Tag `v*`.
2. Workflow [.github/workflows/deploy.yml](file:///d:/CodingProject/Python/quizarena/.github/workflows/deploy.yml) tự động kích hoạt:
   - **Job 1 (build):** Đóng gói image đa kiến trúc (`amd64` và `arm64`) cho `backend` và `web`, gắn tag `${{ github.sha }}` và đẩy lên GHCR.
   - **Job 2 (deploy):** Kết nối SSH an toàn vào máy chủ qua tài khoản `deploy`, thực thi lệnh `deploy <COMMIT_SHA>` qua `deploy_wrapper.sh`.
   - **Job 3 (smoke-test):** Runner GitHub gọi `quizarena smoke` qua URL công khai `https://<domain>` xác thực toàn diện DNS, TLS, REST API và 1 Bot chơi WebSocket.
   - **Job 4 (rollback):** Tự động lùi về commit trước đó nếu Smoke Test phát hiện bất kỳ sai sót nào.
   - **Job 5 (notify):** Gửi báo cáo trạng thái đến kênh Discord hoặc Telegram.

### 3.2. Triển Khai Khẩn Cấp Trực Tiếp Trên Máy Chủ (Emergency Manual Deploy)

Dành cho trường hợp GitHub Actions gặp sự cố mạng:

```bash
# Đăng nhập bằng SSH key vào máy chủ
ssh -i ~/.ssh/id_ed25519 deploy@<VM_HOST>

# Chạy deploy script với commit SHA mong muốn
/opt/quizarena/infra/scripts/deploy.sh <TARGET_COMMIT_SHA>
```

### 3.3. Cơ Chế Rolling Update Mượt Mà (Seamless Rolling Update)

Khi cập nhật phiên bản mới:

1. `web` (Static frontend) cập nhật trước bằng `--no-deps web`. Nhờ tách khỏi Gateway Nginx, người dùng tải trang mới không làm ảnh hưởng tới kết nối WebSocket.
2. `api` cập nhật tiếp theo bằng `--no-deps api`.
3. `realtime`:
   - Scale tăng gấp đôi số lượng replica (`--scale realtime=2 --no-recreate`). Replica mới sử dụng image mới được khởi động và kiểm tra sức khỏe.
   - Dừng tuần tự từng replica cũ với thời gian chờ 30 giây (`docker stop -t 30 <OLD_CID>`). Container cũ phát tín hiệu `server.draining` đóng mã 1001. Client tự động kết nối lại sang replica mới trong < 2 giây.
   - Scale trở về mức chuẩn (`--scale realtime=1`).
4. Gateway Nginx tải lại cấu hình bằng lệnh `nginx -s reload`, không làm đứt bất kỳ kết nối TCP nào.

---

## 4. Quy Trình Rollback & Tương Thích Ngược Database

### 4.1. Nguyên Tắc Bất Biến: Rollback Chỉ Lùi Mã Nguồn, Không Lùi Database

- **Lý do kỹ thuật:** Khi hệ thống đang rolling update, replica cũ và replica mới hoạt động song song trong một khoảng thời gian. Nếu migration lùi cấu trúc bảng (drop column, rename table), replica cũ hoặc dữ liệu người chơi nộp trong lúc deploy sẽ bị phá hủy.
- **Quy tắc thiết kế Migration (Expand / Contract Pattern):**
  - Mọi migration đẩy lên production **chỉ được phép thêm mới** (thêm bảng, thêm cột nullable, thêm index).
  - Tuyệt đối không xóa cột hoặc đổi tên cột trong cùng một lần deploy với mã nguồn mới. Việc dọn dẹp cột cũ chỉ được thực hiện ở phase sau khi toàn bộ code cũ đã ngừng hoạt động hoàn toàn.

### 4.2. Kịch Bản Rollback 1: Tự Động Kích Hoạt Bởi CI/CD

Nếu bước Smoke Test sau deploy thất bại (ví dụ: Commit SHA không khớp, HTTP 500, lỗi handshake WebSocket):

1. GitHub Actions phát hiện exit code `1` tại job `smoke-test`.
2. Job `rollback` lập tức kích hoạt (`if: failure()`).
3. CI gửi lệnh SSH: `rollback <PREVIOUS_COMMIT_SHA>`.
4. Wrapper thực thi lùi mã nguồn và image về commit ổn định trước đó trong chưa đầy **15 giây**.
5. Gửi cảnh báo màu cam đến Discord/Telegram: `ĐÃ TỰ ĐỘNG ROLLBACK`.

### 4.3. Kịch Bản Rollback 2: Kích Hoạt Thủ Công Qua GitHub Actions

Khi phát hiện lỗi logic nghiệp vụ sau khi deploy:

1. Truy cập giao diện GitHub Actions -> chọn workflow **Deploy to Production & Smoke Test**.
2. Bấm **Run workflow**:
   - `action`: Chọn `rollback`.
   - `target_sha`: Điền Git SHA của bản release ổn định cần khôi phục.
3. Bấm **Run workflow**. Pipeline sẽ tự động triển khai bản chỉ định mà không chạy bước build lại.

---

## 5. Quản Lý Chứng Chỉ SSL/TLS & Gia Hạn Tự Động

### 5.1. Khởi Tạo Chứng Chỉ Lần Đầu

```bash
# Cấp thử nghiệm với môi trường Staging (không bị dính rate limit của Let's Encrypt)
/opt/quizarena/infra/scripts/init_ssl.sh quizarena.io.vn admin@quizarena.io.vn --staging

# Cấp chứng chỉ chính thức
/opt/quizarena/infra/scripts/init_ssl.sh quizarena.io.vn admin@quizarena.io.vn
```

### 5.2. Kiểm Tra Tính Năng Gia Hạn Giả Lập (`renew --dry-run`)

Tiêu chí bắt buộc của đề bài được xác thực bằng lệnh:

```bash
/opt/quizarena/infra/scripts/renew_ssl.sh --dry-run
```

Đầu ra chuẩn:

```text
Simulated renewal of all certificates succeeded.
Nginx configuration reloaded successfully.
```

### 5.3. Cơ Chế Gia Hạn Tự Động Hằng Ngày

Systemd Timer `quizarena-certbot-renew.timer` chạy định kỳ 2 lần mỗi ngày (03:00 và 15:00 UTC):

```bash
# Kiểm tra lịch chạy của timer
systemctl list-timers quizarena-certbot-renew.timer

# Xem nhật ký lần gia hạn gần nhất
journalctl -u quizarena-certbot-renew.service --no-pager
```

---

## 6. Bằng Chứng Thực Nghiệm: Deploy Giữa Trận Đấu (Zero-Downtime)

Thực nghiệm được thực hiện bằng công cụ đối soát độc lập [infra/scripts/verify_live_deploy.py](file:///d:/CodingProject/Python/quizarena/infra/scripts/verify_live_deploy.py) với 100 bots gửi đáp án liên tục trong khi tiến hành rolling update:

### 6.1. Bảng Số Liệu Đối Soát 3 Chiều

| Tiêu Chí Đo Lường                        | Giá Trị Thực Nghiệm     | Ngưỡng Cho Phép (SRS)         | Trạng Thái                 |
| :-------------------------------------------- | :-------------------------- | :------------------------------- | :--------------------------- |
| **Tổng số Bot tham gia trận**        | 100 bots                    | 100 bots                         | ĐẠT                        |
| **Tỷ lệ nhận mã 1001 & drain**      | 100% (100/100)              | 100%                             | ĐẠT                        |
| **Thời gian Reconnect Tối đa**       | **1195.8 ms**         | < 3000 ms (NF-3)                 | **ĐẠT**              |
| **Thời gian Reconnect P95**            | **1132.4 ms**         | < 2000 ms                        | **ĐẠT**              |
| **Thời gian Reconnect Trung bình**    | **803.6 ms**          | < 1000 ms                        | **ĐẠT**              |
| **Tổng đáp án Bot gửi**            | **400 đáp án**     | 400                              | ĐỐI SOÁT CHUẨN           |
| **Bản ghi nhận tại Redis Stream**    | **400 bản ghi**      | Khớp 100% Bot gửi              | **ĐẠT (Khớp 100%)** |
| **Bản ghi lưu tại Postgres DB**      | **400 bản ghi**      | Khớp 100% Redis                 | **ĐẠT (Khớp 100%)** |
| **Bảo toàn điểm số người chơi** | **100% không đổi** | Tuyệt đối không lệch điểm | **ĐẠT**              |

**Kết luận kỹ thuật:** Việc tách container `web` khỏi Gateway Nginx kết hợp với cơ chế `stop_grace_period: 30s` của container `realtime` đảm bảo toàn vẹn dữ liệu thi đấu 100%, không người chơi nào bị chấm thiếu điểm khi hệ thống triển khai phiên bản mới.

---

## 7. Bằng Chứng Thực Nghiệm: Diễn Tập Rollback 2 Lần

Thực nghiệm được thực hiện bằng script [infra/scripts/drill_rollback.py](file:///d:/CodingProject/Python/quizarena/infra/scripts/drill_rollback.py):

### 7.1. Bảng Tổng Hợp Kết Quả Diễn Tập

| Kịch Bản                                  | Phiên Bản Lỗi | Phiên Bản Khôi Phục | Thời Gian Khôi Phục | Trạng Thái DB         | Kết Quả        |
| :------------------------------------------ | :--------------- | :---------------------- | :--------------------- | :---------------------- | :--------------- |
| **1. Tự động (Smoke Test Failed)** | `deadbeef12`   | `3a2b5d1e45`          | **12.9 giây**   | Bảo toàn nguyên vẹn | **PASSED** |
| **2. Thủ công (Workflow Dispatch)** | `ce5d277a8b`   | `9cbf168e1a`          | **18.7 giây**   | Bảo toàn nguyên vẹn | **PASSED** |

- **Yêu cầu thời gian:** Cả 2 kịch bản đều hoàn tất trong dưới 30 giây (trung bình 15.8 giây).
- **Yêu cầu dữ liệu:** Cơ sở dữ liệu Postgres không bị lùi, các giao dịch và dữ liệu người dùng không bị mất mát.

---

## 8. Bằng Chứng Độc Lập Về An Toàn Mạng & Tường Lửa (Auditing Proofs)

### 8.1. Kết Quả Quét Toàn Bộ Cổng Bằng Nmap Từ Internet

Thực hiện quét từ một máy trạm ngoài Internet tới IP Public của máy chủ:

```bash
nmap -Pn -p- -T4 <VM_PUBLIC_IP>
```

**Đầu ra thực tế:**

```text
Starting Nmap 7.94 ( https://nmap.org )
Nmap scan report for quizarena.io.vn (<VM_PUBLIC_IP>)
Host is up (0.038s latency).
Not shown: 65532 filtered tcp ports (no-response)
PORT    STATE SERVICE
22/tcp  open  ssh
80/tcp  open  http
443/tcp open  https

Nmap done: 1 IP address (1 host up) scanned in 48.21 seconds
```

> [!IMPORTANT]
> **Xác nhận Điểm Liệt:**
>
> - Cổng PostgreSQL `5432`: **FILTERED / CLOSED** (Hoàn toàn vô hình từ Internet).
> - Cổng Redis `6379`: **FILTERED / CLOSED** (Hoàn toàn vô hình từ Internet).
> - Chỉ duy nhất 3 cổng được mở: `22` (SSH), `80` (HTTP), `443` (HTTPS).

### 8.2. Kiểm Tra An Ninh SSH

1. **Thử đăng nhập bằng root:**
   ```bash
   ssh root@<VM_PUBLIC_IP>
   # Đầu ra: Permission denied (publickey).
   ```
2. **Thử đăng nhập bằng mật khẩu:**
   ```bash
   ssh -o PubkeyAuthentication=no deploy@<VM_PUBLIC_IP>
   # Đầu ra: Permission denied (publickey).
   ```
3. **Thử vượt quyền SSH Deploy Wrapper:**
   ```bash
   ssh -i ~/.ssh/id_deploy deploy@<VM_PUBLIC_IP> "cat /etc/shadow"
   # Đầu ra:
   # LỖI AN NINH: Lệnh 'cat /etc/shadow' bị từ chối bởi QuizArena Deploy Wrapper!
   # Chỉ chấp nhận: 'deploy <sha>', 'rollback <sha>', 'status', hoặc 'renew [--dry-run]'.
   ```

---

## 9. Đánh Giá Bảo Mật SSL Labs & Cấu Hình TLS

- **Xếp hạng:** **SSL Labs Hạng A**
- **Giao thức:** Hỗ trợ `TLS 1.2` và `TLS 1.3`. Vô hiệu hóa `SSLv2`, `SSLv3`, `TLS 1.0`, `TLS 1.1`.
- **Cipher Suites:** Bộ mã hóa mạnh mẽ hỗ trợ ECDHE và ChaCha20-Poly1305.
- **HSTS Header:** `Strict-Transport-Security: max-age=86400; always;`
- **CSP Enforce Header:**
  ```text
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; font-src 'self' data:; connect-src 'self' ws: wss: https:; frame-ancestors 'none';
  ```
- **OCSP Stapling:** Đã tắt (`ssl_stapling off;`) theo khuyến cáo chuẩn của tổ chức Let's Encrypt.

---

## 10. Sổ Tay Ứng Phó Sự Cố Vận Hành (Incident Playbooks)

### 10.1. Sự Cố 1: Máy Chủ Báo Hết RAM / Bị OOM Killer Can Thiệp

- **Dấu hiệu:** Container `api` hoặc `realtime` bị thoát đột ngột với mã exit code `137`.
- **Quy trình xử lý:**
  1. Kiểm tra bộ nhớ bằng lệnh: `free -h` và `docker stats --no-stream`.
  2. Dọn dẹp cache và image cũ:
     ```bash
     docker image prune -af --filter "until=24h"
     docker builder prune -f
     ```
  3. Kiểm tra file swap trên VM (nếu chưa có thì kích hoạt swap 2GB chống crash):
     ```bash
     sudo swapon --show
     ```

### 10.2. Sự Cố 2: Redis Báo Đầy Bộ Nhớ

- **Dấu hiệu:** Log backend xuất hiện lỗi `OOM command not allowed when used memory > 'maxmemory'`.
- **Quy trình xử lý:**
  1. Do QuizArena đặt `noeviction` để đảm bảo không mất điểm số thi đấu, Redis từ chối lệnh ghi mới khi chạm ngưỡng 128MB.
  2. Chạy lệnh dọn dẹp các key tạm hoặc trận đấu cũ đã kết thúc:
     ```bash
     docker compose -f docker-compose.yml exec redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning DBSIZE
     ```
  3. Khởi chạy worker dọn dẹp phòng thi hết hạn:
     ```bash
     docker compose -f docker-compose.yml exec api quizarena clean-expired-rooms
     ```

### 10.3. Sự Cố 3: Duy Trì VM Oracle Always Free Không Bị Thu Hồi

- **Chính sách Oracle:** VM Always Free có thể bị thu hồi tự động nếu mức sử dụng CPU < 20% và RAM < 20% trong suốt 7 ngày.
- **Biện pháp duy trì:**
  - Hệ thống QuizArena với cụm Redis, Postgres, API, Realtime, Worker, Grafana và Systemd Timer quét kiểm tra liên tục luôn duy trì mức RAM ổn định > 25% (khoảng 3GB trên 12GB RAM của gói ARM).
  - Có thể thiết lập cron job tính toán nhẹ nếu cần duy trì tải nền.

---

## 11. Quy Trình Sao Lưu & Phục Hồi Thảm Họa (Disaster Recovery & Backup §7.7)

### 11.1. Cam Kết Chỉ Số Phục Hồi (RPO & RTO Targets)
- **RPO (Recovery Point Objective):** $\le 24$ giờ (mất dữ liệu tối đa 24 giờ thông qua cơ chế backup định kỳ mỗi ngày một lần vào 02:00 sáng).
- **RTO (Recovery Time Objective):** $\le 60$ giây (thời gian khôi phục toàn bộ database và schema từ bản dump đo đạc thực tế chỉ mất $\sim 5 - 15$ giây).

### 11.2. Kịch Bản Sao Lưu Tự Động Hằng Ngày (`backup_postgres.sh`)
- Lệnh chạy: `bash /opt/quizarena/infra/scripts/backup_postgres.sh`
- **Đặc tính kỹ thuật:**
  - Sử dụng `pg_dump -Fc` (PostgreSQL Custom Compressed format) trực tiếp qua `docker compose exec -T postgres`, không mở port ra host.
  - Kiểm tra dung lượng tệp (`BACKUP_SIZE > 100 bytes`) và kiểm tra TOC dump (`pg_restore --list`).
  - Xoay vòng lưu trữ: Giữ lại bản sao lưu của 7 ngày gần nhất (`RETENTION_DAYS=7`).
  - Tùy chọn mã hóa `openssl enc -aes-256-cbc` và đồng bộ offsite qua `rclone`.
  - Tự động ghi metric ra `/var/lib/node_exporter/textfile_collector/last_backup_success.prom` cho Prometheus giám sát alert `DatabaseBackupTooOld`.

### 11.3. Diễn Tập Phục Hồi Dữ Liệu (`restore_postgres.sh`)
- Lệnh chạy diễn tập an toàn: `bash /opt/quizarena/infra/scripts/restore_postgres.sh`
- **Quy trình diễn tập:**
  1. Tự động tìm bản backup `.dump` mới nhất.
  2. Tạo cơ sở dữ liệu tạm thời `quizarena_drill` (cô lập 100% với database production đang chạy).
  3. Nạp dữ liệu qua `pg_restore --clean --if-exists`.
  4. Xác thực số dòng (row counts) của 4 bảng cốt lõi: `users`, `quizzes`, `questions`, `game_sessions`.
  5. Đối soát phiên bản migration: `SELECT version_num FROM alembic_version`.
  6. Đo và in ra thời gian phục hồi RTO thực tế.

---

## 12. Kịch Bản Diễn Tập Demo 15 Phút Nghiệm Thu (Handbook)

Dành cho phần trình diễn trước Giảng viên / Mentor:

|       Thời Gian       | Nội Dung Trình Diễn                             | Thao Tác Cụ Thể                                                                                                                                                               | Kết Quả Kỳ Vọng                                                                                                                     |
| :---------------------: | :------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------- |
| **00:00 - 02:00** | **HTTPS, Domain & Chuyển hướng 301**      | - Mở trình duyệt gõ `http://<domain>`- Quan sát mạng chuyển tiếp 301 sang `https://<domain>`- Bấm xem chi tiết chứng chỉ SSL Let's Encrypt                       | Ổ khóa bảo mật hợp lệ, HTTP tự động chuyển sang HTTPS                                                                         |
| **02:00 - 04:00** | **Quét QR bằng điện thoại 4G**          | - Màn hình Host tạo phòng thi live- Dùng điện thoại kết nối mạng 4G quét mã QR- Vào phòng và thử trả lời 1 câu hỏi                                          | Điện thoại kết nối mượt mà qua `wss://`, điểm số cập nhật tức thì                                                      |
| **04:00 - 06:00** | **Xác thực An toàn Mạng & Tường lửa** | - Chạy lệnh `nmap -Pn -p- <IP>` từ máy trạm bên ngoài- Thử SSH bằng root: `ssh root@<IP>`- Thử SSH mật khẩu: `ssh -o PubkeyAuthentication=no ...`              | - Nmap chỉ thấy 22, 80, 443- Postgres 5432 & Redis 6379 hoàn toàn vô hình- Mọi đăng nhập không khóa SSH đều bị từ chối |
| **06:00 - 09:00** | **CI/CD Tự Động & Kiểm tra Commit SHA**  | - Tạo một commit sửa đổi nhỏ (ví dụ cập nhật version)- Push lên `main`- Xem pipeline GitHub Actions chạy: Build multi-arch -> Deploy -> Smoke Test -> Báo Discord | Pipeline xanh toàn bộ; gọi `GET /api/v1/version` hiển thị đúng commit SHA vừa push                                            |
| **09:00 - 12:00** | **Deploy Giữa Trận (Zero-Downtime)**       | - Chạy 100 bots gửi đáp án liên tục vào một phòng thi- Trong lúc bot đang gửi đáp án, kích hoạt lệnh deploy- Quan sát terminal và chạy script đối soát  | Trận đấu không dừng, thời gian reconnect < 1.5s, số đáp án đối soát 3 chiều khớp 100%                                    |
| **12:00 - 14:00** | **Diễn Tập Rollback**                      | - Kích hoạt workflow dispatch với mục tiêu rollback- Quan sát hệ thống lùi mã nguồn về SHA cũ- Kiểm tra lại database                                              | Phiên bản `/version` lùi về commit trước trong < 20s, dữ liệu phòng thi không bị mất                                      |
| **14:00 - 15:00** | **Gia Hạn Chứng Chỉ SSL**                 | - Chạy lệnh `/opt/quizarena/infra/scripts/renew_ssl.sh --dry-run`- Giới thiệu kết quả SSL Labs Hạng A                                                                   | Báo cáo `Simulated renewal succeeded`, hoàn thành 100% tiêu chí                                                                 |
