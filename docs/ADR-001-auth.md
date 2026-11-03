# ADR-001: Kiến trúc Xác thực và Phân quyền (JWT, Refresh Rotation, Guest Token, WebSocket Auth)

- **Trạng thái:** Accepted
- **Ngày:** 03/11/2026
- **Tác giả:** Đội ngũ QuizArena
- **Tham chiếu:** Đề bài §3.1, §3.2, §6.2, §6.3; SRS v0.2 (FR-AUTH, FR-AUTHZ).

---

## 1. Bối cảnh (Context)

QuizArena là nền tảng thi đố realtime yêu cầu hệ thống xác thực và phân quyền đáp ứng:
1. **Phân quyền 4 vai trò:** `GUEST` (khách vào chơi bằng PIN), `PLAYER` (người chơi có tài khoản), `HOST` (giảng viên/trainer tạo và điều khiển trận), `ADMIN` (quản trị viên).
2. **Trải nghiệm người dùng:** Người dùng có tài khoản giữ đăng nhập 7 ngày; người chơi vãng lai vào phòng thi bằng điện thoại nhanh chóng mà **không cần tạo tài khoản**.
3. **Hiệu năng & Khả năng mở rộng:** REST API đạt độ trễ p95 < 200 ms dưới tải cao; WebSocket kết nối đồng thời từ hàng trăm thiết bị cùng lúc.
4. **Bảo mật:** Chống đánh cắp token, chống brute-force mật khẩu, chống gian lận trong phòng thi, và vô hiệu hóa tức thì khi đăng xuất / đổi mật khẩu.

---

## 2. Quyết định kiến trúc (Decisions)

### 2.1. Cấu trúc Token hai lớp (Access Token + Refresh Token)
- **Access Token (JWT HS256):**
  - Thời gian sống ngắn: **15 phút**.
  - Payload: `sub` (user_id), `roles`, `permissions`, `ver` (token_version), `jti` (unique token ID), `exp`, `iat`, `type="access"`.
  - Lưu trữ phía client: **Chỉ lưu trong bộ nhớ (memory)** của Frontend (Single Page App), không lưu vào `localStorage` / `sessionStorage` để triệt tiêu nguy cơ bị đánh cắp qua XSS.
- **Refresh Token (Opaque Token ngẫu nhiên):**
  - Thời gian sống dài: **7 ngày**.
  - Tạo bằng chuỗi ngẫu nhiên entropy cao (`secrets.token_urlsafe(48)`).
  - Vận chuyển qua Cookie an toàn: `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`. JavaScript không thể đọc cookie này (chống XSS) và cookie không gửi chéo trang (chống CSRF).
  - Lưu trữ trong Database: **Chỉ lưu bản băm SHA-256 (`token_hash`)**, không bao giờ lưu token gốc. Mỗi chuỗi refresh token gắn với một `family_id`.

### 2.2. Refresh Token Rotation & Reuse Detection (AU-4, AU-5)
- **Rotation:** Mỗi lần gọi `POST /auth/refresh`, server thu hồi refresh token hiện tại và cấp một cặp Access Token + Refresh Token mới thuộc cùng `family_id`.
- **Reuse Detection:** Nếu một token đã bị thu hồi/xoay vòng mà được gửi lại (dấu hiệu token bị rò rỉ hoặc bị kẻ tấn công dùng lại), server lập tức **thu hồi toàn bộ các token thuộc cùng `family_id`** và từ chối với mã 401 `REFRESH_TOKEN_REUSED`.

### 2.3. Vô hiệu hóa Token tức thì (AU-6, AU-7)
- **Logout đơn lẻ (`POST /auth/logout`):**
  - Thu hồi refresh token trong DB.
  - Đưa `jti` của access token vào **denylist trên Redis** với TTL bằng thời gian sống còn lại của token (`SET deny:jti:{jti} 1 EX {ttl}`).
- **Logout tất cả thiết bị (`POST /auth/logout-all`) & Đổi mật khẩu (`POST /auth/change-password`):**
  - Thu hồi mọi refresh token của người dùng trong DB (`revoke_all_for_user`).
  - Tăng `token_version` trong bảng `users` (`token_version = token_version + 1`). Mọi access token cũ có `claims["ver"] < user.token_version` lập tức bị từ chối 401 tại dependency `get_current_user`.

### 2.4. Chống Brute-force Đăng nhập (AU-8)
- Sử dụng Redis key: `login_fail:{email}:{client_ip}` với TTL 15 phút (900 giây).
- Khi sai 5 lần liên tiếp: khóa tạm thời và trả về **429 `RATE_LIMITED`**.
- Thông điệp phản hồi và hành vi khóa giữa "email không tồn tại" và "sai mật khẩu" là **hoàn toàn giống nhau**, ngăn chặn kẻ tấn công dò tìm tài khoản.

### 2.5. Guest Token cho người chơi không cần tài khoản (AU-9, AU-10)
- Gọi `POST /api/v1/games/join` với `{pin, nickname}`.
- Trả về JWT `type="guest"`, `sub="guest:<uuid>"`, `game_id="<uuid>"`, `permissions=["game:play"]`.
- Thời gian sống: tối đa **3 giờ** (bằng thời gian mở tối đa của một trận đấu).

### 2.6. Xác thực WebSocket bằng Vé dùng 1 lần (AU-11)
- Do trình duyệt không cho phép gửi header `Authorization: Bearer` tùy biến khi khởi tạo kết nối WebSocket `new WebSocket(...)`, hệ thống áp dụng cơ chế **Ticket-based auth**:
  1. Client gửi request REST có header JWT: `POST /api/v1/realtime/ws-ticket` kèm `game_id`.
  2. Server kiểm tra quyền, tạo ticket ngẫu nhiên (`secrets.token_urlsafe(32)`), lưu định danh vào Redis `ws:ticket:{ticket}` với **TTL = 30 giây**.
  3. Client mở kết nối: `wss://.../ws/game?ticket=...`.
  4. Server WebSocket dùng lệnh nguyên tử **`GETDEL`** để lấy định danh và xóa vé ngay lập tức. Vé không thể dùng lần 2.

---

## 3. Trả lời các câu hỏi bắt buộc (§3.2)

### Câu hỏi 1: Permissions nằm trong JWT → admin thu hồi quyền HOST thì bao lâu sau có hiệu lực, chấp nhận được không?

**Trả lời:**
- **Thời gian có hiệu lực:** Tối đa **15 phút** (thời gian sống của Access Token hiện tại). Khi access token hết hạn, client buộc phải gọi `POST /auth/refresh`; lúc này server đọc permissions mới nhất từ Database và cấp token mới không còn quyền `HOST`.
- **Mức độ chấp nhận được:**
  - **Hoàn toàn chấp nhận được** trong nghiệp vụ thông thường của QuizArena. Việc hạ vai trò từ HOST xuống PLAYER là thao tác quản trị nhân sự/giảng viên, không phải là sự cố an ninh cấp bách đòi hỏi ngắt kết nối theo từng mili-giây.
  - Việc đưa `permissions` vào JWT cho phép kiểm tra quyền (RBAC) dạng Stateless ngay tại bộ nhớ mà **không cần query Database/Redis ở mỗi request**, giúp REST API đạt p95 < 200 ms (NFR-4) và giải phóng tài nguyên database.
- **Biện pháp can thiệp khẩn cấp (Emergency path):** Nếu admin phát hiện tài khoản HOST có hành vi phá hoại hoặc bị xâm nhập:
  - Admin thực hiện khóa tài khoản (`is_active = false`) hoặc tăng `token_version` (`POST /admin/users/{id}/revoke-sessions`).
  - Dependency `get_current_user` kiểm tra `is_active` và `token_version` trên từng request có thao tác nhạy cảm, vô hiệu hóa tài khoản ngay lập tức mà không cần chờ hết hạn 15 phút.

---

### Câu hỏi 2: Vì sao Guest Token KHÔNG có Refresh Token?

**Trả lời:**
1. **Bản chất nghiệp vụ ngắn hạn:** Người chơi vãng lai (GUEST) quét mã QR hoặc nhập PIN chỉ để chơi duy nhất một trận thi đấu diễn ra trong khoảng 15–45 phút. Thời gian sống 3 giờ của Guest Token đã thừa đủ cho toàn bộ vòng đời trận đấu (từ lúc mở phòng chờ đến khi kết thúc và xem bục vinh danh).
2. **Không có danh tính bền vững:** Guest không có bản ghi trong bảng `users`, không có mật khẩu hay thông tin liên hệ. Do đó, việc duy trì phiên đăng nhập 7 ngày bằng refresh token là phi lý về mặt nghiệp vụ.
3. **Tiết kiệm tài nguyên & Tối ưu hiệu năng:** Nếu mỗi guest đều sinh refresh token và lưu vào bảng `refresh_tokens`, một hội trường 500 người chơi sẽ tạo ra 500 bản ghi rác và kích hoạt các truy vấn xoay vòng token không cần thiết, làm phình to database và chậm quá trình dọn dẹp.
4. **Giảm thiểu bề mặt tấn công:** Không có refresh token đồng nghĩa không có rủi ro bị rò rỉ cookie kéo dài ngày, không phải quản lý cơ chế refresh rotation cho khách vãng lai.

---

### Câu hỏi 3: Guest Token bị lộ thì thiệt hại tối đa là gì?

**Trả lời:**
Thiệt hại tối đa khi một Guest Token bị lộ là **cực kỳ thấp và hoàn toàn được cô lập**:
1. **Phạm vi quyền hạn bị khóa chặt (Minimal Privilege):**
   - Guest token chỉ có duy nhất quyền `game:play`, không có bất kỳ quyền nào khác trong 9 quyền còn lại (`quiz:write_own`, `quiz:ai_generate`, `history:read_own`, `users:manage`...).
   - Nếu kẻ tấn công dùng guest token để gọi bất kỳ REST API nào ngoài trận đấu, hệ thống lập tức trả về **403 FORBIDDEN** (đã được chứng minh bằng test tự động `test_guest_token_goi_api_khac_bi_403`).
2. **Phạm vi trận đấu bị cô lập (Game Scoping):**
   - Guest token gắn chặt với đúng một `game_id` (`claims["game_id"]`).
   - Kẻ tấn công không thể dùng token này để lấy ticket hoặc tham gia vào bất kỳ trận đấu nào khác (đã chứng minh bằng `test_guest_token_sai_tran_bi_tu_choi_khi_lay_ticket`).
3. **Trong phạm vi trận đấu đang diễn ra:**
   - Kẻ tấn công chỉ có thể gửi đáp án thay cho người chơi mang token đó. Tuy nhiên, hệ thống đã phòng thủ nhiều lớp:
     - **Không lộ đáp án:** Server không bao giờ gửi đáp án trước khi kết thúc câu hỏi (GM-3). Kẻ tấn công có token cũng không thể biết đáp án đúng.
     - **Tính nguyên tử & Idempotency:** Mỗi câu hỏi chỉ nhận duy nhất 1 đáp án đầu tiên (GM-5).
     - **Rate limiting WebSocket:** Giới hạn 5 tin nhắn/giây (GM-13).
     - **Quyền Host:** Host có thể kick nickname nghi vấn ra khỏi trận bất cứ lúc nào (GM-2).
4. **Thời hạn tự hủy:** Token tự động hết hạn sau tối đa 3 giờ hoặc ngay khi host kết thúc trận đấu.

---

## 4. Hệ quả (Consequences)

- **Điểm tích cực:**
  - Đáp ứng trọn vẹn các yêu cầu bảo mật AU-1 đến AU-11 và phân quyền RBAC §3.2.
  - Zero-database-lookup cho các endpoint chỉ cần kiểm tra quyền JWT.
  - Bảo vệ tuyệt đối token trước nguy cơ XSS, CSRF, Token Replay, và Brute-force.
- **Thỏa hiệp (Trade-off):**
  - Cần duy trì kết nối Redis cho việc lưu trữ denylist `jti`, bộ đếm brute-force, và vé `ws-ticket`. (Redis đã được cấu hình cluster/container độc lập với độ trễ sub-millisecond).
