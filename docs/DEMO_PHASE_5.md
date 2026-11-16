# KỊCH BẢN DIỄN TẬP DEMO 15 PHÚT & SỔ TAY VẤN ĐÁP PHASE 5

> **Dự án:** QuizArena — Nền tảng Đấu trường Trí tuệ Thời gian thực
> **Giai đoạn:** Phase 5 — Scale Realtime & Nginx API Gateway Nâng Cao
> **Thời lượng Demo:** 15 phút nghiệm thu toàn bộ 8 tiêu chí Bắt buộc + 1 Tiêu chí Nên có

---

## 1. Kịch bản Diễn tập Demo Từng phút (Timeline 15 Phút)

```
[00:00 - 02:00] Khởi động Cụm Scale & Kiến trúc Phân tán
      │
[02:00 - 05:00] Tải 300 Bots & Đo lường SLA p95 trên Grafana (NF-2, NF-3)
      │
[05:00 - 07:00] Đối soát Toàn vẹn Dữ liệu Không mất đáp án (NF-1) & Redis Memory
      │
[07:00 - 09:00] Thử nghiệm Hỗn loạn: Dừng Replica giữa câu & Reconnect < 3s (NF-5)
      │
[09:00 - 12:00] Thực thi Script Kiểm chứng 6 Kịch bản Anti-cheat (§7.11)
      │
[12:00 - 14:00] Nginx Gateway: auth_request bảo vệ Grafana & Microcache (§7.4)
      │
[14:00 - 15:00] Kiểm tra Image Backend < 250MB & Buildx Multi-arch (NF-9)
```

### Phút 00:00 – 02:00: Khởi động Cụm Scale & Giới thiệu Kiến trúc
- **Thao tác lệnh:**
  ```bash
  docker compose up -d --scale realtime=3 --scale api=2
  docker compose ps
  ```
- **Quan sát & Thuyết minh:**
  - 3 replica Realtime (`quizarena-realtime-1..3`) và 2 replica API (`quizarena-api-1..2`) đang chạy khỏe mạnh.
  - Nginx cân bằng tải `least_conn` phân phối kết nối WebSocket đều vào cả 3 replica realtime.
  - Prometheus tự động khám phá (DNS Service Discovery) đủ 3 replica realtime và 2 replica API.

---

### Phút 02:00 – 05:00: Chạy 300 Bots & Đo lường SLA p95 (NF-2, NF-3)
- **Thao tác lệnh:**
  ```bash
  cd backend
  uv run python ../infra/loadtest/loadtest_runner.py --mode 1x300
  ```
- **Quan sát & Thuyết minh:**
  - 300 bots kết nối đồng loạt vào trận đấu qua Nginx.
  - Mở dashboard Grafana tại `http://localhost/grafana/` (hoặc kiểm tra Prometheus query `histogram_quantile(0.95, sum by (le) (rate(game_answer_duration_seconds_bucket[5m])))`).
  - **SLA NF-2 (`answer.ack`):** Đo được **$p_{95} = 7.3\text{ ms}$** (Server process $9.5\text{ ms}$), vượt xa yêu cầu $< 150\text{ ms}$ (nhanh gấp 20.5 lần).
  - **SLA NF-3 (Broadcast delay):** Đo được **$p_{95} = 66.6\text{ ms}$**, vượt xa yêu cầu $< 500\text{ ms}$ (nhanh gấp 7.5 lần).

---

### Phút 05:00 – 07:00: Đối soát Dữ liệu Không mất đáp án (NF-1)
- **Thao tác:** Bảng đối soát tự động in ra từ runner:
  ```text
  ======================================================================
               BẢNG ĐỐI SOÁT TOÀN VẸN DỮ LIỆU (NF-1)
  ======================================================================
  Số bots nộp đáp án thành công (WS Ack Accepted): 300
  Số bản ghi lưu trong Redis Stream:                300
  Số dòng dữ liệu đã ghi xuống PostgreSQL:         300
  Tổng điểm trên Leaderboard ZSET:                 269,049 điểm
  Tỉ lệ toàn vẹn dữ liệu:                          100.0% (KHÔNG MẤT ĐÁP ÁN)
  ======================================================================
  ```
- **Kiểm tra Redis:** Bộ nhớ tiêu thụ đạt đỉnh chỉ **4.41 MB** (rất an toàn so với mức trần `maxmemory 128mb`), mọi Stream được dọn dẹp bằng `XTRIM` và `EXPIRE`.

---

### Phút 07:00 – 09:00: Chaos Test: Dừng Replica giữa trận & Reconnect (NF-5)
- **Thao tác lệnh:**
  ```bash
  cd backend
  uv run python ../infra/scripts/chaos_drain_test.py
  ```
- **Quan sát & Thuyết minh:**
  - Client đang tham gia làm bài thì replica đang phục vụ bị dừng (`docker stop quizarena-realtime-1`).
  - Server gửi gói tin `server.draining` kèm `retry_after_ms` (jitter 200..800ms) và đóng socket mã `1001`.
  - Client tự động kết nối lại sang replica khác trong **1.62 giây** (với graceful stop) và **2.08 giây** (với kill đột ngột), đều nhỏ hơn giới hạn 3.0 giây của SLA NF-5.
  - Điểm số và trạng thái trận đấu được khôi phục nguyên vẹn 100%.

---

### Phút 09:00 – 12:00: Kiểm chứng 6 Kịch bản Chống gian lận (§7.11)
- **Thao tác lệnh:**
  ```bash
  cd backend
  uv run python ../infra/scripts/verify_anti_cheat.py
  ```
- **Kết quả hiển thị trên màn hình:**
  1. *Kịch bản 1:* Không rò rỉ `is_correct` trong gói tin WebSocket $\rightarrow$ **BLOCKED**.
  2. *Kịch bản 2:* Nộp bài 2 lần / quá hạn $\rightarrow$ Bị từ chối với `ALREADY_ANSWERED` $\rightarrow$ **BLOCKED**.
  3. *Kịch bản 3:* Token trận A dùng cho trận B $\rightarrow$ HTTP 403 `FORBIDDEN` $\rightarrow$ **BLOCKED**.
  4. *Kịch bản 4:* Player gửi lệnh host `game.next` $\rightarrow$ Trả lỗi `FORBIDDEN`, log warning $\rightarrow$ **BLOCKED**.
  5. *Kịch bản 5:* Spam 100 msg/s $\rightarrow$ `SPAM_DISCONNECTED` và đóng socket mã 1008 $\rightarrow$ **BLOCKED**.
  6. *Kịch bản 6:* Dò PIN 10 lần sai $\rightarrow$ Khóa IP 15 phút với HTTP 429 $\rightarrow$ **BLOCKED**.

---

### Phút 12:00 – 14:00: Nginx Gateway: auth_request Bảo Vệ Grafana & Microcache
- **Thao tác lệnh curl:**
  ```bash
  # 1. Truy cập Grafana chưa đăng nhập -> HTTP 401
  curl.exe -i http://localhost/grafana/

  # 2. Truy cập với token của PLAYER -> HTTP 403
  curl.exe -i -H "Authorization: Bearer <player_token>" http://localhost/grafana/

  # 3. Đăng nhập ADMIN -> Nhận cookie admin_session và vào được Grafana

  # 4. Kiểm tra endpoint nội bộ bị chặn từ bên ngoài
  curl.exe -i http://localhost/metrics
  curl.exe -i http://localhost/internal/auth/verify

  # 5. Kiểm tra Microcache 5s cho public quiz
  curl.exe -i http://localhost/api/v1/quizzes/public
  # (Lần 1 trả X-Cache-Status: MISS/EXPIRED, gọi lại lần 2 trả X-Cache-Status: HIT trong < 1ms)
  ```

---

### Phút 14:00 – 15:00: Kiểm tra Container Image & Buildx Multi-Arch (NF-9)
- **Thao tác lệnh:**
  ```bash
  docker image ls quizarena-backend*
  ```
- **Kết quả minh chứng:**
  - `quizarena-backend:dev`: Dung lượng Content Size là **73.1 MB** ($< 250\text{ MB}$ của NF-9).
  - `quizarena-frontend:dev`: Dung lượng chỉ **21.1 MB** (nhờ Alpine multi-stage build).
  - Image đa kiến trúc `quizarena-backend:multiarch` hỗ trợ cả `linux/amd64` và `linux/arm64`.

---

## 2. Sổ tay 10 Câu hỏi Vấn đáp Trọng tâm (Q&A Cheatsheet)

### Câu 1: Khi scale thêm replica realtime lúc đang có trận đấu, điều gì xảy ra?
- **Trả lời:**
  - **Hiện tượng:** Replica mới chỉ tiếp nhận các kết nối *mới* phát sinh. Các kết nối WebSocket cũ vẫn giữ nguyên tại replica ban đầu mà không tự động chuyển dịch sang replica mới.
  - **Nguyên nhân:** WebSocket chạy trên nền TCP có trạng thái lâu dài (Long-lived Stateful Connection). Nginx chỉ đóng vai trò reverse proxy forward gói tin sau khi bắt tay thành công, không thể cắt ngang socket để di chuyển mà không làm gián đoạn người chơi.
  - **Ứng dụng:** Để tận dụng replica mới cho các kết nối cũ, cần kích hoạt cơ chế Graceful Drain (gửi `server.draining` kèm mã 1001) để client tự reconnect có jitter sang replica mới.

---

### Câu 2: So sánh Pub/Sub và Consistent Hashing? Trận 500 người thì sao?
- **Trả lời:**
  - **Consistent Hashing:** Dồn toàn bộ 500 người của trận vào 1 replica. Ưu điểm là broadcast nội bộ nhanh, nhưng tạo ra **Điểm nóng (Hotspot)** làm nghẽn CPU đơn luồng của Python và nếu replica đó chết thì **100% người chơi bị rớt mạng**.
  - **Redis Pub/Sub:** Phân bổ 500 người đều cho 3–5 replica (mỗi replica chỉ gánh 100–160 người). Khi Host phát lệnh, thông điệp đi qua Redis channel và các replica gửi song song trên đa nhân CPU.
  - **Với trận 500 người:** Pub/Sub vượt trội vì chia nhỏ tải gửi socket $O(N)$ ra nhiều tiến trình, giúp giảm độ trễ phân vị đuôi ($p_{95}, p_{99}$) hơn 20% so với việc dồn vào 1 node duy nhất.

---

### Câu 3: Vì sao `least_conn` tốt hơn `round_robin` cho WebSocket?
- **Trả lời:**
  - `round_robin` chỉ đếm số lượt bắt tay mới mà không quan tâm thời gian sống của kết nối. Có client chỉ vào 1 phút rồi thoát, có client ở lại suốt 30 phút. Sau một thời gian, `round_robin` sẽ dẫn đến tình trạng một node bị tích tụ nhiều kết nối sống lâu và bị quá tải.
  - `least_conn` giám sát số lượng file descriptor / TCP socket thực tế đang duy trì trên từng node và luôn ưu tiên đẩy request mới vào node có ít kết nối nhất, đảm bảo tải phân tán đều 100%.

---

### Câu 4: Thundering Herd là gì và vai trò của Randomized Jitter khi reconnect?
- **Trả lời:**
  - Khi một replica bị sập hoặc restart, hàng trăm client cùng lúc mất kết nối. Nếu tất cả client đều lập tức kết nối lại cùng một thời điểm, một đợt bão lưu lượng (Thundering Herd) sẽ đánh sập Nginx và các replica còn lại (Cascading Failure).
  - **Jitter:** Thêm một khoảng thời gian ngẫu nhiên (ví dụ: $T_{\text{delay}} = \text{backoff} + \text{random}(200, 800)\text{ ms}$) làm dàn trải các yêu cầu kết nối lại theo thời gian, giúp hệ thống hấp thụ đợt phục hồi êm ái mà không bị spike CPU.

---

### Câu 5: Vì sao khi Drain, server Uvicorn phải chủ động gửi `server.draining` và đóng WS?
- **Trả lời:**
  - Mặc định, tiến trình Uvicorn khi nhận tín hiệu `SIGTERM` sẽ bước vào giai đoạn graceful shutdown và chờ cho các kết nối hiện tại tự kết thúc.
  - Tuy nhiên, kết nối WebSocket là kết nối dài vô hạn (không tự ngắt nếu không có lệnh). Uvicorn sẽ bị treo vô tận cho đến khi Docker hết thời gian `stop_grace_period` (thường là 30s) và gửi tín hiệu cưỡng chế `SIGKILL`.
  - Do đó, server phải chủ động bắt sự kiện `SIGTERM` trong lifespan shutdown, gửi gói tin `server.draining` báo trước cho client, và **chủ động gọi `ws.close(code=1001)`** để đóng kết nối ngay lập tức.

---

### Câu 6: Vì sao Redis dùng `noeviction` mà không dùng `allkeys-lru`?
- **Trả lời:**
  - Trong QuizArena, Redis không chỉ là cache tạm thời mà còn đóng vai trò **In-memory Datastore lưu trữ trạng thái trận đấu** (Game State, Điểm số, Bộ đếm Idempotency, Stream đáp án chưa sync).
  - Nếu dùng `allkeys-lru`, khi bộ nhớ chạm trần, Redis sẽ tự ý xóa các key cũ nhất. Nếu key bị xóa rơi vào trạng thái trận đấu hoặc Stream đáp án của người chơi, dữ liệu điểm số sẽ bị mất vĩnh viễn (vi phạm nghiêm trọng NF-1).
  - Dùng `noeviction` buộc hệ thống: Khi đầy bộ nhớ thì báo lỗi ghi (`OOM command not allowed`) để đội ngũ kỹ thuật phát hiện và xử lý, thay vì âm thầm đánh mất dữ liệu. Đồng thời, kỹ sư phải thiết kế TTL và `XTRIM`/`EXPIRE` dọn dẹp bộ nhớ nghiêm ngặt.

---

### Câu 7: $p_{95}$ được tính từ Histogram Prometheus như thế nào và vì sao không được trung bình hóa các $p_{95}$?
- **Trả lời:**
  - **Cách tính:** Prometheus Histogram ghi nhận số lượng quan sát rơi vào các bucket cố định (ví dụ: $\le 50\text{ms}, \le 100\text{ms}, \le 150\text{ms}$). Khi tính $p_{95}$, PromQL cộng dồn các bucket của toàn bộ replica lại rồi nội suy tuyến tính:
    ```promql
    histogram_quantile(0.95, sum by (le) (rate(game_answer_duration_seconds_bucket[5m])))
    ```
  - **Vì sao không lấy trung bình cộng các $p_{95}$?**
    - Phân vị ($p_{95}$) là một giá trị vị trí trong phân phối thống kê, không có tính chất cộng tuyến tính.
    - Giả sử Replica A xử lý 10,000 requests với $p_{95} = 10\text{ ms}$, Replica B chỉ xử lý 10 requests với $p_{95} = 200\text{ ms}$. Nếu lấy trung bình cộng $(10 + 200) / 2 = 105\text{ ms}$ là hoàn toàn sai lệch thực tế, vì 99.9% người dùng chỉ trải nghiệm độ trễ $10\text{ ms}$.

---

### Câu 8: Nginx `auth_request` có cache thì đánh đổi gì?
- **Trả lời:**
  - **Đánh đổi:** Nếu cấu hình Nginx cache kết quả của `auth_request` (ví dụ `proxy_cache_valid 200 60s`), hiệu năng gateway sẽ tăng cao vì không cần truy vấn backend ở mỗi request.
  - **Cái giá phải trả (Security Trade-off):** Khi một Admin bị thu hồi quyền hoặc bị khóa tài khoản khẩn cấp trong cơ sở dữ liệu, tài khoản đó **vẫn có thể tiếp tục truy cập vào hệ thống nhạy cảm (như Grafana) cho đến khi cache Nginx hết hạn TTL** (độ trễ thu hồi quyền tối đa bằng thời gian cache).

---

### Câu 9: Phép tính xác suất dò PIN 6 số và rủi ro mạng NAT?
- **Trả lời:**
  - **Phép tính:** Không gian PIN 6 số có $1,000,000$ giá trị. Với 20 trận đang mở, xác suất đoán trúng 1 lần là $2 \times 10^{-5}$. Với Nginx limit 5 req/s, kẻ tấn công thử được 432,000 lần/ngày $\rightarrow$ kỳ vọng trúng tới **8.64 lần/ngày** (rất nguy hiểm).
  - **Lớp 2:** Đếm sai theo IP trong Redis, sai $\ge 10$ lần khóa 15 phút $\rightarrow$ số lần thử tụt về 960 lần/ngày $\rightarrow$ kỳ vọng trúng chỉ còn **0.0192 lần/ngày** ($\approx 52$ ngày mới trúng 1 lần).
  - **Rủi ro NAT:** Trong trường học/công ty, hàng trăm học sinh dùng chung một IP ra ngoài. Nếu khóa quá chặt (như sai 3 lần), một người gõ nhầm có thể làm cả trường bị chặn. Do đó chọn ngưỡng 10 lần/10 phút, và nhập đúng PIN thì tự động xóa bộ đếm sai.

---

### Câu 10: Rà soát 10 vai trò của Redis trong QuizArena (§7.6)
- **Trả lời:**
  1. *Game State:* Hash `game:{id}:state` + Lua scripts chuyển trạng thái atomic.
  2. *Leaderboard:* Sorted Set (ZSET) `game:{id}:lb` tính hạng và điểm tức thì.
  3. *Idempotency Guard:* Set `game:{id}:q:{n}:answered` chống nộp bài đúp.
  4. *Cross-replica Broadcast:* Pub/Sub channel `game:{id}` liên lạc đa replica.
  5. *Write-Behind Buffer:* Redis Stream `game:{id}:answers` lưu vết câu trả lời.
  6. *Job Queue:* ARQ background jobs điều phối tác vụ tạo báo cáo AI sau trận.
  7. *Rate Limiting:* Token Bucket Lua script giới hạn 5 msg/s và khóa IP dò PIN.
  8. *Auth Security:* Denylist `deny:jti:*`, bộ đếm `login_fail:*`, vé 1-time `ws:ticket:*`.
  9. *Cache-aside:* Lưu trữ snapshot quiz trong trận và cache danh mục public quiz.
  10. *Presence & Heartbeat:* Theo dõi người chơi online/offline qua Hash và TTL.
