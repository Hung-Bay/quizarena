# ADR-003: Kiến trúc Game Clock, Hẹn giờ đóng câu hỏi và Bù trừ trễ mạng

- **Trạng thái:** Đã chấp thuận (Accepted)
- **Ngày quyết định:** 2026-11-09
- **Người đề xuất:** Nhóm phát triển QuizArena Core & Live Engine
- **Tham chiếu:** `docs/SRS.md` (FR-GAME-06, 07, 13, 14, 15, 16), `docs/WS_PROTOCOL.md`

---

## 1. Ngữ cảnh và Vấn đề (Context)

QuizArena là nền tảng thi trắc nghiệm trực thới gian thực hỗ trợ tới 500 người chơi đồng thời trong một phòng đấu. Trong mô hình này, việc quản lý thời gian đặt ra các thách thức lớn:
1. **Lệch giờ client (Clock Skew):** Đồng hồ trên máy tính/điện thoại của người chơi có thể sai lệch vài giây đến vài phút so với giờ chuẩn thế giới.
2. **Độ trễ mạng không đồng đều (Network Jitter & Latency):** Gói tin từ client gửi lên server có thể mất từ vài chục tới hàng trăm mili giây.
3. **Host mất kết nối (Host Disconnection):** Người tổ chức (Host) có thể tắt tab, reload trình duyệt hoặc mất mạng trong khi câu hỏi đang diễn ra. Câu hỏi vẫn phải **tự động đóng đúng giờ** để bảo vệ tính công bằng (FR-GAME-15).
4. **Trải nghiệm chờ đợi (Early Close):** Nếu toàn bộ người chơi trong phòng đã nộp bài trước khi hết giờ, việc bắt tất cả chờ hết thời gian đếm ngược sẽ gây nhàm chán.
5. **Race condition khi đóng câu hỏi:** Host bấm "Next" hoặc đóng câu hỏi đúng thời điểm worker hẹn giờ chạy đóng câu hỏi.

---

## 2. Các phương án kiến trúc đã xem xét (Considered Options)

### Phương án 1: Vòng lặp đếm ngược có khóa phân tán (Distributed Lock Polling Loop)
- Mỗi trận chạy một coroutine vòng lặp `while True: sleep(0.5)` với khóa phân tán Redis (Redlock) để kiểm tra `now >= deadline`.
- **Đánh giá:**
  - *Nhược điểm:* Tốn tài nguyên CPU và I/O mạng cho Redis; khó scale khi có hàng trăm trận đấu diễn ra cùng lúc; rủi ro split-brain nếu lock hết hạn giữa chừng.

### Phương án 2: Phụ thuộc vào tín hiệu từ Client của Host
- Client của Host chạy đếm ngược trên giao diện và gửi lệnh `game.close` khi hết giờ.
- **Đánh giá:**
  - *Nhược điểm:* Hoàn toàn vi phạm nguyên tắc bảo mật (tin tưởng client). Nếu Host đóng tab hoặc cố tình can thiệp mã nguồn thì câu hỏi sẽ bị treo vô tận.

### Phương án 3: Hẹn giờ qua ARQ Job (`defer_until = deadline`) kết hợp Lua Script Idempotent và Timer nội bộ (ĐƯỢC CHỌN)
- Khi câu hỏi bắt đầu (`game.start` hoặc `game.next`), server tính toán deadline chính xác theo nguồn giờ Redis TIME, lập lịch một job đóng câu hỏi trên hàng đợi ARQ với `defer_until = deadline` và `job_id = "close_{game_id}_{q_index}"`.
- Tác vụ đóng câu hỏi được thực thi bằng script Lua nguyên tử (`transition.lua`), có tính chất **Idempotent** theo `(trận, số thứ tự câu, trạng thái)`.
- Khi 100% người chơi đã nộp bài (`all_answered = 1` từ `submit_answer.lua`), server kích hoạt đóng sớm ngay lập tức mà không cần chờ job hẹn giờ.

---

## 3. Quyết định kiến trúc (Decision)

Chúng tôi quyết định áp dụng **Phương án 3**, cụ thể:

### 3.1. Đồng hồ duy nhất do Server quyết định (Single Source of Truth)
- Toàn bộ mốc thời gian bắt đầu (`server_time`) và hạn chót (`deadline`) được lấy trực tiếp từ lệnh `redis.call('TIME')` của Redis.
- Trình duyệt/client chỉ nhận `server_time` và `deadline` dạng epoch milliseconds để hiển thị thanh đếm ngược, không tự ra quyết định thời gian.
- **Ân hạn mạng (Grace period):** Để bù đắp độ trễ truyền gói tin, server chấp nhận đáp án nếu thời điểm server nhận được nhỏ hơn hoặc bằng `deadline + 500ms` (FR-GAME-07). Mọi đáp án gửi sau mốc này bị từ chối với lý do `LATE`.

### 3.2. Tính chất Idempotent của việc đóng câu hỏi
- Script `transition.lua` kiểm tra:
  ```lua
  if current_status == 'question_result' then
      if expected_q == 0 or expected_q == current_q_index then
          return {1, 'question_result', tostring(current_q_index), '0', '0', '1'} -- Idempotent hit
      end
  end
  ```
- Nếu job đóng sớm chạy trước: Trạng thái chuyển thành `question_result`.
- Khi job ARQ hẹn giờ chạy đến sau: Lua phát hiện câu đã đóng, trả về thành công mà không ghi đè dữ liệu hay phát thông điệp lặp.

### 3.3. Giảm độ trễ ARQ Worker
- Mặc định, ARQ có `poll_delay = 0.5s`, có thể làm chậm việc kích hoạt job hẹn giờ từ vài trăm mili giây.
- Thiết lập `WorkerSettings.poll_delay = 0.1` (100ms) để job đóng câu hỏi được thực thi gần như tức thì ngay khi chạm mốc deadline.
- Bổ sung cơ chế đếm ngược `asyncio.Task` dự phòng cục bộ trên replica để đảm bảo tính sẵn sàng cao ngay cả khi worker bị trễ.

### 3.4. Chiến lược lưu trữ bền vững (Redis Persistence)
- Redis được cấu hình với cờ `--appendonly yes` và `--appendfsync everysec` trong `docker-compose.yml`.
- **Cái giá phải trả (Cost/Trade-off):** Trong trường hợp xấu nhất Redis bị crash đột ngột, hệ thống chỉ mất tối đa **khoảng 1 giây dữ liệu** chưa kịp sync xuống đĩa. Toàn bộ đáp án đã nộp trong Stream sẽ được worker ghi bền vào PostgreSQL định kỳ qua cơ chế Write-behind.

---

## 4. Hệ quả (Consequences)

### Tích cực:
- Câu hỏi luôn tự động đóng chính xác kể cả khi Host tắt tab hay ngắt mạng (thỏa mãn FR-GAME-15).
- Không có race condition giữa Host và hệ thống tự động nhờ tính chất nguyên tử và idempotent của Lua script.
- Tiết kiệm thời gian chờ đợi cho người chơi khi tất cả đã nộp bài xong.
- Kiến trúc phân tán rõ ràng: WebSocket server nhận kết nối và fan-out, Worker xử lý hẹn giờ và đồng bộ dữ liệu PostgreSQL.

### Hạn chế & Giảm thiểu:
- Nếu toàn bộ cụm Worker bị treo, câu hỏi có thể mở lâu hơn dự kiến. Tuy nhiên, nhờ kiểm tra deadline bằng `TIME` trong `submit_answer.lua`, người chơi nộp sau deadline vẫn bị từ chối với mã `LATE` (GM-4).
