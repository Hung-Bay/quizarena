# WS_PROTOCOL — Giao thức WebSocket của QuizArena

**Phiên bản giao thức:** `v = 1` · **Trạng thái tài liệu:** CHÍNH THỨC (Phase 3 hoàn thiện) · **Tham chiếu:** `docs/SRS.md` (FR-AUTH-11, FR-GAME-03..22), `docs/ARCHITECTURE.md`, `docs/ADR-003-game-clock.md`


## 1. Kết nối và xác thực

Trình duyệt không gửi được header `Authorization` khi mở kết nối WebSocket, vì vậy giao thức sử dụng vé dùng một lần (AU-11):

```
1. Client  → POST /api/v1/realtime/ws-ticket   (kèm Access Token hoặc Guest Token)
2. Server  ← { "ticket": "<chuỗi ngẫu nhiên>", "expires_in": 30 }  (lưu Redis, TTL 30 giây)
3. Client  → Mở wss://<domain>/ws/game?ticket=<ticket>
4. Server  Đổi vé lấy danh tính bằng lệnh nguyên tử GETDEL (xóa ngay lập tức), sau đó chấp nhận hoặc đóng kết nối
```

- Vé dùng lần thứ hai hoặc quá 30 giây bị từ chối ngay lập tức với mã đóng `4001` (`TICKET_INVALID`).
- Quyền và vai trò của kết nối (Host hay Player) được xác định từ danh tính trong vé, **không** lấy từ nội dung thông điệp client gửi.
- Quyền cần có để tạo vé: `game:play` (người chơi) hoặc `game:host_own` (host của đúng trận đó).
- Ngay sau khi bắt tay thành công, Server gửi ngay một thông điệp `snapshot` chứa trạng thái hiện tại (FR-GAME-17).

### Mã đóng kết nối chuẩn

| Mã | Tên | Khi nào |
|---|---|---|
| `4001` | `TICKET_INVALID` | Vé không tồn tại, hết hạn (> 30s) hoặc đã bị đổi trước đó |
| `4002` | `KICKED` | Người chơi bị Host loại (FR-GAME-05, cấm tham gia lại trong 10 phút) |
| `4003` | `GAME_ENDED` | Trận đấu không tồn tại, đã kết thúc (`finished`) hoặc hết hạn |
| `4004` | `PROTOCOL_ERROR` | Vi phạm giao thức nghiêm trọng |
| `1000` | `NORMAL_CLOSURE` | Đóng kết nối thông thường hoặc kết nối lại từ cùng thiết bị |


## 2. Khung thông điệp (Envelope)

Mọi thông điệp gửi nhận hai chiều đều tuân thủ cấu trúc JSON Envelope chuẩn:

```json
{
  "type": "answer.submit",
  "v": 1,
  "data": { }
}
```

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|:---:|---|
| `type` | string | Có | Tên định danh của loại thông điệp (xem mục 3 và 4) |
| `v` | integer | Có | Phiên bản giao thức (hiện tại `v = 1`). Server từ chối `v != 1` bằng thông điệp `error` mã `UNSUPPORTED_VERSION` |
| `data` | object | Có | Dữ liệu chi tiết của thông điệp |

Thông điệp có `type` ngoài danh mục, sai kiểu JSON, hoặc thiếu `v` sẽ bị Server từ chối bằng `error` mã `BAD_MESSAGE`, **tuyệt đối không làm thay đổi trạng thái trận đấu** (FR-GAME-22).


## 3. Danh mục thông điệp Server → Client

| `type` | Đối tượng nhận | Khi nào | Nội dung chính |
|---|---|---|---|
| `lobby.update` | Host, Player | Người chơi vào/ra/bị kick ở phòng chờ | `game_id`, `pin`, `player_count`, `new_player`, `kicked_player_id` |
| `game.starting` | Host, Player | Host bấm bắt đầu trận đấu | `countdown_s` (đếm ngược 3 giây), `server_time` |
| `question.start` | Host, Player | Bắt đầu một câu hỏi | `q_index`, `total_questions`, `question_id`, `content`, `type`, `points`, `time_limit_s`, `server_time`, `deadline`, `options`. **Tuyệt đối không chứa `is_correct` (FR-GAME-08)** |
| `answer.ack` | Player nộp bài | Ngay sau khi gọi Lua `submit_answer` | `accepted` (bool), `q_index`, `reason` (`LATE`, `ALREADY_ANSWERED`, `NOT_ACTIVE`). **Không lộ đáp án đúng hay điểm số (FR-GAME-07)** |
| `question.result` | Host, Player | Hết giờ làm bài hoặc 100% người chơi đã nộp | `q_index`, `correct_option_ids`, `distribution`, `explanation`, `podium` (Top 5). Đối với từng Player: kèm phần cá nhân hóa `personal` (`score`, `added_score`, `streak`, `rank`, `is_correct`) |
| `game.finished` | Host, Player | Trận đấu kết thúc hoàn toàn | `game_id`, `podium` (Top 3 chung cuộc), `total_players`. Kèm thông tin cá nhân `personal` (`score`, `rank`) |
| `report.ready` | Host, Player | Báo cáo AI hoặc thống kê lớp học đã sinh xong | `game_id`, `report_id`, `status` (`ready` \| `degraded`), `has_ai_insights` (bool). Client tự động fetch `GET /api/v1/games/{id}/report` hoặc chuyển hướng sang trang báo cáo |
| `player.kicked` | Player bị kick | Host kick người chơi | `player_id`, `reason` |
| `snapshot` | Client kết nối / kết nối lại | Ngay sau khi mở WS thành công | Trạng thái toàn diện: `status`, `q_index`, `total_questions`, `current_question` (nếu đang active, không lộ đáp án), `player_score`, `player_streak`, `player_rank`, `is_host` (FR-GAME-17) |
| `error` | Client gửi sai lệnh | Lỗi phân quyền, sai cú pháp, rate limit | `code` (`FORBIDDEN`, `RATE_LIMITED`, `BAD_MESSAGE`, v.v.), `message` |


## 4. Danh mục thông điệp Client → Server

| `type` | Ai gửi | Mô tả | Xử lý & Ràng buộc nghiệp vụ |
|---|---|---|---|
| `heartbeat` | Player, Host | Cập nhật presence định kỳ mỗi 15s (FR-GAME-18) | Gia hạn khóa `game:{id}:presence:{client_id}` TTL 45s trên Redis. Quá 45s không có heartbeat bị coi là offline |
| `snapshot.request` | Player, Host | Yêu cầu nhận lại snapshot trạng thái | Trả về snapshot mới nhất không lộ đáp án |
| `game.start` | Host | Bắt đầu trận đấu từ LOBBY (FR-GAME-06, 14) | Chuyển trạng thái sang `question_active` câu 1 qua Lua script atomic |
| `game.next` | Host | Chuyển sang câu hỏi tiếp theo (FR-GAME-14) | Kiểm tra chống bấm đúp (`expected_q_index`), mở câu hỏi mới qua Lua script atomic |
| `game.end` | Host | Kết thúc trận đấu sớm (FR-GAME-14) | Chuyển trạng thái sang `finished`, tổng kết điểm và phát `game.finished` |
| `player.kick` | Host | Loại người chơi khỏi phòng thi (FR-GAME-05) | Khóa cấm tham gia 10 phút trên Redis (`ex=600`), ngắt kết nối WS của player, phát `lobby.update` |
| `answer.submit` | Player | Nộp bài cho câu hỏi hiện tại (FR-GAME-07, 09, 13) | Chấm điểm atomic qua Lua script: tính điểm theo chiến lược (`standard`/`binary`), bảo vệ lũy đẳng (chỉ ghi nhận lần đầu), kích hoạt đóng sớm nếu 100% người chơi đã nộp bài |

> [!IMPORTANT]
> **Phân quyền nghiêm ngặt:** Toàn bộ lệnh của Host (`game.start`, `game.next`, `game.end`, `player.kick`) chỉ chấp nhận từ kết nối mang cờ `is_host = True`. Người chơi gửi lệnh Host sẽ bị từ chối bằng `error` mã `FORBIDDEN` (FR-GAME-14). Host không được nộp bài (`answer.submit`).


## 5. Quy tắc vận hành hệ thống

1. **Đồng hồ chuẩn Server (Server-Authoritative Clock):**
   - Mọi mốc thời gian đều căn cứ vào nguồn giờ nguyên tử `redis.time()` (epoch ms).
   - Client sử dụng `server_time` và `deadline` trong `question.start` để hiển thị đồng hồ đếm ngược.
   - Đáp án gửi đến server sau `deadline + 500 ms` bị từ chối với lý do `reason="LATE"` (FR-GAME-06, 07).
2. **Tuyệt đối không lộ đáp án (FR-GAME-08):**
   - Thông điệp `question.start` và `snapshot` tuyệt đối không bao giờ chứa trường `is_correct` hay đáp án đúng.
   - `answer.ack` chỉ xác nhận đã ghi nhận nộp bài (`accepted=true`), không bao giờ thông báo đúng/sai.
3. **Tính lũy đẳng (Idempotency - FR-GAME-09):**
   - Người chơi nộp nhiều lần cho một câu hỏi chỉ được tính điểm và cập nhật bảng xếp hạng ở lần đầu tiên. Các lần gửi tiếp theo trả `accepted=false`, `reason="ALREADY_ANSWERED"`.
4. **Giới hạn tốc độ kết nối (Rate Limiting - FR-GAME-21):**
   - Mỗi kết nối WebSocket bị giới hạn tối đa 5 thông điệp / giây (thuật toán Token Bucket).
   - Các gói tin vượt ngưỡng bị từ chối với `error` mã `RATE_LIMITED`.
5. **Cơ chế đóng sớm (Early Close - FR-GAME-15):**
   - Khi 100% người chơi trong phòng nộp bài (`all_answered = True`), câu hỏi lập tức đóng sớm mà không cần đợi hết giờ đếm ngược.
6. **Cá nhân hóa kết quả (Personalization - FR-GAME-16):**
   - Thông điệp `question.result` và `game.finished` được cá nhân hóa cho từng người chơi qua 1 lượt pipeline duy nhất trên Redis per replica, chứa chính xác điểm số, chuỗi thắng và thứ hạng hiện tại của người đó.


## 6. Bảng mã lỗi `error`

| Mã lỗi (`code`) | Ý nghĩa chi tiết |
|---|---|
| `BAD_MESSAGE` | Dữ liệu gửi lên không đúng định dạng JSON, sai kiểu dữ liệu hoặc thiếu trường bắt buộc |
| `UNSUPPORTED_VERSION` | Phiên bản giao thức `v` không được hỗ trợ (chỉ chấp nhận `v=1`) |
| `FORBIDDEN` | Người chơi cố ý gửi lệnh của Host hoặc Host cố ý nộp bài |
| `INVALID_STATE` | Lệnh gửi không phù hợp với trạng thái hiện tại của trận đấu |
| `RATE_LIMITED` | Gửi quá 5 thông điệp trong vòng 1 giây |
| `DATA_NOT_FOUND` | Không tìm thấy dữ liệu câu hỏi hoặc snapshot của trận đấu |
| `TRANSITION_FAILED` | Lệnh chuyển trạng thái bị từ chối |
| `VERSION_MISMATCH` | Lệnh `game.next` bị gửi trùng lặp hoặc sai số thứ tự câu hỏi kỳ vọng (chống bấm đúp) |
| `NO_MORE_QUESTIONS` | Đã hết câu hỏi trong bộ đề |


## 7. Mẫu thông điệp chi tiết (Examples)

### 7.1. Bắt đầu câu hỏi (`question.start`)
```json
{
  "type": "question.start",
  "v": 1,
  "data": {
    "q_index": 1,
    "total_questions": 10,
    "question_id": "c76f6ef4-2bf2-411a-8c76-5a41ea50e41b",
    "content": "Thủ đô của Việt Nam là gì?",
    "type": "single",
    "points": 1000,
    "time_limit_s": 15,
    "server_time": 1762740000000,
    "deadline": 1762740015000,
    "options": [
      {"id": "673f8e6c-0e7d-4cb4-b52e-503d42c3584e", "position": 1, "content": "Hà Nội"},
      {"id": "766c5d18-2e06-40e9-bca1-ef776e01a8ef", "position": 2, "content": "TP. Hồ Chí Minh"},
      {"id": "c1f7274f-9e67-4a0b-8515-56fa1e2b694b", "position": 3, "content": "Đà Nẵng"},
      {"id": "93b2a30b-9aa0-449e-a035-7c01b2a26569", "position": 4, "content": "Cần Thơ"}
    ]
  }
}
```

### 7.2. Người chơi nộp bài (`answer.submit`)
```json
{
  "type": "answer.submit",
  "v": 1,
  "data": {
    "q_index": 1,
    "selected_option_ids": ["673f8e6c-0e7d-4cb4-b52e-503d42c3584e"],
    "client_sent_epoch_ms": 1762740003500
  }
}
```

### 7.3. Server xác nhận nộp bài (`answer.ack`)
```json
{
  "type": "answer.ack",
  "v": 1,
  "data": {
    "accepted": true,
    "q_index": 1,
    "reason": null
  }
}
```

### 7.4. Kết quả câu hỏi (`question.result`)
```json
{
  "type": "question.result",
  "v": 1,
  "data": {
    "q_index": 1,
    "correct_option_ids": ["673f8e6c-0e7d-4cb4-b52e-503d42c3584e"],
    "distribution": {
      "673f8e6c-0e7d-4cb4-b52e-503d42c3584e": 42,
      "766c5d18-2e06-40e9-bca1-ef776e01a8ef": 6,
      "c1f7274f-9e67-4a0b-8515-56fa1e2b694b": 2,
      "93b2a30b-9aa0-449e-a035-7c01b2a26569": 0
    },
    "explanation": "Hà Nội là thủ đô của nước Cộng hòa Xã hội Chủ nghĩa Việt Nam.",
    "podium": [
      {"rank": 1, "player_id": "893c...", "nickname": "TopG", "score": 985, "streak": 1},
      {"rank": 2, "player_id": "4b1a...", "nickname": "Speedy", "score": 960, "streak": 1}
    ],
    "personal": {
      "score": 985,
      "added_score": 985,
      "streak": 1,
      "rank": 1,
      "is_correct": true
    }
  }
}
```

### 7.5. Trận đấu hoàn thành (`game.finished`)
```json
{
  "type": "game.finished",
  "v": 1,
  "data": {
    "game_id": "a98402c5-7b56-4089-a2e9-441d8e03e2c3",
    "total_players": 50,
    "podium": [
      {"rank": 1, "player_id": "893c...", "nickname": "TopG", "score": 9450, "streak": 8},
      {"rank": 2, "player_id": "4b1a...", "nickname": "Speedy", "score": 9120, "streak": 5},
      {"rank": 3, "player_id": "7a3f...", "nickname": "Star", "score": 8780, "streak": 6}
    ],
    "personal": {
      "score": 9450,
      "rank": 1
    }
  }
}
```

### 7.6. Báo cáo AI sẵn sàng (`report.ready`)
Phát ra sau khi ARQ worker hoàn tất tổng hợp số liệu và phân tích AI (hoặc chuyển sang chế độ degraded nếu AI gặp sự cố). Client nhận thông điệp này để tự động cập nhật UI từ "Đang phân tích..." sang hiển thị số liệu phân tích:
```json
{
  "type": "report.ready",
  "v": 1,
  "data": {
    "game_id": "a98402c5-7b56-4089-a2e9-441d8e03e2c3",
    "report_id": "b18204d6-8c67-5190-b3f0-552e9f14f3d4",
    "status": "ready",
    "has_ai_insights": true,
    "message": "Báo cáo phân tích trận đấu đã sẵn sàng."
  }
}
```
*Lưu ý: Nếu `status` là `"degraded"`, trường `has_ai_insights` mang giá trị `false`, client vẫn có thể lấy đầy đủ số liệu thống kê câu hỏi và chủ đề qua endpoint REST `/api/v1/games/{id}/report`.*
```
