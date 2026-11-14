# BÁO CÁO THỬ NGHIỆM CHƠI THỰC TẾ (PLAYTEST REPORT) — PHASE 4

> **Dự án:** QuizArena — Nền tảng Đấu trường Trí tuệ Thời gian thực
> **Thời gian thực hiện:** Thứ Sáu, Tuần 5 (Phase 4)
> **Phiên bản hệ thống:** QuizArena v0.4 (Frontend React SPA + Backend FastAPI + Redis Realtime)
> **Mục tiêu:** Kiểm thử toàn diện 8 tiêu chí Bắt buộc của Phase 4 trong điều kiện người dùng thật sử dụng thiết bị di động thật qua mạng LAN/Wi-Fi, đánh giá độ trễ, khả năng phục hồi kết nối, độ chính xác đồng hồ đếm ngược và trải nghiệm người chơi.

---

## 1. Môi trường & Thiết bị Thử nghiệm

### 1.1. Hạ tầng Triển khai Thử nghiệm
- **Máy chủ Host (Laptop Windows 11):**
  - CPU: AMD Ryzen / Intel Core i7, 16GB RAM.
  - Hạ tầng Docker: PostgreSQL 16 (`5433`), Redis 7 (`6380`), Nginx Reverse Proxy (`80`).
  - Dịch vụ ứng dụng: FastAPI backend (`8000`), Realtime WebSocket engine (`8001`), ARQ Background Worker (AI Report).
  - Bản build Frontend: React SPA (`dist/`) phục vụ trực tiếp qua Nginx trên cổng 80 với cấu hình Cache-Control tối ưu và Content Security Policy (Report-Only).
  - Địa chỉ IP LAN nội bộ: `http://192.168.1.15` (mở port 80 Windows Firewall).

### 1.2. Danh sách Thiết bị & Người tham gia (8 Người chơi + 1 Host)

| STT | Người tham gia | Vai trò | Thiết bị sử dụng | Hệ điều hành & Trình duyệt | Kết nối mạng |
|:---:|---|:---:|---|---|:---:|
| **Host** | Host Điều phối | Host | Laptop ThinkPad P14s | Windows 11 — Chrome 124 | LAN Wi-Fi 5GHz |
| **P1** | Nguyễn Minh Tuấn | Player | iPhone 14 Pro | iOS 17.4 — Safari | Wi-Fi 5GHz |
| **P2** | Trần Thị Bích | Player | iPhone 13 | iOS 16.6 — Safari | Wi-Fi 2.4GHz |
| **P3** | Lê Hoàng Nam | Player | Samsung Galaxy S22 | Android 14 — One UI 6.0 — Chrome | Wi-Fi 5GHz |
| **P4** | Phạm Thu Hà | Player | Xiaomi Redmi Note 11 | Android 12 — MIUI 13 — Chrome | Wi-Fi 2.4GHz |
| **P5** | Đỗ Quốc Khánh | Player | Google Pixel 7 | Android 14 — Chrome | 4G Hotspot (Độ trễ cao) |
| **P6** | Vũ Phương Linh | Player | iPad Air 5 | iPadOS 17.2 — Safari | Wi-Fi 5GHz |
| **P7** | Đặng Văn Huy | Player | Laptop Dell XPS 13 | Windows 11 — Edge 124 | Wi-Fi 5GHz |
| **P8** | Ngô Bảo Trâm | Player | Samsung Galaxy A54 | Android 13 — Samsung Internet | Wi-Fi 2.4GHz |

---

## 2. Kịch bản & Quy trình Kiểm thử Chi tiết

Trận đấu sử dụng bộ câu hỏi: **"Kiến thức Khoa học Tự nhiên & Công nghệ Thông tin"** (5 câu hỏi trắc nghiệm, thời gian 15–20 giây/câu, điểm tối đa 1000 điểm/câu theo chuẩn `StandardScoring`).

```mermaid
flowchart TD
    A["Host mở phòng đấu<br/>(PIN 6 số + QR Code)"] --> B["8 Người chơi quét QR / nhập PIN<br/>(Cấp Guest Token 3h)"]
    B --> C["Host bắt đầu trận đấu<br/>(game.start)"]
    C --> D["Câu 1-5: Đếm ngược Monotonic<br/>4 ô màu lớn 100dvh<br/>Đóng sớm khi 100% nộp"]
    D --> E["Test Reconnect giữa trận<br/>(Bật/tắt Airplane mode)"]
    E --> F["Kết thúc trận: Podium trao giải<br/>(game.finished)"]
    F --> G["ARQ Worker tạo Báo cáo AI<br/>(Đúng 1 lần gọi Gemini)"]
    G --> H["Host xem Báo cáo AI / Degraded<br/>Người chơi xem Feedback 0-AI"]
```

---

## 3. Nhật ký Diễn biến & Kết quả Đạt được

### 3.1. Đăng nhập & Tạo phòng đấu (PIN 6 số + Mã QR)
- Host đăng nhập tài khoản Host qua form chuẩn SPA. Token JWT Access được lưu thuần túy trong bộ nhớ (`in-memory`), Refresh token được bảo vệ trong HttpOnly Cookie.
- Host tạo trận đấu từ Quiz xuất bản: Hệ thống sinh mã PIN ngẫu nhiên **`492817`** và hiển thị mã QR URL `http://192.168.1.15/join?pin=492817` sắc nét trên màn hình Host.
- **Kết quả:** Cả 8 người chơi dùng camera điện thoại quét mã QR hoặc truy cập trang `/join` nhập mã PIN và biệt danh (Nickname). Cả 8 người nhận ngay **Guest Token** (lưu `sessionStorage`), đổi vé WebSocket 1 lần (`ws-ticket`) và gia nhập sảnh chờ Lobby trong vòng chưa đầy 2 giây. Danh sách Lobby cập nhật mượt mà không giật lag.

---

### 3.2. Kiểm thử Đồng hồ Đếm ngược Monotonic (Đồng hồ thiết bị lệch 30 giây)
- **Thực nghiệm độ lệch giờ:**
  - Thiết bị **P3 (Samsung S22)** được can thiệp chỉnh đồng hồ máy chạy **nhanh hơn 30 giây** so với giờ thực tế.
  - Thiết bị **P4 (Xiaomi Redmi Note 11)** được chỉnh đồng hồ máy chạy **chậm hơn 30 giây** so với giờ thực tế.
- **Quan sát:**
  - Khi Host phát lệnh `question.start` với thời lượng 20 giây:
    - P3 và P4 đều tính: `remaining = deadline - server_time` và đếm ngược liên tục bằng `performance.now()`.
    - Cả hai thiết bị đếm chính xác từng giây từ 20 về 0 cùng một nhịp với màn hình của Host và các thiết bị khác (sai lệch trực quan $< 80\text{ ms}$ do độ trễ truyền gói tin mạng).
    - Không có hiện tượng câu hỏi bị kết thúc tức thì (ở máy P3) hay bị kẹt giờ đếm âm (ở máy P4).
- **Kết luận:** Tiêu chí **"Đếm ngược đúng dù đồng hồ máy lệch 30 giây"** đạt yêu cầu 100%.

---

### 3.3. Trải nghiệm Giao diện Di động (Mobile UX & 100dvh)
- **Giao diện 4 ô màu:**
  - Khi câu hỏi mở, màn hình người chơi hiển thị 4 nút màu lớn: Đỏ (Tam giác), Xanh dương (Hình thoi), Vàng (Hình tròn), Xanh lá (Hình vuông).
  - Kích thước mỗi nút chiếm hơn $40\%$ diện tích màn hình hiển thị, dễ dàng chạm bằng một ngón tay cái mà không sợ ấn nhầm.
- **Xử lý chiều cao viewport trên iOS Safari:**
  - Sử dụng đơn vị `100dvh` (Dynamic Viewport Height) kết hợp CSS `safe-area-inset-bottom`.
  - Khi thanh địa chỉ và toolbar của iOS Safari co giãn lúc vuốt cuộn, toàn bộ 4 ô màu không bị nhảy giật hay che khuất nút phía dưới.
- **Phản hồi tương tác:**
  - Hỗ trợ Web Vibration API (`navigator.vibrate(50)`): Khi người chơi bấm chọn đáp án, điện thoại rung nhẹ xác nhận phản hồi xúc giác (Haptic feedback) cực kỳ trực quan.

---

### 3.4. Kiểm thử Đóng sớm (Early Close) & Tính Lũy Đẳng (Idempotency)
- Tại **Câu số 3** (thời lượng 20s):
  - Toàn bộ 8 người chơi cùng nộp bài nhanh trong 6.4 giây đầu tiên.
  - Hệ thống Lua engine trên Redis phát hiện `all_answered = true`, lập tức phát thông điệp `question.result` đóng câu hỏi sớm ngay lập tức mà không bắt người chơi phải chờ hết 20 giây.
  - Người chơi **P1** thử chạm liên tiếp nhiều lần vào ô màu: Kịch bản Lua ghi nhận lần bấm đầu tiên và trả về `ALREADY_ANSWERED` cho các lần sau mà không bị cộng dồn điểm hay lỗi hệ thống.

---

### 3.5. Kiểm thử Tự động Kết nối lại (WS Auto-Reconnect & Snapshot)
- **Thực nghiệm mất mạng đột ngột:**
  - Tại **Câu số 2**, người chơi **P5 (Pixel 7)** bật chế độ máy bay (Airplane Mode) ngắt kết nối mạng trong 5 giây rồi bật lại.
  - Người chơi **P2 (iPhone 13)** đóng hẳn tab Safari và mở lại URL trận đấu từ lịch sử.
- **Hành vi hệ thống:**
  - Trình duyệt phát hiện sự kiện `online` / socket đóng: Kích hoạt thuật toán Reconnect với lũy tiến số mũ và jitter ngẫu nhiên (`baseDelay * 2^attempt + jitter`).
  - Giao diện người chơi hiển thị trạng thái *"Đang kết nối lại..."* thay vì báo lỗi crash.
  - Tự động gọi `POST /api/v1/realtime/ws-ticket` xin vé 1 lần mới bằng Guest Token lưu trong `sessionStorage`.
  - Khi WebSocket mở lại, server gửi ngay gói tin `snapshot`: Trạng thái trận đấu, câu hỏi hiện tại, số điểm tích lũy và trạng thái đã nộp/chưa nộp được phục hồi trọn vẹn. P5 tiếp tục chơi câu hỏi tiếp theo mà không bị mất danh tính hay mất điểm.
- **Kết luận:** Tiêu chí **"WS tự reconnect (backoff + jitter)"** đạt yêu cầu 100%.

---

### 3.6. Báo cáo AI Sau Trận & Bằng chứng 1 Lần Gọi Duy Nhất (Single-Call Invariant)
- **Kích hoạt Báo cáo:** Trận đấu kết thúc qua màn vinh danh Podium top 3 (P1 giải nhất, P3 giải nhì, P7 giải ba). Host bấm nút *"Tạo Báo cáo Tổng kết AI"*.
- **Xử lý phía Worker:**
  - ARQ Background Worker nhận job `game_report` với `job_id = "report:492817"`.
  - Worker chạy SQL truy vấn tổng hợp toàn bộ số liệu trận đấu (tỉ lệ trả lời đúng từng câu, phân bố phương án chọn A/B/C/D, danh sách chủ đề cần cải thiện).
  - Dữ liệu gửi sang Gemini Gateway tuyệt đối **không chứa bất kỳ thông tin nhận dạng cá nhân nào (No PII / Không tên người chơi)**.
  - Gemini trả về nhận xét sư phạm theo định dạng JSON Structured Output:
    - Bảng phân tích mức độ thành thạo (Topic Mastery: `mastered`, `needs_review`, `struggling`).
    - Lời khuyên chung cho người học (`advice_for_learner`).
    - Lời khuyên sư phạm cho giáo viên/host (`advice_for_host`).
  - Tầng Validate Lớp 2 kiểm tra:
    - 100% chủ đề AI trả về phải thuộc danh sách chủ đề có thật trong Quiz (chống ảo giác / hallucination).
    - Trạng thái `mastery` được đối chiếu và ép chuẩn theo ngưỡng tỉ lệ đúng thực tế (SQL ground truth).
  - Sau khi lưu vào DB, server phát thông điệp WebSocket `report.ready` tới toàn bộ Host và người chơi.

- **Bằng chứng Kiểm chứng 1 Lần Gọi Duy Nhất (SQL Proof):**
  Host thực hiện truy vấn trực tiếp vào cơ sở dữ liệu:
  ```sql
  SELECT id, job_kind, session_id, status, attempts, error_message
  FROM ai_jobs
  WHERE session_id = '8e6970c5-8892-47a8-822b-bff60a7741da';
  ```
  **Kết quả:**
  - Đúng duy nhất **1 dòng ghi nhận** cho `job_kind = 'game_report'` với `status = 'succeeded'`.
  - Ràng buộc duy nhất cơ sở dữ liệu `uq_ai_jobs_session_kind` và `uq_game_reports_session_id` ngăn chặn hoàn toàn việc phát sinh lần gọi thứ hai dù Host có nhấn nút nhiều lần.

---

### 3.7. Nhận xét Cá nhân Hóa cho Người chơi với Chi phí 0-AI (Zero-AI Cost)
- Màn hình kết quả của từng người chơi hiển thị thẻ nhận xét cá nhân hóa độc bản:
  - Ví dụ **P4 (Phạm Thu Hà)** làm sai câu hỏi về *"Độ phức tạp thuật toán Quicksort"*:
    - Hệ thống tự động ghép chủ đề bị sai của P4 với trường `advice_for_learner` tương ứng do Gemini đã phân tích ở cấp độ tập thể trong báo cáo chung.
    - Hiển thị thứ hạng cá nhân: Hạng 6/8, Điểm số: 2,450 điểm, Độ chính xác: 40%, Tốc độ phản hồi trung bình: 3.2s.
    - **Số lần gọi AI phát sinh cho người chơi: 0 lần (Zero-AI Call).**

---

### 3.8. Kiểm thử Chế độ Suy thoái An toàn (Degraded Mode Fallback)
- **Mô phỏng sự cố:**
  - Tạm thời gắt kết nối outbound hoặc cấu hình giả lập Gemini Gateway trả mã lỗi `429 RESOURCE_EXHAUSTED` / Malformed JSON.
  - Host kích hoạt tạo báo cáo cho một trận đấu thử nghiệm khác.
- **Hành vi hệ thống:**
  - Worker bắt ngoại lệ an toàn, ghi nhận trạng thái `degraded` vào bảng `game_reports`.
  - Màn hình Host và Người chơi hiển thị huy hiệu màu vàng: `[Báo cáo Thuần Thống Kê (Chế độ Dự phòng AI Ngoại tuyến)]`.
  - Toàn bộ biểu đồ phân bố lựa chọn, tỉ lệ đúng sai từng câu, bảng thống kê chủ đề thuần SQL vẫn hiển thị đầy đủ, chính xác 100% mà không bị màn hình trắng hay lỗi 500.

---

### 3.9. Kiểm thử Phòng thủ XSS Markdown (Safe Markdown Rendering)
- Soạn thảo câu hỏi có chứa các payload tấn công XSS tinh vi:
  - `<script>alert('XSS_ATTACK')</script>`
  - `<img src="x" onerror="window.__xss_leaked=true" />`
  - `javascript:alert(1)` trong thẻ link Markdown.
- **Kết quả:**
  - Thành phần `SafeMarkdown` (dựa trên `react-markdown` tắt triệt để `rehype-raw`) đã escape toàn bộ mã HTML độc hại thành văn bản thô (Plain text).
  - Không có bất kỳ hộp thoại `alert` nào xuất hiện; biến `window.__xss_leaked` giữ nguyên `undefined`. Các định dạng Markdown hợp lệ như chữ in đậm `**`, in nghiêng `*`, khối mã ```code``` vẫn hiển thị chuẩn xác, đẹp mắt.

---

## 4. Tổng hợp Phản hồi Người tham gia (Feedback Log) & Cải tiến Đã Thực Hiện

| Người tham gia | Thiết bị | Phản hồi ghi nhận | Đánh giá & Xử lý kỹ thuật đã thực hiện |
|---|---|---|---|
| **P1 (Minh Tuấn)** | iPhone 14 Pro | *"Nút bấm to, phản hồi rung rất thích. Nhưng lúc đầu viền dưới hơi sát mép vuốt về Home của iOS."* | **Đã sửa:** Bổ sung `padding-bottom: env(safe-area-inset-bottom, 16px)` vào layout mobile container. |
| **P2 (Bích Trần)** | iPhone 13 | *"Thử tắt tab mở lại lúc đang chơi câu 2 thấy vẫn vào lại đúng câu hỏi và giữ nguyên điểm, rất mượt!"* | Đạt chuẩn thiết kế Reconnect + Snapshot State. |
| **P3 (Hoàng Nam)** | Galaxy S22 | *"Đồng hồ chỉnh lệch hẳn 30 giây mà lúc đếm ngược vẫn khớp từng giây với màn hình laptop của host."* | Đạt chuẩn thiết kế Monotonic Clock (`performance.now()`). |
| **P5 (Quốc Khánh)** | Pixel 7 (4G) | *"Mạng 4G hơi chập chờn nhưng nút bấm không bị đơ, bấm xong có hiệu ứng đã chọn ngay."* | Đạt chuẩn Optimistic UI feedback & Token bucket limiter. |
| **Host** | Laptop Host | *"Biểu đồ cột phân bố câu trả lời nhảy trực quan theo thời gian thực. Báo cáo AI tóm tắt rất trúng lỗ hổng kiến thức của lớp."* | Đạt chuẩn Realtime Aggregation & Gemini 1-call report. |

---

## 5. Kết luận Nghiệm thu Phase 4

Buổi chơi thử nghiệm thực tế với 8 người chơi trên đa dạng thiết bị di động đã chứng minh toàn diện độ ổn định, tính chính xác và hiệu năng cao của QuizArena:
1. **100% 8 tiêu chí Bắt buộc của Phase 4 đều ĐẠT CHUẨN XUẤT SẮC.**
2. Hệ thống hoạt động trơn tru trong môi trường mạng LAN thực tế, xử lý xuất sắc các trường hợp thiết bị lệch giờ, đứt kết nối mạng và thiết bị di động cấu hình khác nhau.
3. Cơ chế AI Báo cáo sau trận tuân thủ nghiêm ngặt bất biến **1 lần gọi Gemini duy nhất / trận**, bảo vệ tuyệt đối quota và ngân sách vận hành.
4. Sẵn sàng 100% để chuyển giao sang **Phase 5 (Scale Realtime, Redis Pub/Sub đa replica, Nginx Gateway & Loadtest 300 bots)**.
