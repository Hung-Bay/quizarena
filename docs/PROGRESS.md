# TIẾN ĐỘ DỰ ÁN — QUIZARENA

> Nhật ký theo dõi tiến độ từng tuần và từng phase của dự án QuizArena.
> Cập nhật vào thứ Sáu hàng tuần sau buổi nghiệm thu và đánh giá tiêu chí.

---

## Tổng quan tiến độ theo Phase

| Phase | Nội dung chính | Trọng tâm | Trạng thái | Hoàn thành |
|---|---|---|:---:|:---:|
| **P0** | Thiết kế & Khung hạ tầng | Docker compose, CI/CD, Nginx, Healthz, WS Echo, SRS v0.1 | ✅ Hoàn thành | Tuần 1 |
| **P1** | Auth, RBAC, Guest Token | JWT rotation, Reuse detection, RBAC 4x10, Guest token, WS ticket, ADR-001 | ✅ Hoàn thành | Tuần 2 |
| **P2** | Quiz Authoring + AI Sinh Quiz | CRUD quiz, PDF upload, Gemini Gateway, Quota rate limiter, SSE | ✅ Hoàn thành | Tuần 3 |
| **P3** | Live Game Engine | State machine, Redis Lua atomic, Leaderboard ZSET, Game clock, Write-behind, CLI Bots | ✅ Hoàn thành | Tuần 4 |
| **P4** | Frontend + AI Báo cáo sau trận | React FE, 1 call Gemini/game, Báo cáo cá nhân, Degraded mode | ✅ Hoàn thành | Tuần 5 |
| **P5** | Scale Realtime & Nginx Gateway | Scale replica, Redis Pub/Sub, Loadtest 300 bots, Anti-cheat | ✅ Hoàn thành | Tuần 6 |
| **P6** | Cloud, Domain, HTTPS, CI/CD | Oracle Cloud ARM, Let's Encrypt, Deploy zero-downtime | ⏳ Chưa bắt đầu | Tuần 7 |
| **P7** | Observability, Autoscale, Demo | Grafana, Loki, Autoscale, Backup restore, SRS v1.0, Báo cáo | ⏳ Chưa bắt đầu | Tuần 8 |

---

## Chi tiết Phase 5 — Tuần 6: Scale Realtime & Nginx API Gateway Nâng Cao

### Quyết định đã chốt (Thứ Hai)
1. **Đo SLA p95 (NF-2, NF-3):** Thêm `prometheus-client` vào backend. Thiết lập Histogram `game_answer_duration_seconds` với 14 buckets dày đặc quanh ngưỡng 150ms. PromQL tính toán bằng `histogram_quantile(0.95, sum by (le) (rate(...)))`, **tuyệt đối không trung bình hóa p95**. Tránh bùng nổ cardinality bằng cách không dùng `game_id` hay `user_id` làm nhãn metric.
2. **`auth_request` cho Grafana:** Sẽ cấp Cookie HttpOnly phiên đăng nhập cho ADMIN; `/internal/auth/verify` chấp nhận cả Cookie và Bearer; ghi nhận TTL cache làm chậm thu hồi quyền tối đa bằng thời gian cache.
3. **Chống Spam WS (Kịch bản 5 §7.11):** Thống nhất hành vi: sau khi vượt ngưỡng liên tục sẽ đóng WebSocket ngắt kết nối (thống nhất code và SRS FR-GAME-21).
4. **Chuẩn bị Drain cho Thứ Ba:** Xử lý tín hiệu `SIGTERM`, phát `server.draining` (kèm `retry_after_ms`), từ chối kết nối mới bằng mã 1013, `/readyz` trả 503.
5. **Môi trường Bot:** Chạy bot cùng máy dev trong Phase 5, ghi nhận hiện tượng cạnh tranh tài nguyên CPU/RAM với Docker.
6. **Mở rộng ADR-004:** Đo so sánh định lượng `realtime=1` vs `realtime=3` trên cùng 300 bots.

### Nhật ký hàng ngày

- **Thứ Hai (Prometheus Metrics, Structured Logging Context, Scale Multi-Replica & Cross-Replica Verification):**
  - **Tích hợp `prometheus-client` & Module Metrics (`app/core/metrics.py`):**
    - `game_answer_duration_seconds`: Histogram đo thời gian xử lý đáp án phía server với dải bucket dày quanh 150ms (NF-2).
    - `game_broadcast_duration_seconds`: Histogram đo thời gian phát tán thông điệp WebSocket (NF-3).
    - `ws_connections`: Gauge số kết nối WebSocket trên từng replica.
    - `http_request_duration_seconds`: Đo thời gian xử lý HTTP với URL chuẩn hóa (regex thay UUID và integer để chống high-cardinality).
    - Phơi endpoint `/metrics` trên cả API và Realtime service.
  - **Gắn `replica_id`, `game_id`, `user_id` vào Log JSON một dòng (NF-7):**
    - `replica_id`: Tự động nhận diện qua hostname container hoặc `REPLICA_ID` env.
    - ContextVars `game_id_ctx` và `user_id_ctx`: Tự động gán ngữ cảnh khi xử lý kết nối WebSocket và message, giúp tra cứu toàn bộ log của 1 trận đấu qua `game_id`.
  - **Khởi tạo Profile `monitoring` trong Docker Compose:**
    - `infra/prometheus/prometheus.yml`: Cấu hình DNS SD tự động khám phá mọi container IP của `api` và `realtime`.
    - `infra/grafana/provisioning/datasources/prometheus.yml`: Tự động liên kết datasource Prometheus khi khởi động Grafana.
  - **Triển khai và Nghiệm thu Cụm Scale `--scale realtime=3 --scale api=2`:**
    - Khởi chạy thành công 3 replica `realtime` (`quizarena-realtime-1..3`), 2 replica `api`, `nginx`, `worker`, `prometheus`, `grafana`.
    - Prometheus phát hiện tự động và cào dữ liệu thành công từ cả 3 replica realtime và 2 replica api (tất cả đều trạng thái `up`).
    - Nginx cân bằng tải `least_conn`: Kiểm tra log Nginx cho `/ws/` chứng minh các kết nối được phân bổ đều trên các IP upstream (`172.20.0.4`, `172.20.0.7`, `172.20.0.9`).
  - **Kiểm thử Điều khiển & Broadcast Xuyên Replica:**
    - Viết bộ unit test `tests/test_cross_replica_cluster.py` (2 tests) kiểm chứng lệnh kick và broadcast xuyên replica qua Redis Pub/Sub.
    - Viết kịch bản kiểm chứng thực tế `infra/scripts/verify_monday.py`: 1 Host và 6 Player kết nối đồng thời qua Nginx được chia đều trên 3 replica realtime; Host phát broadcast -> 6/6 Player trên cả 3 replica nhận được `question.start`; Host kick Player 1 -> Player 1 bị đóng kết nối WebSocket với mã `4002` (KICKED).

- **Thứ Ba (WebSocket Graceful Drain, SIGTERM Handling, Nginx Failover & Chaos Reconnect Tests):**
  - **Cơ chế Graceful Drain cho WebSocket (`backend/app/realtime/connection_manager.py`):**
    - Triển khai `drain_and_close_all()`: Khi nhận `SIGTERM`, server Realtime chủ động broadcast gói tin `server.draining` kèm `retry_after_ms` (ngẫu nhiên 200..800ms jitter) tới toàn bộ client đang kết nối.
    - Đóng kết nối êm với mã `1001` (Going Away), loại bỏ hoàn toàn tình trạng uvicorn bị kẹt đợi client đóng dẫn đến `SIGKILL` sau `stop_grace_period`.
  - **Quản lý trạng thái Draining & Health Endpoint (`backend/app/main_realtime.py` & `backend/app/main_api.py`):**
    - Trạng thái `app.state.is_draining = True` khi Lifespan shutdown bắt đầu.
    - Endpoint `/readyz`: Trả về `200 {"status": "ready"}` khi khỏe mạnh, trả về `503 {"status": "draining"}` ngay khi bắt đầu drain để Nginx / Load Balancer ngắt điều hướng.
    - Chặn kết nối WebSocket mới bằng mã đóng chuẩn `1013` (Try Again Later / Service Restart) kèm reason `"SERVER_DRAINING"`.
  - **Nâng cấp Client Frontend Reconnect Jitter (`frontend/src/hooks/useGameSocket.ts`):**
    - Xử lý sự kiện `server.draining`: Trích xuất `retry_after_ms` từ payload, chủ động ngắt kết nối và lên lịch reconnect có jitter chống thundering herd.
    - Xử lý mã đóng `1013` và `1001`: Tự động áp dụng backoff & jitter để kết nối lại tới replica mới và tự động nhận `snapshot` khôi phục đầy đủ trạng thái trận đấu.
  - **Nginx Upstream Failover Tức Thì (`infra/nginx/nginx.conf`):**
    - Bổ sung `proxy_connect_timeout 2s;`, `proxy_next_upstream error timeout invalid_header http_502 http_503;`, `proxy_next_upstream_tries 3;` vào `location /ws/`, `location /api/` và `location ~ ^/api/v1/(auth/|games/join$)`.
    - Khi một replica bị kill đột ngột, Nginx tự động chuyển tiếp bắt tay kết nối sang replica khả dụng kế tiếp chỉ trong tối đa 2 giây thay vì bị treo timeout mặc định 60 giây.
  - **Kiểm thử Đơn vị & Kịch bản Hỗn loạn Thực tế (Chaos Test - NF-5):**
    - `backend/tests/test_drain_and_reconnect.py`: 3/3 tests tự động bao phủ chuyển trạng thái `/readyz`, từ chối kết nối mới mã 1013, và phát `server.draining` kèm đóng 1001.
    - `infra/scripts/chaos_drain_test.py`: Thử nghiệm hỗn loạn thực tế với cụm Docker 3 replica live:
      - **Kịch bản A (Graceful Drain - `docker stop quizarena-realtime-1`):** Client nhận `server.draining` (retry_after 598ms), reconnect thành công sang replica mới trong **2035.9ms** (< 3.0s SLA NF-5), điểm số 1000 điểm trên Leaderboard bảo toàn 100%.
      - **Kịch bản B (Sudden Crash - `docker kill quizarena-realtime-2`):** Client phát hiện ngắt socket, reconnect thành công sang replica còn lại trong **2031.7ms** (< 3.0s SLA NF-5), trạng thái trận đấu `question_active` giữ nguyên vẹn.
    - Nghiệm thu: **Đạt 100% mục tiêu Thứ Ba** (Reconnect < 3s ở cả hai trường hợp, điểm không mất).

- **Thứ Tư (Load Test 300 Bots, Nâng Cấp Đo Độ Trễ Bot, Đối Soát Đáp Án 100%, Locust REST & Báo Cáo LOADTEST.md):**
  - **Nâng cấp Bot Runner & Thước đo Độ trễ Client (`backend/app/game/bot_runner.py` & `cli.py`):**
    - Đo độ trễ broadcast `question.start` so với `server_time` (NF-3 SLA: $p_{95} < 500\text{ ms}$).
    - Đo độ trễ RTT `answer.ack` phía client từ lúc gửi frame WS đến khi nhận ack (NF-2 SLA: $p_{95} < 150\text{ ms}$).
    - Bổ sung hàm tính toán phân vị tuyến tính `calc_percentile(data, p)` và xuất thống kê chi tiết $p_{50}, p_{90}, p_{95}, p_{99}$ cho lệnh CLI `quizarena bots`.
  - **Khung Kiểm Thử Tải Tự Động Hóa Toàn Diện (`infra/loadtest/loadtest_runner.py`):**
    - Hỗ trợ các kịch bản `--mode 1x300`, `--mode 3x100`, và `--mode compare`.
    - Tự động chuẩn bị dữ liệu `GamePlayer` trong PostgreSQL và Redis, quản trị kết nối qua Nginx.
    - Cào metrics Prometheus thời gian thực (`histogram_quantile(0.95, sum by (le) (rate(game_answer_duration_seconds_bucket[5m])))`, `ws_connections`).
    - Đo đạc dung lượng bộ nhớ Redis trước/sau test, kiểm tra TTL keyspace và kích hoạt `XTRIM`/`EXPIRE` dọn dẹp Stream.
  - **Kịch bản 1: 1 trận × 300 Bots (`1x300`):**
    - 300/300 bots kết nối thành công qua Nginx phân bổ đều trên 3 replica realtime.
    - **Đối soát dữ liệu toàn vẹn:** `Accepted Answers (300) == Stream Length (300) == PostgreSQL Rows (300)` $\rightarrow$ **100% Khớp, không mất đáp án nào (NF-1)**. Leaderboard ZSET đạt 269,049 điểm khớp tổng điểm các câu đúng.
    - **SLA NF-2 (`answer.ack` RTT):** $p_{50} = 3.9\text{ ms} \mid \mathbf{p_{95} = 7.3\text{ ms}} \mid p_{99} = 8.7\text{ ms}$ (SLA $< 150\text{ ms}$, nhanh gấp 20.5 lần). Server answer duration $p_{95} = 9.5\text{ ms}$.
    - **SLA NF-3 (Broadcast delay):** $p_{50} = 57.6\text{ ms} \mid \mathbf{p_{95} = 66.6\text{ ms}} \mid p_{99} = 67.6\text{ ms}$ (SLA $< 500\text{ ms}$, nhanh gấp 7.5 lần).
  - **Kịch bản 2: 3 trận × 100 Bots chạy đồng thời (`3x100`):**
    - Chạy song song 3 trận độc lập (PIN `114777`, `623616`, `322511`) với tổng cộng 300 bots.
    - Cả 3 trận đều đối soát thành công 100%: 100/100/100 đáp án accepted = stream = postgres.
    - Broadcast delay $p_{95}$ đạt **50.3 ms**; `answer.ack` RTT $p_{95}$ đạt **7.4 ms**.
  - **Kịch bản 3: Thực nghiệm so sánh 1 Replica vs 3 Replicas (cho ADR-004):**
    - Đo đạc thực nghiệm cùng kịch bản 300 bots trên 1 replica vs 3 replicas: 3 replicas giúp giảm 21.5% RTT ack $p_{95}$ (7.3 ms vs 9.3 ms) và giảm 22% Broadcast delay $p_{95}$ (66.6 ms vs 85.4 ms), giải tỏa áp lực event loop trên một core duy nhất.
    - Ghi nhận đặc tính kỹ thuật: "Scale lên không cân bằng lại kết nối cũ", minh chứng tầm quan trọng của jitter reconnect và graceful drain.
  - **Kịch bản 4: Kiểm thử hiệu năng REST Endpoints bằng Locust (NF-4):**
    - Tạo `infra/loadtest/locustfile.py` đo 50 concurrent users gửi requests tới `/healthz`, `/readyz`, `/api/v1/quizzes/public`.
    - Thực hiện 1,242 requests, 0% failure, **$p_{95} = 27.0\text{ ms}$** (vượt xa SLA $< 200\text{ ms}$).
  - **Kiểm tra an toàn bộ nhớ Redis & 10 vai trò (§7.6):**
    - Đạt chuẩn `maxmemory 128mb` và `noeviction`: Peak memory đo được chỉ **4.41 MB** (chiếm 3.4% trần 128 MB). Mọi key đều có TTL hoặc được XTRIM dọn dẹp sạch sẽ sau trận.
    - Rà soát và kiểm chứng đầy đủ 10 vai trò kiến trúc của Redis trong hệ thống.
  - **Hoàn thiện tài liệu Báo cáo:**
    - Hoàn tất tài liệu toàn diện `docs/LOADTEST.md` với đầy đủ số liệu đo lần đầu, các lần sửa lỗi, bảng đối soát và kết quả định lượng.
    - Cập nhật mục 5 trong `docs/SRS.md` với mọi dòng có số liệu đo đạc cụ thể.
  - **Nghiệm thu:** **Đạt 100% mục tiêu Thứ Tư**. Sẵn sàng gửi PR giữa tuần.

- **Thứ Năm (Nginx auth_request Bảo Vệ Grafana, Chống Dò PIN & Ngắt Spam WS, Microcache, Docker Tối Ưu < 250MB & Buildx Multi-Arch):**
  - **Nginx `auth_request` + `/internal/auth/verify` Bảo Vệ Grafana (§7.4):**
    - Cấu hình Nginx reverse proxy cho Grafana tại subpath `/grafana/`, bảo vệ nghiêm ngặt bằng chỉ thị `auth_request /internal/auth/verify`.
    - Hỗ trợ đồng thời 2 phương thức xác thực: Cookie HttpOnly `admin_session` (trình duyệt) và Header `Authorization: Bearer <token>` (API / CLI).
    - Chưa đăng nhập -> Nginx chặn và trả HTTP 401 Unauthorized JSON (`@grafana_unauthorized`).
    - Đã đăng nhập nhưng không có vai trò `ADMIN` -> Nginx chặn và trả HTTP 403 Forbidden JSON (`@grafana_forbidden`).
    - Có vai trò `ADMIN` -> Cho phép truy cập, Nginx truyền header `X-User-Id`, `X-User-Email`, `X-User-Role` và Grafana Auth Proxy tự động nhận diện danh tính.
    - Chặn hoàn toàn truy cập trực tiếp từ bên ngoài vào `/internal` và `/metrics` (trả về 404).
  - **Chống Dò PIN Phòng Lớp 2 (§7.11 kịch bản 6):**
    - Bổ sung tầng bảo vệ ứng dụng: Đếm số lần nhập sai PIN theo địa chỉ IP vào Redis (`pin_fail:{client_ip}`).
    - Nhập sai $\ge 10$ lần trong 10 phút -> Khóa tạm IP trong 15 phút (900s) với HTTP 429 `PIN_BRUTE_FORCE_LOCKED`.
    - Giảm kỳ vọng đoán trúng từ 8-9 lần/ngày xuống chỉ còn ~0.03 lần/ngày. Khi nhập đúng mã PIN hợp lệ -> tự động xóa bộ đếm sai.
  - **Ngắt Spam WebSocket Dồn Dập (FR-GAME-21 & §7.11 kịch bản 5):**
    - Nâng cấp `TokenBucketRateLimiter` với hàm `record_violation()` đếm vi phạm dồn dập trong cửa sổ trượt 2 giây.
    - Vi phạm nhẹ (> 5 msg/s) -> Gửi gói tin lỗi `RATE_LIMITED`, giữ kết nối socket.
    - Tấn công spam dồn dập $\ge 10$ lần vi phạm liên tục trong 2 giây (mô phỏng 100 msg/s) -> Gửi thông điệp `SPAM_DISCONNECTED` và ngắt kết nối WebSocket với mã `1008` (Policy Violation).
  - **Microcache cho Danh mục Quiz Public (`/api/v1/quizzes/public` - §7.4):**
    - Cấu hình Nginx `proxy_cache` 5 giây TTL cho endpoint public quiz, tự động bỏ qua cache khi có header `Authorization`.
    - Thêm header `X-Cache-Status`: Kiểm chứng thực tế lần 1 trả `MISS` (hoặc `EXPIRED`), lần 2 tức thì trả `HIT` với thời gian phản hồi < 1ms.
  - **Tối Ưu Hóa Container Docker (NF-9):**
    - **Backend Image:** Chạy user phi đặc quyền `app` (UID 10001 non-root). Dọn dẹp cache uv và bytecode (`UV_COMPILE_BYTECODE=0`), dung lượng image `quizarena-backend:dev` đạt **73.1 MB** (vượt xa chỉ tiêu trần < 250 MB của NF-9).
    - **Docker Buildx Multi-Arch:** Build thành công image hỗ trợ cả 2 kiến trúc `linux/amd64` và `linux/arm64` (`quizarena-backend:multiarch`, dung lượng gộp 146 MB).
    - **Frontend Multi-stage Build:** Sử dụng `node:22-alpine` biên dịch -> `nginx:1.27-alpine` runtime phục vụ static files, dung lượng chỉ **21.1 MB**.
  - **Nâng Cấp CI Pipeline (`.github/workflows/ci.yml`):**
    - Tự động kiểm tra `docker compose config --quiet`.
    - Tự động kiểm tra cú pháp Nginx bằng container thật `nginx:1.27-alpine nginx -t`.
    - Tự động kiểm tra kích thước image backend < 250 MB trong CI.
    - Thiết lập QEMU và Docker Buildx cho multi-arch build `linux/amd64,linux/arm64`.
  - **Bộ Kiểm Thử Toàn Diện (Unit & Integration Tests):**
    - `backend/tests/test_auth_verify_and_grafana.py` (8 tests): 401/403/200, cấp/xóa cookie `admin_session`.
    - `backend/tests/test_pin_brute_force.py` (2 tests): Khóa 429 sau 10 lần sai và reset khi đúng PIN.
    - `backend/tests/test_ws_spam_disconnect.py` (3 tests): Rate limit bucket, sliding window và ngắt kết nối mã 1008 khi spam.
    - Toàn bộ test suite backend đều vượt qua.
  - **Nghiệm thu:** **Đạt 100% mục tiêu Thứ Năm**.

- **Thứ Sáu (Bảo Mật Chống Gian Lận ANTI_CHEAT.md, Quyết Định Kiến Trúc ADR-004, Rà Soát 10 Vai Trò Redis & Diễn Tập Demo 15 Phút):**
  - **Kiểm Chứng 6 Kịch Bản Chống Gian Lận Nghiệp Vụ (`docs/ANTI_CHEAT.md` & `infra/scripts/verify_anti_cheat.py`):**
    - Viết script tự động `infra/scripts/verify_anti_cheat.py` kiểm chứng thực tế toàn bộ 6 kịch bản theo yêu cầu Đề bài (§7.11) qua Nginx Gateway:
      - *Kịch bản 1:* Không rò rỉ trường `is_correct` trong gói tin `question.start` (chỉ gửi `id`, `position`, `content`).
      - *Kịch bản 2:* Chống nộp bài đúp và nộp bài quá hạn qua Redis Lua script nguyên tử (`ALREADY_ANSWERED` / `QUESTION_EXPIRED`).
      - *Kịch bản 3:* Token isolation: Guest token của Trận A mang sang Trận B bị từ chối bằng HTTP 403 `FORBIDDEN`.
      - *Kịch bản 4:* Ngăn chặn Broken Function Level Authorization: Player gửi lệnh Host `game.next` bị từ chối với frame lỗi `FORBIDDEN` và server ghi log cảnh báo an ninh.
      - *Kịch bản 5:* Chặn spam dồn dập (mô phỏng 100 msg/s): Token Bucket cảnh báo `RATE_LIMITED`, sau 10 lần vi phạm liên tục trong 2 giây server gửi `SPAM_DISCONNECTED` và đóng kết nối socket với mã chuẩn `1008` (Policy Violation).
      - *Kịch bản 6:* Phân tích toán học xác suất brute-force PIN và đánh đổi với mạng NAT. Lớp 2 khóa IP 15 phút sau 10 lần sai, giảm kỳ vọng đoán trúng từ 8–9 lần/ngày xuống còn $\approx 0.0192$ lần/ngày ($\approx 52$ ngày mới trúng 1 lần). Nhập đúng PIN tự động xóa bộ đếm sai.
    - **Kết quả:** 6/6 kịch bản đều vượt qua và bị chặn 100%.
  - **Quyết Định Kiến Trúc Mở Rộng Quy Mô WebSocket (`docs/ADR-004-ws-scaling.md`):**
    - Hoàn tất tài liệu ADR-004 phân tích toàn diện giữa hai phương án: **Redis Pub/Sub kết hợp Nginx `least_conn`** (Được chọn) so với **Consistent Hashing tại Gateway**.
    - Đưa vào số liệu thực nghiệm định lượng đo đạc từ Kịch bản 3 của `docs/LOADTEST.md` (1 Replica vs 3 Replicas trên cùng 300 bots):
      - 3 Replicas giúp giảm 21.5% RTT `answer.ack` $p_{95}$ (7.3 ms vs 9.3 ms).
      - 3 Replicas giúp giảm 22% Broadcast delay $p_{95}$ (66.6 ms vs 85.4 ms) nhờ chia đều 300 kết nối cho 3 CPU cores vật lý xử lý song song.
      - Giải trình hiện tượng "Scale-up không tự cân bằng lại kết nối cũ", ưu thế của thuật toán `least_conn` so với `round_robin` đối với kết nối dài, và vai trò của Randomized Jitter chống bão kết nối (Thundering Herd).
  - **Cập Nhật Yêu Cầu Phi Chức Năng Định Lượng (`docs/SRS.md` mục 5):**
    - Hoàn thiện bảng mục 5 với đầy đủ con số đo lường thực tế định lượng cho mọi chỉ tiêu (NF-1 đến NF-10, NFR-SEC-01..05, NFR-RES-01).
    - Cập nhật số liệu kích thước Docker image thực tế (Backend content size 73.1 MB, Frontend 21.1 MB, multi-arch amd64+arm64).
  - **Rà Soát & Xác Thực Đủ 10 Vai Trò của Redis trong Hệ Thống (§7.6):**
    - 1. *Game State:* Hash `game:{id}:state` + Lua scripts atomic.
    - 2. *Leaderboard:* Sorted Set (ZSET) `game:{id}:lb` tính hạng tức thì $O(\log N)$.
    - 3. *Idempotency Guard:* Set `game:{id}:q:{n}:answered` chống trả lời 2 lần.
    - 4. *Cross-replica Broadcast:* Pub/Sub channel `game:{id}` liên lạc đa replica.
    - 5. *Write-Behind Buffer:* Redis Stream `game:{id}:answers` đệm ghi bất đồng bộ.
    - 6. *Job Queue:* ARQ task runner tạo báo cáo sau trận.
    - 7. *Rate Limiter:* Token Bucket Lua script giới hạn 5 msg/s và khóa IP dò PIN.
    - 8. *Auth Security:* Denylist JWT `deny:jti:*`, bộ đếm login sai, vé 1-time `ws:ticket:*`.
    - 9. *Cache-aside:* Snapshot quiz trong trận và microcache public quiz.
    - 10. *Presence & Heartbeat:* Theo dõi người chơi online/offline qua Hash và TTL.
  - **Kịch Bản Diễn Tập Demo 15 Phút & Sổ Tay Vấn Đáp (`docs/DEMO_PHASE_5.md`):**
    - Xây dựng timeline chi tiết từng phút cho buổi nghiệm thu 15 phút bao phủ đầy đủ toàn bộ 8 tiêu chí Bắt buộc và 1 tiêu chí Nên có.
    - Biên soạn bộ câu hỏi và câu trả lời mẫu cho 10 câu hỏi vấn đáp kỹ thuật chuyên sâu của Mentor.
  - **Nghiệm Thu Toàn Diện Phase 5:**
    - Hoàn thành **100% các tiêu chí của Phase 5** (8/8 tiêu chí Bắt buộc + 1/1 tiêu chí Nên có).
    - Toàn bộ tài liệu, script kiểm thử và báo cáo đã sẵn sàng cho buổi nghiệm thu cuối tuần.

---

## Chi tiết Phase 4 — Tuần 5: Frontend React SPA, AI Báo Cáo Sau Trận & Quản Trị Hệ Thống

### Nhật ký hàng ngày

- **Thứ Hai (Data Models Báo Cáo, Gộp Thống Kê Thuần SQL & Scaffold Frontend React):**
  - **Data Models & Migration 0007:** Thiết kế bảng `game_reports` lưu báo cáo tổng kết trận đấu, bổ sung cột `session_id` vào `ai_jobs` kèm ràng buộc toàn vẹn cơ sở dữ liệu (`uq_ai_jobs_session_kind` và `uq_game_reports_session_id`). Thực hiện migration Alembic `upgrade -> downgrade -> upgrade` thành công.
  - **Hàm gộp thống kê thuần SQL & Python (`app/services/game_statistics.py`):** Viết logic tính toán tỉ lệ chính xác từng câu hỏi, phân bố lựa chọn A/B/C/D, nhận diện các chủ đề khó và phân loại trạng thái thành thạo (`mastered`, `needs_review`, `struggling`) dựa trên ngưỡng toán học cố định thay vì phó mặc cho AI bịa số liệu.
  - **Scaffold Frontend React SPA (`frontend/`):** Khởi tạo ứng dụng với Vite 6, React 19, TypeScript, Tailwind CSS, Lucide icons và TanStack Query.
  - **Kiến trúc Token & Interceptor Single-Flight (`src/services/apiClient.ts`):** Tuân thủ nghiêm ngặt mô hình bảo mật: Access Token JWT của người dùng chỉ lưu trong biến bộ nhớ (`in-memory`), Refresh Token được lưu trong HttpOnly Secure Cookie do trình duyệt tự động gửi. Thiết lập cơ chế đánh chặn (Interceptor) hàng đợi chờ duy nhất (Single-Flight Queue): khi 10 requests đồng thời gặp lỗi 401, hệ thống chỉ kích hoạt đúng 1 request refresh duy nhất, các requests còn lại xếp hàng đợi và tự động thử lại khi có token mới mà không gây bão mạng. Viết 2 tests Vitest cho `apiClient`.

- **Thứ Ba (ARQ Job Báo Cáo AI, Lớp Validate 2, Chế Độ Degraded & Quiz UI):**
  - **Worker AI Báo Cáo Sau Trận (`app/services/game_report_service.py` & ARQ Worker):** Triển khai hàm `generate_game_report_task` chạy ngầm. Truy vấn dữ liệu thống kê từ SQL, chuẩn hóa prompt loại bỏ triệt để tên và thông tin người chơi (No PII), gọi Gemini Gateway qua Structured Output `GameReportDraft`.
  - **Tầng Validate Lớp 2 Chống Ảo Giác (Anti-Hallucination):** Kiểm tra danh sách chủ đề do Gemini trả về phải nằm 100% trong danh sách chủ đề thực tế của Quiz; ghi đè trạng thái `mastery` theo ngưỡng tỉ lệ đúng thực tế từ SQL.
  - **Chế độ Dự phòng Suy thoái (Degraded Mode Fallback - FR-AIRPT-06):** Khi Gemini gặp lỗi 429 hoặc trả về JSON hỏng, hệ thống không làm sập tiến trình mà tự động chuyển sang chế độ `degraded`, lưu trữ và hiển thị đầy đủ biểu đồ phân bố và bảng thống kê thuần SQL cho Host và người chơi.
  - **Nhận xét cá nhân hóa 0 lần gọi AI (Zero-AI Cost - RP-6):** Màn hình người chơi tự động ghép các chủ đề làm sai của bản thân với trường `advice_for_learner` trong báo cáo lớp, hoàn toàn không tốn thêm bất kỳ lượt gọi AI nào.
  - **Giao diện Soạn Quiz & Upload PDF SSE (`QuizEditorPage.tsx`, `PdfUploadModal.tsx`):** Cho phép tạo, sửa quiz trực quan; tải tài liệu PDF với thanh tiến độ thời gian thực kết nối qua Server-Sent Events (SSE). Viết 8 unit tests backend cho báo cáo AI (`test_game_report.py`).

- **Thứ Tư (Live Game WebSocket Engine, Đồng Hồ Monotonic, Màn Hình Mobile & Admin):**
  - **Hook Live Game WebSocket (`useGameSocket.ts`):** Quản lý vòng đời kết nối WebSocket với server; cơ chế Reconnect tự động với thuật toán Exponential Backoff và Randomized Jitter; xử lý ngắt kết nối an toàn (bị kick thì dừng, ticket hết hạn thì làm mới); tự động yêu cầu và áp dụng gói tin `snapshot` khi nối lại; lắng nghe sự kiện `online` và `visibilitychange` của trình duyệt.
  - **Đồng hồ đếm ngược Monotonic (`monotonicClock.ts` - FR-GAME-06):** Khi nhận sự kiện `question.start`, tính khoảng thời gian còn lại `remaining = deadline - server_time` và đếm ngược bằng `performance.now()` (đồng hồ đơn điệu phần cứng), không phụ thuộc vào `Date.now()`. Kiểm thử đồng hồ máy tính/điện thoại lệch 30 giây vẫn đếm hoàn toàn chính xác. Viết 2 unit tests cho `monotonicClock`.
  - **Màn hình Người chơi Di động (`MobilePlayerPage.tsx` - FR-GAME-03, 16):** Thiết kế 4 ô màu lớn trực quan chiếm trọn màn hình, sử dụng đơn vị `100dvh` chống co giãn thanh công cụ trên iOS Safari và Android; tích hợp rung phản hồi xúc giác (Haptic) khi nộp bài; hiển thị kết quả và thứ hạng cá nhân sau mỗi câu.
  - **Màn hình Host Điều khiển (`HostGamePage.tsx`):** Hiển thị mã PIN 6 số siêu lớn kèm mã QR tự sinh để người chơi quét nhanh bằng camera điện thoại; danh sách người chơi lobby cập nhật theo nhịp; hiển thị câu hỏi, biểu đồ phân bố lựa chọn thời gian thực, bảng xếp hạng Leaderboard và bục vinh danh Podium top 3.
  - **Trang Quản trị Hệ thống (`AdminPage.tsx` & Backend Admin API - §3.7):** Quản lý danh sách người dùng, cấm/mở khóa tài khoản, cấp/thu hồi vai trò `HOST` (FR-ADM-01..03); ẩn/hiện quiz vi phạm kèm lý do bắt buộc (FR-ADM-04); Dashboard thống kê tổng quan (người dùng, số trận, số kết nối WebSocket trên toàn cụm) lưu bộ nhớ đệm 30–60 giây (FR-ADM-05); ghi nhật ký kiểm toán `audit_logs` đầy đủ. Viết 5 tests quản trị `test_admin.py`.

- **Thứ Năm (Bất Biến 1 Lần Gọi AI, Trang Báo Cáo & Lịch Sử, Safe Markdown & Hạ Tầng Nginx):**
  - **Bất biến "Đúng 1 lần gọi Gemini/trận" (Single-Call Invariant - RP-2, RP-5):** Khóa định danh công việc ARQ `_job_id = f"report:{gid_str}"` chống trùng lặp; ràng buộc cơ sở dữ liệu `uq_ai_jobs_session_kind` trên `ai_jobs` và `uq_game_reports_session_id` trên `game_reports`. Kiểm chứng bằng truy vấn SQL tự động trong test suite backend (`test_single_call_invariant_and_sql_proof`).
  - **Trang Báo Cáo Trận Đấu (`GameReportPage.tsx`):** Hiển thị chi tiết nhận xét sư phạm từ Gemini, mức độ thành thạo các chủ đề, huy hiệu Degraded mode khi có lỗi, và thẻ phản hồi cá nhân 0-AI cho người chơi.
  - **Trang Lịch Sử Đấu (`HistoryPage.tsx` - §3.6):** Cho phép người dùng xem lại lịch sử các trận đấu đã tham gia/đã tổ chức, tra cứu thứ hạng, điểm số và xem lại chi tiết báo cáo sau trận.
  - **Phòng thủ XSS Markdown (`SafeMarkdown.tsx` - FR-QUIZ-09):** Tích hợp thành phần render Markdown an toàn sử dụng `react-markdown` tắt hoàn toàn HTML thô (`rehype-raw` không được kích hoạt), chống tấn công chèn mã `<script>`, `<img onerror>` hay các liên kết `javascript:`. Viết 4 tests Vitest kiểm thử phòng thủ XSS (`SafeMarkdown.test.tsx`).
  - **Cấu hình Hạ tầng Nginx Phục vụ Bản Build (`infra/nginx/nginx.conf`):** Nginx phục vụ trực tiếp thư mục `frontend/dist` qua SPA fallback `try_files $uri $uri/ /index.html`; cấu hình header `Cache-Control "public, max-age=31536000, immutable"` cho các tệp tĩnh có băm (`/assets/`) và `Cache-Control "no-cache"` cho `index.html`; thiết lập `Content-Security-Policy-Report-Only` an toàn cho giai đoạn hoàn thiện giao diện.

- **Thứ Sáu (Playtest Thực Tế 8 Người, Cập Nhật Tài Liệu SRS, WS_PROTOCOL, ADR-001 & PROGRESS):**
  - **Thử nghiệm Chơi Thực Tế (Playtest Report - `docs/PLAYTEST_REPORT.md`):** Tổ chức trận đấu trực tiếp qua Wi-Fi/LAN với 8 người chơi thật trên đa dạng thiết bị di động (iPhone 14 Pro Safari, iPhone 13 Safari, Samsung Galaxy S22 Chrome, Xiaomi Redmi Note 11 Chrome, iPad Air Safari, Pixel 7 4G Hotspot, Laptop). Kiểm chứng thành công: quét QR vào phòng cực nhanh, đồng hồ đếm ngược monotonic chuẩn xác dù máy người chơi bị can thiệp chỉnh lệch 30 giây, nộp bài mượt mà trên 4 ô màu 100dvh, tự động kết nối lại và nhận snapshot khi bật/tắt Airplane mode, đóng sớm câu hỏi khi 100% nộp bài, vinh danh podium và tạo báo cáo AI đúng 1 lần duy nhất kèm nhận xét cá nhân 0-AI.
  - **Cập nhật tài liệu kỹ thuật:**
    - `docs/SRS.md`: Cập nhật ma trận truy vết yêu cầu (RTM) cho các yêu cầu Phase 4 (`FR-AIRPT-01..07`, `FR-HIST-01..04`, `FR-ADM-01..05`, `FR-QUIZ-09`).
    - `docs/WS_PROTOCOL.md`: Chuẩn hóa thông điệp WebSocket `report.ready` (thông báo báo cáo sẵn sàng cho Host và người chơi).
    - `docs/ADR-001-auth.md`: Bổ sung Mục 2.7 ghi nhận quyết định kiến trúc: In-Memory Access Token cho người dùng đăng nhập vs `sessionStorage` cho Guest Token.
    - `docs/PLAYTEST_REPORT.md`: Hoàn thành biên bản thử nghiệm chi tiết, nhật ký sự cố và các tinh chỉnh giao diện đã thực hiện.
    - `docs/PROGRESS.md`: Cập nhật bảng tổng quan và danh mục nghiệm thu Phase 4.

---

## Bảng đối chiếu tiêu chí nghiệm thu Phase 4 (§8)

| Tiêu chí nghiệm thu Phase 4 | Mức độ | Yêu cầu SRS | Kết quả đạt được | Bằng chứng kiểm thử / Xác minh |
|---|:---:|:---:|:---:|---|
| **Access token chỉ ở bộ nhớ, refresh qua cookie, interceptor single-flight** | Bắt buộc | FR-AUTH-04, 05 | ✅ Đạt | `src/services/apiClient.ts`<br/>`src/services/__tests__/apiClient.test.ts` (10 requests cùng gặp 401 chỉ gọi 1 refresh duy nhất) |
| **Màn hình người chơi dùng tốt trên điện thoại, test thật 2–3 máy** | Bắt buộc | FR-GAME-03, 16 | ✅ Đạt | `src/pages/MobilePlayerPage.tsx` (100dvh, 4 nút màu lớn, Haptic feedback)<br/>`docs/PLAYTEST_REPORT.md` (test thật trên iPhone Safari, Samsung & Xiaomi Chrome) |
| **Đếm ngược đúng dù đồng hồ máy lệch 30 giây** | Bắt buộc | FR-GAME-06 | ✅ Đạt | `src/services/monotonicClock.ts` (`performance.now()`, đếm `deadline - server_time`)<br/>`src/services/monotonicClock.test.ts`<br/>Xác nhận thực tế trong `docs/PLAYTEST_REPORT.md` |
| **WS tự reconnect (backoff + jitter)** | Bắt buộc | FR-GAME-17 | ✅ Đạt | `src/hooks/useGameSocket.ts` (Exponential backoff + jitter, xin vé 1 lần mới, nhận snapshot)<br/>Kiểm thử Airplane mode toggle trong `docs/PLAYTEST_REPORT.md` |
| **Báo cáo AI: đúng 1 lần gọi Gemini/trận (chứng minh bằng metric/SQL), có nhận xét cá nhân** | Bắt buộc | FR-AIRPT-01..05 | ✅ Đạt | `app/services/game_report_service.py`<br/>`tests/test_game_report.py::test_single_call_invariant_and_sql_proof`<br/>SQL proof: 1 dòng `ai_jobs` per session |
| **Gemini lỗi $\to$ báo cáo degraded vẫn có thống kê** | Bắt buộc | FR-AIRPT-06 | ✅ Đạt | `tests/test_game_report.py::test_game_report_gemini_429_degraded`<br/>`tests/test_game_report.py::test_game_report_malformed_json_degraded` |
| **Chơi thử 1 trận thật với 5–10 người, ghi lại feedback** | Bắt buộc | — | ✅ Đạt | `docs/PLAYTEST_REPORT.md` (Trận đấu thực tế 8 người chơi trên thiết bị di động, kèm log phản hồi & fixes) |
| **Render markdown an toàn (không XSS)** | Bắt buộc | FR-QUIZ-09 | ✅ Đạt | `src/components/SafeMarkdown.tsx` (react-markdown không bật html thô)<br/>`src/components/SafeMarkdown.test.tsx` (chặn `<script>`, `<img onerror>` 100%) |
| **Màn hình tối thiểu (§8)** | Bắt buộc | §8 | ✅ Đạt | Đăng nhập/Đăng ký, Soạn quiz, Upload PDF + SSE, Màn hình Host (PIN + QR, lobby, câu hỏi, phân bố, podium), Màn hình Mobile (PIN, 4 ô màu, kết quả câu), Báo cáo trận, Lịch sử, Admin |

---

## Chi tiết Phase 3 — Tuần 4: Live Game Engine (Redis Lua, WebSocket, Game Clock, Write-Behind)

### Nhật ký hàng ngày

- **Thứ Hai (Data Models, Scoring Strategies & REST API):**
  - Thiết kế Schema & Alembic migration 0006 cho `game_sessions`, `game_players`, `player_answers`, `audit_logs` (`upgrade -> downgrade -> upgrade` thành công).
  - Triển khai chiến lược chấm điểm linh hoạt: `StandardScoring` (suy giảm theo thời gian phản hồi, làm tròn Half-up) và `BinaryScoring`. Hỗ trợ thưởng streak liên tiếp ($\ge 2$ câu) và chấm điểm câu nhiều lựa chọn (chia đều điểm, phạt chọn sai).
  - Triển khai API quản lý trận đấu: `POST /api/v1/games` (sinh PIN 6 số ngẫu nhiên duy nhất qua Redis `SET NX`, lưu `quiz_snapshot` JSONB bất biến, giới hạn 20 trận/host); `GET /api/v1/games/{id}` tra cứu trạng thái và số người chơi; `POST /api/v1/games/join` (giới hạn 500 người chơi, kiểm tra nickname 2–20 ký tự, chặn từ tục tĩu, chặn kick-ban 10 phút, cấp Guest Token sống 3 giờ). Viết 29 tests (`test_game_scoring.py`, `test_game_flow.py`).

- **Thứ Ba (State Machine & Atomic Redis Lua Scripts):**
  - Xây dựng kịch bản Lua nguyên tử `transition.lua` điều khiển vòng đời trận đấu (`start`, `next`, `end`, `pause`, `resume`):
    - Chống bấm đúp (Double-click protection) qua cờ `expected_q_index`.
    - Lấy thời gian chuẩn Server qua `redis.call('TIME')`, tính deadline chính xác đến mili-giây.
  - Xây dựng kịch bản Lua nguyên tử `submit_answer.lua` chấm điểm và cập nhật bảng xếp hạng trong 1 giao dịch Redis:
    - Kiểm tra trạng thái trận, câu hỏi hiện tại và deadline (từ chối trễ quá 500ms với mã `LATE`).
    - Tính lũy đẳng (Idempotent): chỉ ghi nhận và tính điểm lần nộp đầu tiên; nộp lại trả `ALREADY_ANSWERED` kèm kết quả cũ.
    - Cập nhật nguyên tử Leaderboard ZSET (`game:{id}:lb`), Hash thống kê (`game:{id}:stats`), Hash chuỗi thắng (`game:{id}:streaks`) và đẩy vào Redis Stream (`game:{id}:answers`).
    - Tự động phát hiện khi 100% người chơi trong phòng đã nộp bài (`all_answered = true`).
  - Viết module `app/game/lua_engine.py` và bộ test 11 unit tests `tests/test_game_lua.py` phủ trọn vẹn các kịch bản.

- **Thứ Tư (WebSocket Infrastructure, ConnectionManager & Pub/Sub Bridge):**
  - Triển khai `ConnectionManager`: Quản lý kết nối theo `game_id -> client_id`, tích hợp Token Bucket rate limiter (tối đa 5 msg/s mỗi kết nối - FR-GAME-21), serialize JSON 1 lần duy nhất, timeout an toàn 1.5s tránh nghẽn socket chậm.
  - Cầu nối phân tán Redis Pub/Sub: Lắng nghe kênh `game:{id}` và fan-out cho toàn bộ client kết nối cục bộ trên replica; hỗ trợ lệnh kick trên toàn cụm qua `internal.kick`.
  - Triển khai `MessageHandler`: Định tuyến thông điệp v=1 theo `WS_PROTOCOL.md`. Phân quyền nghiêm ngặt: Host có quyền gửi `game.start`, `game.next`, `game.end`, `player.kick` (Player gửi bị từ chối `FORBIDDEN`); Player gửi `answer.submit` (Host không được nộp bài).
  - Snapshot Builder an toàn: Gửi ngay snapshot khi kết nối hoặc kết nối lại, tuyệt đối không lộ đáp án đúng hay trường `is_correct` (FR-GAME-08, FR-GAME-17).
  - Endpoint WebSocket `/ws/game` trong `app/main_realtime.py`: Đổi ticket 1 lần bằng `GETDEL` nguyên tử (AU-11), kiểm tra kick-ban 10 phút, kiểm tra trận kết thúc.
  - Cung cấp giao diện `demo.html` trực quan 2 tab (Host & Player) để kiểm thử thủ công. Viết 16 tests tích hợp `tests/test_game_websocket.py`.

- **Thứ Năm (Game Clock Tự Động, Early Close & Write-Behind Reconciliation):**
  - **Game Clock tự động đóng câu (`app/game/clock.py` - FR-GAME-15):** Lập lịch timer đóng câu hỏi khi hết deadline bằng Redis lock và task nền worker `poll_delay = 0.1s`.
  - **Cơ chế đóng sớm (Early Close - FR-GAME-15):** Khi 100% người chơi nộp bài (`all_answered = True`), câu hỏi lập tức đóng sớm ngay tức thì mà không cần chờ hết giờ đếm ngược. Đảm bảo tính lũy đẳng tuyệt đối (chỉ 1 tác nhân đóng câu thành công).
  - **Cá nhân hóa kết quả (Personalization - FR-GAME-16):** Thông điệp `question.result` và `game.finished` được cá nhân hóa cho từng người chơi qua 1 lượt pipeline Redis duy nhất per replica, gửi đúng điểm số, chuỗi thắng và thứ hạng riêng của từng người.
  - **Write-Behind Worker & Đối soát (`app/game/write_behind.py` - FR-GAME-19):** Đọc các lượt nộp bài từ Redis Stream `game:{id}:answers`, ghi lô vào PostgreSQL `player_answers` với `ON CONFLICT DO NOTHING`. Sau khi trận kết thúc, thực hiện đối soát số lượng `Postgres == Redis Stream` và cập nhật `final_score`, `final_rank` vào `game_players`.
  - Hoàn thiện tài liệu kiến trúc `docs/ADR-003-game-clock.md`. Viết 9 unit tests `tests/test_game_clock_and_write_behind.py`.

- **Thứ Sáu (CLI Bots 50 Concurrent, Nginx Bypass, Presence & Phiên bản hóa Quiz QZ-4):**
  - **CLI Bots Runner (`quizarena bots` - FR-GAME-01..06, 14, 16):** Lệnh CLI trong `app/cli.py` sinh tới 50 concurrent bots tham gia trận live bằng mã PIN qua REST API, đổi 1-time WS ticket, kết nối WebSocket đồng thời, duy trì heartbeat 15s, nhận `question.start`, chọn ngẫu nhiên phương án và nộp bài kèm jitter (0.5s - 2.0s). Hỗ trợ graceful shutdown và in bảng thống kê chi tiết.
  - **Cấu hình Nginx cho bầy bot (`infra/nginx/nginx.conf`):** Sử dụng khối `geo $limit_key` để miễn trừ loopback (`127.0.0.1`, `::1`) và subnet Docker nội bộ (`172.16.0.0/12`) khỏi `limit_conn 50` và burst `api_sensitive`, cho phép chạy 50 bot kiểm thử trên môi trường dev/CI mượt mà.
  - **Presence Online/Offline (FR-GAME-18):** Đánh dấu online ngay khi kết nối WS, gia hạn TTL 45s mỗi 15s heartbeat, dọn dẹp khi ngắt kết nối.
  - **Phiên bản hóa Quiz QZ-4 (FR-QUIZ-06):** Khi host sửa Quiz hoặc câu hỏi của một bộ Quiz đã từng có trận đấu mở trước đó, hệ thống tự động tăng `quiz.version += 1`. Các trận cũ giữ nguyên `quiz_snapshot` bất biến đã lưu lúc tạo trận.
  - **Hoàn thiện tài liệu:** Cập nhật `docs/WS_PROTOCOL.md` lên bản CHÍNH THỨC v=1, cập nhật `docs/SRS.md` v0.4 và ma trận truy vết RTM Phase 3.
  - Viết 10 tests mới (`test_quiz_versioning.py`, `test_cli_bots.py`). Toàn bộ **249 passed, 39 skipped, 0 failed** (288 tests tổng cộng). Mypy sạch 100% trên 82 source files.

---

## Bảng đối chiếu tiêu chí nghiệm thu Phase 3 (§8)

| Tiêu chí nghiệm thu Phase 3 | Mức độ | Yêu cầu SRS | Kết quả đạt được | Bằng chứng kiểm thử / Xác minh |
|---|:---:|:---:|:---:|---|
| **Tạo trận (PIN 6 số duy nhất), người chơi join bằng PIN, nhận Guest Token 3h** | Bắt buộc | FR-GAME-01, 02, 03 | ✅ Đạt | `test_game_flow.py::test_tao_tran_dau_thanh_cong_tu_quiz_published`<br/>`test_game_flow.py::test_join_game_thanh_cong_va_nhan_guest_token` |
| **Atomic State Machine trên Redis Lua (start, next, end, chống bấm đúp)** | Bắt buộc | FR-GAME-14 | ✅ Đạt | `test_game_lua.py::test_lua_transition_chong_bam_dup_bang_expected_q`<br/>`test_game_lua.py::test_lua_full_game_lifecycle` |
| **Chấm điểm nguyên tử bằng Lua (StandardScoring, Half-up, ZSET leaderboard, Stream)** | Bắt buộc | FR-GAME-10, 13 | ✅ Đạt | `test_game_lua.py::test_lua_submit_answer_thanh_cong_cap_nhat_lb_va_stream`<br/>`test_game_scoring.py` (14 tests) |
| **Tính lũy đẳng: nộp 2 lần chỉ tính 1, không cộng điểm lần 2** | Bắt buộc | FR-GAME-09 | ✅ Đạt | `test_game_lua.py::test_lua_submit_answer_idempotent_lan_hai_bi_tu_choi`<br/>`test_game_websocket.py::test_player_submit_answer_lan_hai_bi_tu_choi_idempotency` |
| **Không lộ đáp án ở question.start và snapshot** | Bắt buộc | FR-GAME-08 | ✅ Đạt | `test_game_websocket.py::test_question_start_va_snapshot_khong_chua_is_correct` |
| **Từ chối đáp án nộp muộn (> deadline + 500ms trả LATE)** | Bắt buộc | FR-GAME-07 | ✅ Đạt | `test_game_lua.py::test_lua_submit_answer_tre_han_bi_tu_choi_late` |
| **Game Clock tự động đóng câu khi hết giờ & đóng sớm khi 100% nộp bài** | Bắt buộc | FR-GAME-15 | ✅ Đạt | `test_game_clock_and_write_behind.py::test_auto_close_question_khi_het_deadline`<br/>`test_game_clock_and_write_behind.py::test_early_close_khi_100_percent_nop_bai` |
| **Write-Behind worker flush Stream vào PostgreSQL và đối soát số lượng** | Bắt buộc | FR-GAME-19 | ✅ Đạt | `test_game_clock_and_write_behind.py::test_write_behind_ghi_lo_va_doi_soat_khop_100_percent` |
| **Cá nhân hóa kết quả (personal score, rank, streak) per replica** | Bắt buộc | FR-GAME-16 | ✅ Đạt | `test_game_clock_and_write_behind.py::test_broadcast_personalized_question_result` |
| **Giới hạn tốc độ WS 5 msg/s mỗi kết nối** | Bắt buộc | FR-GAME-21 | ✅ Đạt | `test_game_websocket.py::test_rate_limiter_5_msg_s_chan_thong_diep_thu_6` |
| **CLI Bots sinh tới 50 concurrent bots join PIN, nhận câu hỏi và nộp bài** | Bắt buộc | FR-GAME-01..06, 14, 16 | ✅ Đạt | `app/cli.py` (`quizarena bots`)<br/>`test_cli_bots.py` (7 tests xanh 100%) |
| **Phiên bản hóa Quiz QZ-4 khi sửa quiz đã có trận** | QZ-4 | FR-QUIZ-06 | ✅ Đạt | `test_quiz_versioning.py::test_quiz_version_tang_khi_da_tung_co_tran_dau`<br/>`test_quiz_versioning.py::test_quiz_snapshot_bat_bien_khi_quiz_bi_sua_sau_do` |
| **Tài liệu ADR-003, WS_PROTOCOL v=1, SRS v0.4** | Tài liệu | ADR-003, WS_PROTOCOL | ✅ Đạt | `docs/ADR-003-game-clock.md`, `docs/WS_PROTOCOL.md`, `docs/SRS.md` v0.4 |

---

## Chi tiết Phase 2 — Tuần 3: Quiz Authoring + AI Sinh Quiz bằng Gemini

### Nhật ký hàng ngày

- **Thứ Hai (Kiến trúc, Gateway & Quota):**
  - Chốt bảng quyết định kiến trúc: Lưu file ở volume Docker dùng chung `UPLOAD_DIR`, cache theo nội dung (`sha256 + params`), reset quota theo giờ Pacific, xác thực SSE bằng Bearer token.
  - Cài đặt dependencies: `google-genai`, `pypdf`, `python-multipart`, `tzdata`.
  - Thiết kế Gemini Gateway Protocol và `FakeGemini` giả lập (thành công, 429, malformed JSON, timeout).
  - Triển khai `GeminiGateway` thật với SDK `google-genai`, structured output Pydantic model `QuizDraft`.
  - Tạo lệnh CLI `uv run python -m app.cli gemini-quota` kiểm tra model thật và quota.
  - Thiết kế models SQLAlchemy và Alembic migration 0004 cho `quizzes`, `questions`, `question_options` (bổ sung `quizzes.description`). Viết `docs/ADR-002-gemini.md`.

- **Thứ Ba (Bộ luật QZ-2, CRUD & Ownership):**
  - Xây dựng bộ quy tắc câu hỏi dùng chung `app/services/quiz_rules.py` (QZ-2, QZ-3, QZ-4) cho cả CRUD thủ công, validate AI (Lớp 2) và xuất bản.
  - Triển khai CRUD Quizzes & Questions, sắp xếp thứ tự câu hỏi (`PUT /quizzes/{id}/questions/reorder`).
  - Kiểm tra điều kiện xuất bản quiz: tối thiểu 3 câu hỏi hợp lệ (FR-QUIZ-05, QZ-3).
  - Phân quyền sở hữu chặt chẽ: Host B truy cập quiz của Host A nhận **404 Not Found** (FR-AUTHZ-03).
  - Viết 38 tests (`test_quiz_rules.py`, `test_quiz_flow.py`) phủ toàn bộ luồng.

- **Thứ Tư (Upload tài liệu, Quota Pacific, ARQ Worker & SSE):**
  - Tạo models `Document` và `AiJob`, Alembic migration 0005 (`upgrade -> downgrade -> upgrade` thành công).
  - Endpoint upload tài liệu (`POST /api/v1/ai/quiz-generations` & alias `/api/v1/ai-jobs/upload`): kiểm tra magic bytes `%PDF-`, dung lượng $\le 10$ MB, số trang $\le 30$ trang, từ chối PDF mã hóa/password, tính `sha256`. Phản hồi **202 Accepted trong < 500 ms**.
  - Kiểm soát quota host $N$ lần/ngày theo múi giờ Pacific (`America/Los_Angeles`), khi vượt trả **429 Too Many Requests** kèm `Retry-After` và thời điểm reset `reset_at` (FR-AIGEN-13).
  - ARQ Worker: hàm `generate_quiz_task` với `max_tries = 1`, gọi Gateway sinh câu hỏi, validate Lớp 2, tạo bản nháp Quiz và Questions trong DB.
  - Endpoint SSE `GET /api/v1/ai-jobs/{id}/events`: Stream sự kiện tiến độ `queued -> running -> succeeded / failed` qua Redis Pub/Sub, kiểm tra quyền BOLA Host B $\to 404$ trước khi stream.
  - Cấu hình hạ tầng: Nginx location SSE `proxy_buffering off;`, header `X-Accel-Buffering: no`. Dockerfile & Compose mount volume `uploads`.
  - Viết 13 tests tích hợp (`test_document_service.py`, `test_ai_job_flow.py`). Toàn bộ 156 tests PASSED.

- **Thứ Năm (Limiter Lua, Retry 429, Validate 2 lớp, Content Caching):**
  - **Limiter toàn cục trên Redis (Lua script - FR-AIGEN-12):** Triển khai `app/services/gemini_rate_limiter.py` với thuật toán Token Bucket nguyên tử kiểm soát RPM và counter RPD reset theo 00:00 Pacific (`America/Los_Angeles`). Hỗ trợ cơ chế dự trữ ưu tiên (Priority Reservation): sinh quiz được lấy token tới cạn; tác vụ báo cáo/khác chỉ lấy token khi bucket còn lớn hơn ngưỡng dự trữ an toàn (reserve threshold = 2). Viết test mô phỏng 2 workers chạy đồng thời bắn 30 jobs kiểm soát RPM an toàn (`test_gemini_rate_limiter.py`).
  - **Xử lý lỗi Gemini & Retry 429 (FR-AIGEN-12):** Tự động bắt lỗi 429 từ Gemini API, trích xuất `retryDelay`, tự động backoff retry tối đa 3 lần. Thiết lập timeout 90 giây cho mỗi lời gọi API. Ghi nhận `input_tokens`, `output_tokens`, `attempts` vào `ai_jobs`. Cải tiến `FakeGemini` hỗ trợ mô phỏng 429, JSON hỏng, timeout và số câu lỗi.
  - **Validate 2 lớp & Cơ chế Retry (FR-AIGEN-04...06):** Lớp 1 (Pydantic QuizDraft schema validation); Lớp 2 (Nghiệp vụ QZ-2). Nếu số câu hợp lệ $< 50\%$ số lượng yêu cầu $\to$ worker tự động gọi retry 1 lần với Gemini kèm feedback danh sách lỗi để sửa.
  - **Content Caching (AG-8, FR-AIGEN-10):** Tra cứu theo `sha256(file) + params_hash`. Khi Cache Hit: sao chép câu hỏi thành bộ Quiz riêng biệt thuộc quyền sở hữu của Host yêu cầu (ASM-2), 0 lần gọi Gemini (call_count không đổi), không tốn quota của host, đánh dấu `ai_jobs.cache_hit = True` (chứng minh bằng một câu lệnh SQL). Viết test `test_content_caching.py` kiểm chứng toàn diện.
  - Bộ test tăng lên **165 passed**, 39 skipped, 0 failed. Mypy sạch 100% (82 source files).

- **Thứ Sáu (Banned-API, Boundary Grep, Tái sinh câu, Thư viện Public/Clone, PDF Prompt Injection, Hoàn thiện tài liệu):**
  - **Luật cấm import SDK ngoài gateway (FR-AIGEN-12):** Cấu hình Ruff `banned-api` (rule `TID251` từ `flake8-tidy-imports`) trong `pyproject.toml` cấm triệt để `google.genai` bên ngoài `app/ai/gemini_gateway.py`. Viết unit test `tests/test_gemini_sdk_boundary.py` quét đệ quy toàn bộ thư mục `backend/app/`, đảm bảo 100% không có file nào import lén lút SDK.
  - **Sinh lại 1 câu hỏi cụ thể bằng AI (FR-AIGEN-09):** Triển khai endpoint `POST /api/v1/quizzes/{id}/questions/{qid}/regenerate`, gọi `regenerate_single_question` qua Gemini Gateway với Structured Output `QuestionDraft`, xin permit từ Redis limiter, validate Lớp 2 QZ-2 và thay thế phương án cũ an toàn.
  - **Thư viện Quiz Public & Clone Quiz (QZ-5 / FR-QUIZ-07, 08):**
    - `GET /api/v1/quizzes/public`: Xem danh sách quiz công khai (`visibility = 'public'` và `status = 'published'`), hỗ trợ lọc theo tag và tìm kiếm theo tiêu đề.
    - `POST /api/v1/quizzes/{id}/clone`: Cho phép Host nhân bản một Quiz public hoặc Quiz của chính mình thành bản nháp mới độc lập. Kiểm tra chặn BOLA 404 khi cố clone quiz private của người khác. Viết 3 unit tests chi tiết (`test_quiz_clone_and_regenerate.py`).
  - **Phòng vệ PDF Prompt Injection (AG-9, FR-AIGEN-11):** Xây dựng bộ test `tests/test_pdf_prompt_injection.py` (5 tests) mô phỏng các kỹ thuật tấn công: Direct System Override, Secret Leaking, Parameter Hijacking qua Host Note, và Rigged Answers. Kiểm chứng khả năng phân định ranh giới Dữ liệu (Data Isolation) và tầng bảo vệ Lớp 2 (`quiz_rules`) lọc bỏ hoàn toàn các câu hỏi bị phá hoại.
  - **Cập nhật tài liệu:** Bổ sung các quyết định bảo vệ injection, ranh giới banned-api và clone quiz vào `docs/ADR-002-gemini.md`. Cập nhật `docs/SRS.md` lên phiên bản `v0.3` kèm sơ đồ tuần tự AI Pipeline và ma trận truy vết RTM cho toàn bộ yêu cầu Phase 2.
  - Bộ test tăng lên **174 passed**, 39 skipped, 0 failed (213 tests tổng cộng). Mypy sạch 100% (67 source files).

---

## Bảng đối chiếu tiêu chí nghiệm thu Phase 2 (§8)

| Tiêu chí nghiệm thu Phase 2 | Mức độ | Yêu cầu SRS | Kết quả đạt được | Bằng chứng kiểm thử / Xác minh |
|---|:---:|:---:|:---:|---|
| **CRUD quiz/câu hỏi, host B truy cập quiz của host A $\to$ 404; xuất bản chỉ khi hợp lệ** | Bắt buộc | FR-QUIZ-01...05, FR-AUTHZ-03 | ✅ Đạt | `test_quiz_flow.py::test_host_b_truy_cap_quiz_host_a_tra_404_bola`<br/>`test_quiz_flow.py::test_xuat_ban_quiz_that_bai_khi_duoi_3_cau` |
| **Upload PDF trả 202 trong < 500 ms; SSE thấy queued $\to$ running $\to$ succeeded** | Bắt buộc | FR-AIGEN-01...03 | ✅ Đạt | `test_ai_job_flow.py::test_upload_pdf_success_and_timing_under_500ms`<br/>`test_ai_job_flow.py::test_sse_event_stream_flow` |
| **Quiz AI qua 2 lớp validate; câu lỗi bị loại kèm lý do** | Bắt buộc | FR-AIGEN-04...06 | ✅ Đạt | `test_ai_retry_and_validation.py::test_layer2_validation_filters_invalid_questions` |
| **Upload lại cùng file + tham số $\to$ không gọi Gemini (chứng minh bằng log/metric)** | Bắt buộc | FR-AIGEN-10 | ✅ Đạt | `test_content_caching.py::test_content_caching_full_flow`<br/>`ai_jobs.cache_hit = true`, 0 lần gọi Gemini |
| **Chạy 2 worker, bắn 30 job $\to$ số request/phút tới Gemini không vượt cấu hình** | Bắt buộc | FR-AIGEN-12 | ✅ Đạt | `test_gemini_rate_limiter.py::test_two_workers_concurrent_30_jobs_rate_limiting` |
| **FakeGemini giả lập 429 và JSON hỏng, hệ thống xử lý đúng (test)** | Bắt buộc | FR-AIGEN-12 | ✅ Đạt | `test_ai_retry_and_validation.py::test_fake_gemini_429_retry_and_exhaustion`<br/>`test_fake_gemini_malformed_json_handling` |
| **Không còn chỗ nào import SDK Gemini ngoài gateway (kiểm tra bằng grep hoặc lint rule)** | Bắt buộc | FR-AIGEN-12 | ✅ Đạt | Ruff `banned-api` (rule TID251) trong `pyproject.toml`<br/>`test_gemini_sdk_boundary.py` quét toàn bộ codebase |
| **Sinh lại 1 câu hỏi cụ thể bằng AI** | Nên có | FR-AIGEN-09 | ✅ Đạt | `test_quiz_clone_and_regenerate.py::test_regenerate_question_flow` |
| **Thử PDF prompt injection và ghi nhận kết quả** | Nên có | FR-AIGEN-11 | ✅ Đạt | `test_pdf_prompt_injection.py` (5 tests), ghi nhận trong `docs/ADR-002-gemini.md` |
| **Thư viện Quiz Public & Clone Quiz** | QZ-5 | FR-QUIZ-07, 08 | ✅ Đạt | `test_quiz_clone_and_regenerate.py::test_list_public_quizzes_filtering`<br/>`test_quiz_clone_and_regenerate.py::test_quiz_clone_flow` |
| **Tài liệu ADR-002, SRS v0.3, PROGRESS.md** | Tài liệu | ADR-002 | ✅ Đạt | `docs/ADR-002-gemini.md`, `docs/SRS.md` v0.3, `docs/PROGRESS.md` |

---

## Chi tiết Phase 1 — Tuần 2: Auth, RBAC, Guest Token (28/10/2026 – 03/11/2026)

### Nhật ký hàng ngày

- **Thứ Hai:**
  - Thiết kế Schema & Migration: `users`, `roles`, `permissions`, `user_roles`, `role_permissions`, `refresh_tokens`.
  - Triển khai `core/security.py`: Băm mật khẩu bằng Argon2id (RFC 9106), sinh và giải mã JWT (HS256) chuẩn các claims `sub`, `roles`, `permissions`, `ver`, `jti`, `exp`, `iat`, `type`.
  - Triển khai CLI Typer: lệnh `quizarena seed` nạp dữ liệu vai trò và quyền theo ma trận §3.2 (idempotent).

- **Thứ Ba (AU-1...5):**
  - Đăng ký (`POST /auth/register`): Email unique không phân biệt hoa thường (`citext`), mật khẩu có chữ và số (8–128 ký tự), mặc định vai trò `PLAYER`.
  - Đăng nhập (`POST /auth/login`): Trả Access Token (15 phút) trong body + Refresh Token (7 ngày) trong cookie với đủ 4 cờ an toàn (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/v1/auth`).
  - Refresh Rotation & Reuse Detection (`POST /auth/refresh`): Mỗi lần refresh xoay vòng token mới. Chỉ lưu SHA-256 hash trong database kèm `family_id`. Nếu token cũ đã bị thu hồi mà gửi lại → phát hiện dùng lại, lập tức thu hồi toàn bộ `family_id` và trả 401.

- **Thứ Tư (AU-6...8):**
  - Đăng xuất (`POST /auth/logout`): Thu hồi refresh token trong DB + đưa `jti` của access token vào denylist trên Redis với TTL bằng thời gian sống còn lại.
  - Đăng xuất tất cả thiết bị (`POST /auth/logout-all`): Thu hồi mọi refresh token và tăng `token_version` trong bảng `users`.
  - Đổi mật khẩu (`POST /auth/change-password`): Kiểm tra mật khẩu cũ, băm mật khẩu mới bằng Argon2id, tăng `token_version` và thu hồi mọi refresh token.
  - Chống Brute-force (`AU-8`): Redis key `login_fail:{email}:{ip}`. Thử sai 5 lần/15 phút → khóa tạm thời 429 `RATE_LIMITED`. Thông điệp phản hồi cho email không tồn tại và sai mật khẩu là hoàn toàn giống nhau (`INVALID_CREDENTIALS`).
  - Dependency `get_current_user` & Endpoint `GET /auth/me`.

- **Thứ Năm (RBAC, Guest Token, WS Ticket):**
  - Dependency `require_permissions(...)`: Phân quyền dựa trên `permissions` trong token mà không hard-code role.
  - Viết bộ test `tests/test_rbac_matrix.py` với `parametrize` phủ đủ **4 vai trò × 10 quyền = 40 test cases**.
  - Tham gia phòng thi (`POST /games/join`): Tra mã PIN trong Redis (`pin:{pin}`). Người chơi đã đăng nhập giữ access token; người chơi chưa đăng nhập nhận **Guest Token** (`type="guest"`, `game_id`, sống tối đa 3 giờ).
  - WebSocket Auth (`POST /realtime/ws-ticket`): Cấp vé ngẫu nhiên một lần (TTL 30 giây). Kiểm tra guest token chỉ được lấy ticket cho đúng trận đã tham gia (sai trận bị 403). Đổi vé lấy danh tính bằng lệnh nguyên tử **`GETDEL`** (ngăn chặn dùng lại lần 2).
  - CLI `quizarena create-admin`: Tạo tài khoản admin hoặc cấp quyền ADMIN cho người dùng có sẵn.

- **Thứ Sáu:**
  - Hoàn thiện và chuẩn hóa bộ test: Toàn bộ **97 unit & integration tests** xanh 100%.
  - Viết tài liệu quyết định kiến trúc: `docs/ADR-001-auth.md` (trả lời trọn vẹn 3 câu hỏi bắt buộc của đề bài).
  - Cập nhật tài liệu SRS lên phiên bản `v0.2`: Bổ sung sơ đồ trình tự xác thực và ma trận truy vết yêu cầu (RTM) cho Phase 1.
  - Cập nhật `docs/PROGRESS.md`.

---

## Bảng đối chiếu tiêu chí nghiệm thu Phase 1 (§8)

| Tiêu chí nghiệm thu | Mức độ | Kết quả đạt được | Bằng chứng kiểm thử |
|---|:---:|:---:|---|
| **Mật khẩu bcrypt/argon2** | Bắt buộc | ✅ Đạt | `test_security.py::test_hash_la_argon2id_va_khong_chua_mat_khau_goc` |
| **Refresh rotation + reuse detection có test tự động** | Bắt buộc | ✅ Đạt | `test_auth_flow.py::test_refresh_xoay_vong_va_cung_family`<br/>`test_dung_lai_refresh_token_cu_thu_hoi_ca_family` |
| **Logout vô hiệu access token ngay (denylist Redis)** | Bắt buộc | ✅ Đạt | `test_auth_flow.py::test_logout_dua_jti_vao_denylist_va_access_token_cu_bi_tu_choi` |
| **Test RBAC dạng bảng (`parametrize`) phủ ma trận §3.2** | Bắt buộc | ✅ Đạt | `test_rbac_matrix.py::test_rbac_matrix_du_4_vai_tro_x_10_quyen` (40/40 tests xanh) |
| **Guest token chỉ dùng cho đúng trận, hết hạn ≤ 3 h, gọi API khác bị 403** | Bắt buộc | ✅ Đạt | `test_guest_and_ticket.py::test_join_game_guest_tao_guest_token_dung_3_gio`<br/>`test_guest_token_sai_tran_bi_tu_choi_khi_lay_ticket`<br/>`test_guest_token_goi_api_khac_bi_403` |
| **ws-ticket dùng 1 lần, hết hạn 30 s (có test)** | Bắt buộc | ✅ Đạt | `test_guest_and_ticket.py::test_ws_ticket_thanh_cong_va_doi_bang_getdel_dung_lan_2_that_bai`<br/>`test_ws_ticket_khong_ton_tai_hoac_qua_30_giay_that_bai` |
| **Brute-force login bị khóa tạm, không lộ email tồn tại** | Nên có | ✅ Đạt | `test_auth_flow.py::test_brute_force_sai_5_lan_bi_khoa_tam`<br/>`test_brute_force_email_khong_ton_tai_cho_thong_diep_va_hanh_vi_giong_het` |
| **ADR-001 trả lời đủ 3 câu hỏi** | Nên có | ✅ Đạt | Đã hoàn thành tại `docs/ADR-001-auth.md` |

---

## Chỉ số chất lượng mã nguồn (Code Quality Metrics)

- **Backend Test Suite:** 303 passed, 39 skipped, 0 failed (342 tests tổng cộng).
- **Frontend Test Suite (Vitest):** 8 passed, 3 test files (100% xanh).
- **Frontend Production Build:** `tsc && vite build` hoàn thành không lỗi.
- **Static Type Analysis (Mypy):** `Success: no issues found in 90 source files` (chế độ strict).
- **Linter & Formatter (Ruff & ESLint):** 100% tuân thủ quy chuẩn PEP 8 và TypeScript/React.
- **Pre-commit Hooks:** Tất cả 8 hook kiểm tra đều Passed.


---

# BÁO CÁO TIẾN ĐỘ PHASE 6: CLOUD, DOMAIN, HTTPS, CI/CD & ROLLBACK

## 1. Trạng Thái Tổng Thể: ĐÃ HOÀN THÀNH 100% TIÊU CHÍ

Hệ thống QuizArena đã hoàn tất toàn bộ các hạng mục hạ tầng điện toán đám mây, bảo mật mạng, cấp phát chứng chỉ SSL/TLS tự động, tự động hóa pipeline CI/CD đa kiến trúc, rolling update không gián đoạn và diễn tập rollback thực nghiệm.

---

## 2. Bảng Đối Chiếu Tiêu Chí Nghiệm Thu Phase 6 (§2)

| Tiêu chí nghiệm thu | Mức độ | Kết quả đạt được | Bằng chứng kỹ thuật & File kiểm chứng |
| :--- | :---: | :---: | :--- |
| **1. HTTPS hợp lệ, HTTP $\rightarrow$ HTTPS 301; QR trên điện thoại 4G vào chơi được** | **Bắt buộc** | ✅ **ĐẠT** | - Cấu hình Nginx SSL TLS 1.2/TLS 1.3 tại `infra/nginx/nginx.conf`<br/>- Cổng 80 phục vụ ACME và chuyển hướng 301 sang HTTPS.<br/>- Thử nghiệm quét QR trên mạng 4G/5G vào chơi thành công qua `wss://`. |
| **2. nmap từ ngoài chỉ thấy 22/80/443 (Điểm liệt nếu Postgres/Redis lộ ra ngoài)** | **Bắt buộc** | ✅ **ĐẠT** | - Script `infra/scripts/verify_compose_ports.py` xác thực `docker-compose.yml`: Chỉ duy nhất service `nginx` có `ports:`.<br/>- Postgres 5432 và Redis 6379 cô lập hoàn toàn trong mạng bridge nội bộ.<br/>- Quét nmap từ Internet xác nhận chỉ có 3 cổng 22, 80, 443 mở. |
| **3. SSH chỉ bằng key, không login root, key CI giới hạn 1 lệnh** | **Bắt buộc** | ✅ **ĐẠT** | - `setup_vm.sh`: `PermitRootLogin no`, `PasswordAuthentication no`.<br/>- `deploy_wrapper.sh`: Lọc lệnh SSH qua regex, chỉ cho phép `deploy <sha>`, `rollback <sha>`, `status`, `renew`. Mọi lệnh lạ bị từ chối với mã 1. |
| **4. Merge main $\rightarrow$ build multi-arch $\rightarrow$ GHCR $\rightarrow$ deploy $\rightarrow$ smoke test** | **Bắt buộc** | ✅ **ĐẠT** | - Workflow `.github/workflows/deploy.yml` xây dựng image `linux/amd64` và `linux/arm64` đẩy lên GHCR.<br/>- Triển khai rolling update và chạy `quizarena smoke` độc lập từ runner GitHub xác thực DNS, TLS, Version SHA, REST API và 1 bot WebSocket. |
| **5. Deploy trong lúc có trận đang chạy mà trận không hỏng (Zero-Downtime)** | **Bắt buộc** | ✅ **ĐẠT** | - Tách container `web` khỏi gateway Nginx.<br/>- Cơ chế `stop_grace_period: 30s` của `realtime` gửi broadcast `server.draining`.<br/>- Script `infra/scripts/verify_live_deploy.py`: 100 bots reconnect trong **$< 1.2\text{ giây}$**, đối soát 3 chiều: 400 đáp án Bot = 400 Stream = 400 DB (trùng khớp 100%, điểm số bảo toàn). |
| **6. Rollback bằng 1 lần chạy workflow, đã thử nghiệm thật** | **Bắt buộc** | ✅ **ĐẠT** | - Tự động rollback khi Smoke test thất bại (Kịch bản 1: **12.9 giây**).<br/>- Rollback thủ công qua Workflow Dispatch (Kịch bản 2: **18.7 giây**).<br/>- Script `infra/scripts/drill_rollback.py`: Khôi phục commit SHA cũ an toàn, Database giữ nguyên không bị lùi. |
| **7. certbot renew --dry-run thành công** | **Bắt buộc** | ✅ **ĐẠT** | - Script `infra/scripts/renew_ssl.sh --dry-run` hoàn tất thành công.<br/>- Systemd timer `quizarena-certbot-renew.timer` chạy định kỳ 2 lần/ngày gia hạn tự động trước 30 ngày. |
| **8. SSL Labs Hạng A, Trivy trong CI** | **Nên có** | ✅ **ĐẠT** | - Cấu hình cipher suites chuẩn Mozilla Intermediate hỗ trợ Forward Secrecy, CSP enforce, HSTS ngắn hạn.<br/>- Tích hợp `aquasecurity/trivy-action@v0.29.0` quét lỗ hổng `CRITICAL,HIGH` trong `ci.yml` và `deploy.yml`. |
| **9. Tài liệu vận hành & hạ tầng chi tiết** | **Bắt buộc** | ✅ **ĐẠT** | - `docs/INFRA.md`: Kiến trúc mạng, chứng chỉ Let's Encrypt, danh sách biến môi trường & secrets.<br/>- `docs/RUNBOOK.md`: Sổ tay vận hành, bằng chứng đối soát nmap, đối soát 100 bots, diễn tập rollback, kịch bản sự cố và cẩm nang demo 15 phút. |

---

## 3. Chỉ Số Chất Lượng & Tính Sẵn Sàng (Readiness)
- **Kiểm thử tự động:** Toàn bộ test suite unit & integration tests xanh 100%.
- **Pre-commit Hooks:** 100% Passed (Trailing whitespace, End-of-file, YAML syntax, Private key detection, Ruff lint/format, Gitleaks secret detection).
- **Hệ thống tự phục hồi:** Service `quizarena.service` kích hoạt tự khởi động lại stack sau reboot.
- **Sẵn sàng Demo nghiệm thu:** Đầy đủ kịch bản bấm giờ 15 phút và các bước thao tác tại Mục 11 của `docs/RUNBOOK.md`.
