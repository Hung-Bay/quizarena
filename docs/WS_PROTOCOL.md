# WS_PROTOCOL — Giao thức WebSocket của QuizArena

**Phiên bản giao thức:** `v = 1` · **Trạng thái tài liệu:** NHÁP (Phase 0). Chi tiết payload chốt ở Phase 3 · **Tham chiếu:** `docs/SRS.md` (FR-AUTH-11, FR-GAME-03…22), `docs/ARCHITECTURE.md`


## 1. Kết nối và xác thực

Trình duyệt không gửi được header `Authorization` khi mở WebSocket, nên dùng vé dùng một lần (FR-AUTH-11):

```
1. Client  → POST /api/v1/realtime/ws-ticket   (kèm access token hoặc guest token)
2. Server  ← { "ticket": "<chuỗi ngẫu nhiên>" }     vé lưu Redis, TTL 30 giây
3. Client  → mở wss://<domain>/ws/game?ticket=<ticket>
4. Server  đổi vé lấy danh tính bằng GETDEL (xóa ngay), rồi chấp nhận hoặc đóng kết nối
```

- Vé dùng lần thứ hai hoặc quá 30 giây bị từ chối.
- Vai trò của kết nối (host hay người chơi) lấy từ danh tính trong vé, **không** lấy từ nội dung thông điệp.
- Quyền cần có để xin vé: `game:play` (người chơi) hoặc `game:host_own` (host của đúng trận).

### Mã đóng kết nối **[ĐỀ XUẤT]**

| Mã | Tên | Khi nào |
|---|---|---|
| 4001 | `TICKET_INVALID` | Vé không tồn tại, hết hạn hoặc đã dùng |
| 4002 | `KICKED` | Người chơi bị host loại (FR-GAME-05) |
| 4003 | `GAME_ENDED` | Trận đã kết thúc hoặc hết hạn |
| 4004 | `PROTOCOL_ERROR` | Vi phạm giao thức nghiêm trọng hoặc lặp lại |
| 1013 | (chuẩn) | Server tạm thời không nhận thêm, thử lại |

## 2. Khung thông điệp (envelope)

Mọi thông điệp, hai chiều, là một đối tượng JSON:

```json
{ "type": "answer.submit", "v": 1, "data": { }, "seq": 17 }
```

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `type` | string | Tên thông điệp, nằm trong danh sách ở mục 3–4 |
| `v` | integer | Phiên bản giao thức. Server từ chối `v` không hỗ trợ bằng `error` |
| `data` | object | Nội dung riêng của từng `type` (chốt ở Phase 3) |
| `seq` | integer | Số thứ tự **[ĐỀ XUẤT]**: ở chiều server→client, tăng dần theo từng trận để client phát hiện thiếu thông điệp và yêu cầu snapshot; ở chiều client→server, client tự tăng để server loại thông điệp lặp |

Thông điệp có `type` ngoài danh sách, thiếu `v`, hoặc `data` sai cấu trúc → server trả `error`, **không đổi trạng thái trận** (FR-GAME-22).

## 3. Server → Client

| `type` | Gửi cho | Khi nào | Nội dung chính (nháp) | FR |
|---|---|---|---|---|
| `lobby.update` | Host, người chơi | Có người vào/ra/bị kick ở phòng chờ | Danh sách người chơi (nickname, trạng thái online) | FR-GAME-03 |
| `question.start` | Mọi người | Bắt đầu một câu | `index`, nội dung câu, các lựa chọn, `time_limit`, `server_time`, `deadline` (epoch ms). **Không có đáp án đúng** | FR-GAME-06, 08 |
| `answer.ack` | Người trả lời | Sau mỗi `answer.submit` | `accepted` (bool), `reason` khi từ chối (ví dụ `LATE`). Chỉ báo "đã ghi nhận", chưa nói đúng/sai | FR-GAME-07, 09 |
| `question.result` | Mọi người; mỗi người chơi kèm phần riêng | Hết câu | Đáp án đúng, phân bố lựa chọn; riêng người chơi: điểm câu, tổng điểm, thứ hạng | FR-GAME-16 |
| `leaderboard` | Mọi người | Sau kết quả câu | Top 10 (nickname, điểm) | FR-GAME-16 |
| `game.finished` | Mọi người | Trận kết thúc | Bục vinh danh, kết quả cuối; báo cáo AI sẽ đến sau | FR-AIRPT-01 |
| `error` | Người gửi lỗi | Thông điệp sai hoặc bị từ chối | `code`, `message` | FR-GAME-21, 22 |
| `server.draining` | Mọi kết nối của replica đang tắt | Replica sắp dừng (scale down, deploy) | Thời gian còn lại dự kiến; client chuẩn bị kết nối lại | NF-5 |
| `snapshot` **[ĐỀ XUẤT]** | Client vừa kết nối lại | Ngay sau khi kết nối thành công | Trạng thái hiện tại: câu đang chạy cùng thời gian còn lại, hoặc màn kết quả; điểm của người đó | FR-GAME-17 |

## 4. Client → Server

| `type` | Ai gửi | Mô tả | FR |
|---|---|---|---|
| `answer.submit` | Người chơi | Nộp đáp án cho câu hiện tại (mã câu, danh sách lựa chọn). Mỗi người mỗi câu một lần, gửi lại trả kết quả lần đầu | FR-GAME-07, 09, 13 |
| `heartbeat` | Người chơi | Gửi mỗi 15 giây. Quá 45 giây không có thì bị đánh dấu offline (không xóa điểm) | FR-GAME-18 |
| `game.start` | Host | Bắt đầu trận từ phòng chờ | FR-GAME-14 |
| `game.next` | Host | Sang câu tiếp theo | FR-GAME-14 |
| `game.pause` | Host | Tạm dừng trận (cách mô hình hóa còn chờ chốt, xem `ARCHITECTURE.md` mục 10) | FR-GAME-14 |
| `game.end` | Host | Kết thúc trận sớm; ghi `audit_logs` | FR-GAME-14; FR-ADM-05 |
| `player.kick` | Host | Loại một người chơi; người đó không vào lại trong 10 phút | FR-GAME-05 |

Lệnh của host chỉ được chấp nhận từ kết nối có danh tính host của **đúng trận đó**; người chơi gửi lệnh host bị từ chối bằng `error` mã `FORBIDDEN`.

## 5. Quy tắc chung

1. **Đồng hồ do server quyết định.** Client chỉ dùng `server_time` và `deadline` để hiển thị đếm ngược, tự hiệu chỉnh độ lệch giờ. Đáp án đến sau `deadline + 500 ms` bị từ chối với `reason="LATE"` (FR-GAME-06, 07).
2. **Không lộ đáp án.** Mọi thông điệp trước khi hết câu không chứa đáp án đúng, kể cả trường ẩn (FR-GAME-08).
3. **Idempotent.** Nộp đáp án lặp lại không cộng điểm lần nữa (FR-GAME-09).
4. **Giới hạn tốc độ.** Tối đa 5 thông điệp mỗi giây mỗi kết nối; phần vượt bị từ chối bằng `error` mã `RATE_LIMITED` (FR-GAME-21).
5. **Kết nối lại.** Cùng danh tính kết nối lại sẽ nhận snapshot hiện tại và giữ nguyên điểm; host giữ quyền điều khiển (FR-GAME-17).
6. **Chuyển kết nối khi tắt replica.** Replica gửi `server.draining`, client kết nối lại (xin vé mới) trong vòng 3 giây (NF-5).
7. **Phiên bản.** Thay đổi không tương thích tăng `v`; server có thể hỗ trợ đồng thời nhiều `v` trong thời gian chuyển tiếp.

## 6. Mã lỗi của thông điệp `error` **[ĐỀ XUẤT]**

| `code` | Ý nghĩa |
|---|---|
| `BAD_MESSAGE` | Sai cấu trúc, thiếu `v`, `type` không hợp lệ |
| `UNSUPPORTED_VERSION` | `v` không được hỗ trợ |
| `FORBIDDEN` | Gửi lệnh không đúng quyền (ví dụ người chơi gửi `game.next`) |
| `INVALID_STATE` | Lệnh không hợp lệ ở trạng thái hiện tại (ví dụ `game.next` khi đang ở phòng chờ) |
| `RATE_LIMITED` | Vượt 5 thông điệp/giây |
| `NOT_ACTIVE` | Nộp đáp án khi không có câu nào đang chạy |
| `ALREADY_ANSWERED` | Đã trả lời câu này (kèm kết quả lần đầu) |

Lý do từ chối trong `answer.ack` (`reason`): `LATE`, `NOT_ACTIVE`, `ALREADY_ANSWERED`.

## 7. Ví dụ minh họa (nháp, cấu trúc `data` chưa chốt)

**Server → Client: bắt đầu câu hỏi**

```json
{
  "type": "question.start", "v": 1, "seq": 12,
  "data": {
    "index": 3,
    "content": "HTTP status code nào nghĩa là Not Found?",
    "options": [{"id": "a", "text": "200"}, {"id": "b", "text": "404"}, {"id": "c", "text": "500"}],
    "time_limit": 20,
    "server_time": 1761400000000,
    "deadline":    1761400020000
  }
}
```

**Client → Server: nộp đáp án**

```json
{ "type": "answer.submit", "v": 1, "seq": 5, "data": { "question_index": 3, "option_ids": ["b"] } }
```

**Server → Client: đáp án bị từ chối vì trễ (FR-GAME-07)**

```json
{ "type": "answer.ack", "v": 1, "seq": 13, "data": { "accepted": false, "reason": "LATE" } }
```
