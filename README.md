# QuizArena

Nền tảng thi đố trực tuyến thời gian thực (Realtime Multiplayer Quiz), bộ câu hỏi được sinh tự động từ tài liệu học tập bằng AI (Google Gemini).

---

## 1. Yêu Cầu Môi Trường (Prerequisites)

- **Docker & Docker Compose** (Docker Desktop trên Windows / macOS, hoặc Docker Engine trên Linux).
- **Python 3.12+** và công cụ quản lý package [**uv**](https://github.com/astral-sh/uv).
- **Node.js 20+** và **pnpm** (nếu chạy local dev Frontend).

---

## 2. Hướng Dẫn Khởi Chạy Nhanh Bằng Docker Compose (Khuyên Dùng)

Đây là phương thức chạy chuẩn tương đương môi trường triển khai Production/Staging, bao gồm: Nginx Gateway (SSL/TLS), PostgreSQL, Redis, Backend API (FastAPI), Realtime Gateway (WebSocket), Worker (ARQ), và Web SPA (Vue/Vite).

### Bước 1: Thiết lập tệp cấu hình `.env`
Sao chép từ tệp mẫu:
```bash
cp .env.example .env
```
*(Trên Windows PowerShell: `Copy-Item .env.example .env`)*

- Nếu có API key của Google AI Studio, hãy điền vào `GEMINI_API_KEY=` và đặt `GEMINI_GATEWAY_MODE=real`.
- Nếu chưa có API key, hệ thống mặc định hỗ trợ chế độ giả lập an toàn `GEMINI_GATEWAY_MODE=fake`.

### Bước 2: Tạo chứng chỉ SSL tự ký cho môi trường Local
Hệ thống sử dụng Nginx Gateway làm cổng duy nhất mở ra ngoài với toàn bộ lưu lượng được mã hóa HTTPS (cổng 443). Chạy script sau để nạp chứng chỉ SSL bootstrap vào volume Docker:
```bash
# Tạo chứng chỉ giả lập
python infra/scripts/generate_dummy_cert.py

# Nạp chứng chỉ vào volume Docker certbot_etc (đảm bảo Nginx khởi động thành công)
docker volume create quizarena_certbot_etc
docker run --rm -v quizarena_certbot_etc:/etc/letsencrypt -v "${PWD}/infra/nginx/dummy_certs/quizarena:/dummy:ro" alpine sh -c "mkdir -p /etc/letsencrypt/live/quizarena && cp /dummy/* /etc/letsencrypt/live/quizarena/"
```

### Bước 3: Khởi động cụm dịch vụ
```bash
docker compose up -d --build
```

### Bước 4: Chạy Migration và Khởi tạo Dữ liệu Quản trị
```bash
# 1. Chạy Database Migration cập nhật schema bảng mới nhất
docker compose run --rm migrate

# 2. Khởi tạo Ma trận Vai trò & Quyền hạn (Seed RBAC)
docker compose exec api python -m app.cli seed

# 3. Tạo tài khoản Quản trị viên (Admin/Host)
docker compose exec api python -m app.cli create-admin --email admin@quizarena.io.vn --password "AdminPass123456!"
```

### Bước 5: Truy cập Ứng Dụng
- **Giao diện Web (SPA)**: [https://localhost](https://localhost)
  *(Vì sử dụng chứng chỉ tự ký trên localhost, trình duyệt sẽ hiển thị cảnh báo bảo mật. Bạn chỉ cần chọn **Nâng cao (Advanced)** -> **Tiếp tục truy cập (Proceed)**).*
  - Tài khoản mặc định: `admin@quizarena.io.vn`
  - Mật khẩu: `AdminPass123456!`
- **Tài liệu API (Swagger UI)**: [https://localhost/docs](https://localhost/docs)
- **Kiểm tra Sức khỏe Hệ thống**: [https://localhost/healthz](https://localhost/healthz)

---

## 3. Bật Stack Giám Sát (Observability: Prometheus, Loki, Grafana)

QuizArena tích hợp sẵn hệ thống quan sát toàn diện theo tiêu chuẩn Phase 7 (Prometheus scraper, Loki log aggregation, Alloy agent, Node-Exporter, cAdvisor và Grafana Dashboards):

```bash
# Khởi động cụm giám sát
docker compose --profile monitoring up -d
```

- **Truy cập Grafana**: [http://localhost/grafana/](http://localhost/grafana/)
  - Tài khoản mặc định: `admin` / `admin`
- **Các Dashboard được cấu hình sẵn**:
  1. `QuizArena Live`: Giám sát phòng thi thời gian thực, số lượng kết nối WebSocket, p95 latency NF-2 / NF-3, Live Logs lọc theo `$game_id`.
  2. `AI & Quota`: Giám sát hạn mức RPM, RPD của Gemini API, thống kê số token tiêu thụ và số lượt gọi AI sinh đề / viết báo cáo.
  3. `Infra`: Giám sát tài nguyên máy chủ CPU %, RAM %, hàng đợi Worker Queue Depth, và số lượng container replica autoscaling.

---

## 4. Hướng Dẫn Chạy Local Development (Backend & Frontend Riêng Biệt)

Dành cho nhà phát triển muốn trực tiếp debug hoặc chỉnh sửa mã nguồn có hot-reload.

### 4.1. Chạy cơ sở dữ liệu nền bằng Docker
```bash
docker compose up -d postgres redis
```

### 4.2. Khởi chạy Backend
```bash
cd backend

# Cài đặt dependencies
uv sync

# Chạy migration và seed dữ liệu
uv run alembic upgrade head
uv run quizarena seed
uv run quizarena create-admin --email admin@quizarena.io.vn --password "AdminPass123456!"

# Chạy REST API (Port 8000)
uv run uvicorn app.main_api:app --host 0.0.0.0 --port 8000 --reload

# Mở cửa sổ terminal khác: Chạy Realtime WebSocket Gateway (Port 8001)
uv run uvicorn app.main_realtime:app --host 0.0.0.0 --port 8001 --reload

# Mở cửa sổ terminal khác: Chạy Background Worker (ARQ)
uv run arq app.worker.WorkerSettings
```

### 4.3. Khởi chạy Frontend
```bash
cd frontend

# Cài đặt thư viện
pnpm install

# Khởi chạy Vite Dev Server
pnpm dev
```
Truy cập giao diện phát triển tại `http://localhost:5173`.

---

## 5. Các Lệnh CLI Quản Trị & Kiểm Thử Hữu Ích

Mọi tác vụ quản trị và đo lường hiệu năng đều được tích hợp vào CLI của QuizArena:

```bash
cd backend

# 1. Chạy Smoke Test kiểm tra sức khỏe toàn chuỗi dịch vụ (DNS, TLS, API, 1 Bot WebSocket)
uv run quizarena smoke --url https://localhost

# 2. Benchmark đánh giá chất lượng sinh bộ câu hỏi AI từ tài liệu mẫu (Phase 7 - §10.3)
uv run quizarena eval-quizgen --samples evals/samples --questions 3

# 3. Kiểm tra thông tin hạn mức Gemini Quota (RPM/RPD)
uv run quizarena gemini-quota

# 4. Chạy mô phỏng tải Bot tham gia phòng thi đấu
uv run quizarena bots --pin <PIN_PHÒNG> --count 50 --stagger 0.05

# 5. Chạy toàn bộ Unit & Integration tests
uv run pytest
```

---

## 6. Sao Lưu & Phục Hồi Cơ Sở Dữ Liệu (§7.7)

```bash
# Tạo bản sao lưu cơ sở dữ liệu nén (pg_dump -Fc), xoay vòng 7 ngày và cập nhật metric Prometheus
bash infra/scripts/backup_postgres.sh

# Diễn tập phục hồi dữ liệu vào database tạm (quizarena_drill), đối soát row counts và đo RTO
bash infra/scripts/restore_postgres.sh
```
