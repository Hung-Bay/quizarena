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
| **P4** | Frontend + AI Báo cáo sau trận | React FE, 1 call Gemini/game, Báo cáo cá nhân, Degraded mode | ⏳ Chưa bắt đầu | Tuần 5 |
| **P5** | Scale Realtime & Nginx Gateway | Scale replica, Redis Pub/Sub, Loadtest 300 bots, Anti-cheat | ⏳ Chưa bắt đầu | Tuần 6 |
| **P6** | Cloud, Domain, HTTPS, CI/CD | Oracle Cloud ARM, Let's Encrypt, Deploy zero-downtime | ⏳ Chưa bắt đầu | Tuần 7 |
| **P7** | Observability, Autoscale, Demo | Grafana, Loki, Autoscale, Backup restore, SRS v1.0, Báo cáo | ⏳ Chưa bắt đầu | Tuần 8 |

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

- **Test Suite:** 249 passed, 39 skipped, 0 failed (288 tests tổng cộng).
- **Static Type Analysis (Mypy):** `Success: no issues found in 82 source files` (chế độ strict).
- **Linter & Formatter (Ruff):** 100% tuân thủ quy chuẩn PEP 8 và định dạng tự động.
- **Pre-commit Hooks:** Tất cả 8 hook kiểm tra đều Passed.
