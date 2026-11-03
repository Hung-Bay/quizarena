# TIẾN ĐỘ DỰ ÁN — QUIZARENA

> Nhật ký theo dõi tiến độ từng tuần và từng phase của dự án QuizArena.
> Cập nhật vào thứ Sáu hàng tuần sau buổi nghiệm thu và đánh giá tiêu chí.

---

## Tổng quan tiến độ theo Phase

| Phase | Nội dung chính | Trọng tâm | Trạng thái | Hoàn thành |
|---|---|---|:---:|:---:|
| **P0** | Thiết kế & Khung hạ tầng | Docker compose, CI/CD, Nginx, Healthz, WS Echo, SRS v0.1 | ✅ Hoàn thành | Tuần 1 |
| **P1** | Auth, RBAC, Guest Token | JWT rotation, Reuse detection, RBAC 4x10, Guest token, WS ticket, ADR-001 | ✅ Hoàn thành | Tuần 2 |
| **P2** | Quiz Authoring + AI Sinh Quiz | CRUD quiz, PDF upload, Gemini Gateway, Quota rate limiter, SSE | ⏳ Sắp tới | Tuần 3 |
| **P3** | Live Game Engine | State machine, Redis Lua atomic, Leaderboard ZSET, Game clock, Write-behind | ⏳ Chưa bắt đầu | Tuần 4 |
| **P4** | Frontend + AI Báo cáo sau trận | React FE, 1 call Gemini/game, Báo cáo cá nhân, Degraded mode | ⏳ Chưa bắt đầu | Tuần 5 |
| **P5** | Scale Realtime & Nginx Gateway | Scale replica, Redis Pub/Sub, Loadtest 300 bots, Anti-cheat | ⏳ Chưa bắt đầu | Tuần 6 |
| **P6** | Cloud, Domain, HTTPS, CI/CD | Oracle Cloud ARM, Let's Encrypt, Deploy zero-downtime | ⏳ Chưa bắt đầu | Tuần 7 |
| **P7** | Observability, Autoscale, Demo | Grafana, Loki, Autoscale, Backup restore, SRS v1.0, Báo cáo | ⏳ Chưa bắt đầu | Tuần 8 |

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

- **Test Suite:** 97 passed, 39 skipped (các test DB khi chạy local không có Postgres instance).
- **Static Type Analysis (Mypy):** `Success: no issues found in 47 source files` (chế độ strict).
- **Linter & Formatter (Ruff):** 100% tuân thủ quy chuẩn PEP 8 và định dạng tự động.
- **Pre-commit Hooks:** Tất cả 8 hook kiểm tra (trailing whitespace, EOF, yaml, secret detection, private key) đều Passed.
