# ADR-004: Kiến trúc Mở rộng Quy mô WebSocket Realtime: Redis Pub/Sub so với Consistent Hashing

- **Trạng thái:** Đã chấp thuận (Accepted)
- **Ngày quyết định:** 2026-11-16
- **Người đề xuất:** Nhóm phát triển Hạ tầng & Realtime Engine QuizArena
- **Tham chiếu:** `docs/SRS.md` (§7.4, NF-1..NF-5), `docs/LOADTEST.md`, `docs/WS_PROTOCOL.md`

---

## 1. Ngữ cảnh và Bài toán (Context & Problem Statement)

QuizArena là nền tảng đấu trường trắc nghiệm trực tuyến thời gian thực yêu cầu độ trễ cực thấp:
- Xử lý đáp án: $p_{95} < 150\text{ ms}$ (NF-2).
- Phát tán câu hỏi: $p_{95} < 500\text{ ms}$ (NF-3).
- Chịu tải tối thiểu 300 người chơi đồng thời trong 1 phòng đấu hoặc nhiều phòng 100 người (NF-1).
- Tự phục hồi kết nối $< 3\text{ giây}$ khi một node server gặp sự cố (NF-5).

Khác với REST API (vốn phi trạng thái — Stateless), kết nối **WebSocket là kết nối có trạng thái (Stateful Long-lived TCP Connection)**:
- Người chơi giữ kết nối mở liên tục với một process Uvicorn/FastAPI cụ thể.
- Một trận đấu gồm 1 Host và 300 người chơi có thể phân tán trên các tiến trình server khác nhau.
- **Thách thức:** Khi Host phát lệnh `question.start` hoặc `player.kick` trên Replica A, làm sao các người chơi đang kết nối tại Replica B và Replica C nhận được thông điệp ngay lập tức mà không gây trễ mạng hoặc nghẽn cổ chai đơn luồng?

---

## 2. Các phương án kiến trúc đã xem xét (Considered Options)

### Phương án 1: Định tuyến có trạng thái bằng Consistent Hashing tại Gateway (Stateful Routing)
- **Nguyên lý:** Nginx cấu hình module `upstream_hash` theo mã PIN hoặc `game_id` (ví dụ: `hash $arg_pin consistent;`). Toàn bộ người chơi và Host của cùng một trận đấu được Nginx gom về kết nối tới đúng 1 replica duy nhất.
- **Ưu điểm:**
  - Broadcast hoàn toàn cục bộ trong bộ nhớ tiến trình (In-process memory loop), không tốn hop mạng trung gian sang Message Bus.
  - Loại bỏ chi phí băng thông Pub/Sub giữa các replica cho trận đấu đó.
- **Nhược điểm nghiêm trọng (Lý do bác bỏ):**
  1. **Vấn đề Điểm nóng (Hotspot / Load Imbalance):** Nếu một trận đấu lớn diễn ra (ví dụ: 500 người chơi trong một hội trường), toàn bộ 500 kết nối dồn vào 1 CPU core của replica đó. Trong khi đó, các replica khác hoàn toàn rảnh rỗi. Event loop đơn luồng của Python bị bão hòa, đẩy độ trễ xử lý đáp án và phát tán lên cao.
  2. **Bán kính thiệt hại lớn (Large Blast Radius):** Nếu replica chịu tải trận đấu lớn đó bị dừng hoặc crash, **100% người chơi của trận đấu bị đứt kết nối cùng một lúc**.
  3. **Tái băm khi mở rộng (Re-hashing on Scale):** Khi thêm replica mới vào cụm, Consistent Hash Ring bị dịch chuyển vị trí node. Điều này khiến các kết nối WebSocket mới có thể bị phân về node khác với node cũ nếu không có cơ chế tracking phức tạp.

---

### Phương án 2: Phân tán trạng thái qua Redis Pub/Sub kết hợp Nginx `least_conn` (ĐƯỢC CHỌN)
- **Nguyên lý:**
  - Các replica Realtime hoàn toàn độc lập và **phi trạng thái đối với phòng chơi** (Room-agnostic).
  - Nginx phân bổ kết nối người chơi bằng thuật toán **`least_conn`**: Node nào đang giữ ít kết nối WebSocket hơn sẽ nhận kết nối tiếp theo. 300 người chơi của 1 trận được phân bổ đều ra cả 3 replica (mỗi node gánh ~100 kết nối).
  - Giao tiếp xuyên replica qua **Redis Pub/Sub channel `game:{game_id}`**:
    - Khi có sự kiện phát sóng (Host mở câu, Host kick người chơi, câu hỏi kết thúc): Replica phát sự kiện gọi `redis.publish("game:" .. game_id, payload)`.
    - Toàn bộ 3 replica đều lắng nghe channel này qua một background listener (`redis.pubsub()`). Khi nhận message, replica duyệt qua danh sách client cục bộ đang thuộc `game_id` đó và gửi xuống socket.
- **Ưu điểm:**
  1. **Tận dụng đa nhân CPU (Multi-core Concurrency):** Tải của 300 bots được san sẻ đều cho 3 tiến trình Uvicorn riêng biệt trên 3 CPU cores.
  2. **Khả năng chịu lỗi cao (Fault Tolerance):** Nếu 1 replica bị dừng (`docker stop`), chỉ 1/3 số người chơi bị ngắt kết nối. 2/3 số người chơi còn lại trên 2 replica kia vẫn tiếp tục chơi bình thường mà không hề hay biết sự cố.
  3. **Zero-downtime & Dễ dàng Scale:** Replica mới khởi động có thể tiếp nhận kết nối ngay lập tức mà Nginx không cần tính toán lại bảng băm.

---

## 3. Lập luận & Số liệu Thực nghiệm Định lượng

Để kiểm chứng quyết định kiến trúc trên cơ sở khoa học, chúng tôi đã thực hiện bài đo thực nghiệm đối đầu giữa **1 Replica đơn lẻ** vs **3 Replicas phân tán qua Redis Pub/Sub** với cùng kịch bản **300 bots** (trích xuất từ [LOADTEST.md](file:///d:/CodingProject/Python/quizarena/docs/LOADTEST.md#L127-L148)):

### Bảng đối so sánh số liệu đo đạc thực tế:

| Chỉ số đo lường thực tế | 1 Replica Realtime | 3 Replicas Realtime (Pub/Sub) | Chênh lệch & Đánh giá định lượng |
|---|:---:|:---:|---|
| **Số kết nối đồng thời** | 300 kết nối trên 1 PID | 100 kết nối / PID $\times$ 3 nodes | Cân bằng tải hoàn hảo qua `least_conn` |
| **`answer.ack` RTT Client ($p_{50}$)** | 4.7 ms | **3.9 ms** | 3 Replicas nhanh hơn 17.0% |
| **`answer.ack` RTT Client ($p_{95}$)** | 9.3 ms | **7.3 ms** | **3 Replicas nhanh hơn 21.5%** |
| **`answer.ack` RTT Client ($p_{99}$)** | 10.9 ms | **8.7 ms** | 3 Replicas nhanh hơn 20.2% |
| **Broadcast Delay Client ($p_{50}$)** | **42.4 ms** | 57.6 ms | 1 Replica nhanh hơn ở trung vị (không tốn hop mạng) |
| **Broadcast Delay Client ($p_{95}$)** | 85.4 ms | **66.6 ms** | **3 Replicas nhanh hơn 22.0% ở phân vị đuôi** |
| **Broadcast Delay Client ($p_{99}$)** | 88.4 ms | **67.6 ms** | **3 Replicas nhanh hơn 23.5% ở phân vị $p_{99}$** |
| **Áp lực Event Loop** | Dồn 100% vào 1 Core | Phân tải trên 3 Cores | Loại bỏ hoàn toàn nghẽn đơn luồng Python |
| **Thời gian Reconnect khi node chết** | Chết toàn bộ trận đấu | **$1.62\text{ s} - 2.08\text{ s}$** | Đạt chuẩn phục hồi SLA NF-5 ($< 3\text{ s}$) |

### Phân tích ý nghĩa số liệu:
1. **Tại sao Broadcast $p_{50}$ của 1 Replica lại thấp hơn (42.4 ms vs 57.6 ms)?**
   - Ở 50% số thông điệp đầu tiên, 1 replica phát tán trực tiếp trong RAM mà không cần gửi gói tin qua socket Redis Pub/Sub, tiết kiệm khoảng $10\text{ ms} - 15\text{ ms}$ overhead mạng loopback.
2. **Tại sao ở $p_{95}$ và $p_{99}$, 3 Replicas lại vượt trội hơn hẳn (66.6 ms vs 85.4 ms)?**
   - Đây là điểm mấu chốt của kiến trúc: Khi 1 tiến trình Python phải thực hiện vòng lặp `for ws in 300_sockets: await ws.send_text()`, các socket nằm ở cuối danh sách (từ socket 250 đến 300) phải xếp hàng đợi event loop xử lý tuần tự, làm kéo dài độ trễ phân vị đuôi ($p_{95}, p_{99}$).
   - Trong mô hình 3 Replicas, sau khi nhận 1 broadcast từ Redis Pub/Sub, mỗi replica chỉ phải lặp qua **100 sockets**. Việc gửi socket diễn ra song song đồng thời trên 3 CPU cores vật lý, giúp độ trễ đuôi $p_{95}$ giảm mạnh **22%**.

---

## 4. Các Vấn đề Kỹ thuật Đáng chú ý khi Vận hành

### 4.1. Hiện tượng "Scale-up không tự cân bằng lại kết nối cũ"
- **Hiện tượng:** Trong quá trình load test, khi hệ thống đang có 300 kết nối tại `realtime-1`, việc chạy `docker compose up -d --scale realtime=3` chỉ sinh ra thêm 2 container mới. **Toàn bộ 300 kết nối cũ vẫn nằm nguyên tại `realtime-1`**, 2 container mới có `ws_connections = 0`.
- **Bản chất:** TCP/WebSocket là kết nối giữ trạng thái liên tục. Nginx chỉ can thiệp ở thời điểm bắt tay (Handshake). Một khi kết nối đã thiết lập, Nginx chỉ forward dữ liệu hai chiều và không thể tự ý di dời socket đang mở sang node khác nếu không ngắt kết nối.
- **Giải pháp:** Tận dụng cơ chế Graceful Drain (gửi `server.draining` kèm mã 1001) khi cần tái cân bằng hoặc nâng cấp phiên bản không gián đoạn.

### 4.2. Tại sao `least_conn` tốt hơn `round_robin` cho WebSocket?
- `round_robin` phân bổ tuần tự không quan tâm tới thời lượng kết nối. Trong môi trường WebSocket, người chơi có thời gian chơi khác nhau (có người vào 1 phút rồi thoát, có người ở lại 30 phút). `round_robin` sau một thời gian sẽ dẫn đến tình trạng node chứa nhiều kết nối "sống lâu" bị quá tải, trong khi node khác lại ít kết nối.
- `least_conn` liên tục theo dõi số lượng socket TCP thực tế đang duy trì trên từng upstream và đẩy kết nối mới vào node đang có ít kết nối nhất, giúp tổng số CCU luôn được chia đều tuyệt đối.

### 4.3. Bão tái kết nối (Thundering Herd) và Vai trò của Randomized Jitter
- Khi 1 replica bị dừng hoặc khởi động lại (Deploy/Drain), hàng trăm client bị đứt kết nối cùng một phần nghìn giây.
- Nếu client lập tức gửi yêu cầu kết nối lại đồng loạt (Deterministic Retry), một đợt bão kết nối (Thundering Herd) sẽ đập vào Nginx và các replica còn lại, gây nghẽn CPU và sập dây chuyền (Cascading Failure).
- **Giải pháp bắt buộc:** Client phải áp dụng thuật toán **Exponential Backoff kết hợp Randomized Jitter**:
  $$T_{\text{reconnect}} = \min(T_{\text{max}}, T_{\text{base}} \times 2^{\text{attempt}}) + \text{random}(0, \text{jitter\_ms})$$
  Đồng thời, server gửi kèm trường `retry_after_ms` (ngẫu nhiên 200..800ms) trong envelope `server.draining` để phân tán dàn trải đỉnh lưu lượng.

---

## 5. Giới hạn Quy mô & Ngưỡng Chuyển dịch Kiến trúc (Scalability Horizon)

Mô hình **Redis Pub/Sub kết hợp Nginx `least_conn`** là giải pháp tối ưu cho QuizArena ở Phase 5:
- **Ngưỡng phù hợp:** Đáp ứng hoàn hảo các trận đấu từ 1 đến 500 người chơi đồng thời, cụm từ 2 đến 10 replicas realtime, tổng lưu lượng dưới 10,000 CCU.
- **Giới hạn kỹ thuật:** Khi hệ thống phát triển lên quy mô hàng chục ngàn kết nối đồng thời trên hàng trăm nodes, Redis Pub/Sub sẽ gặp giới hạn về băng thông do phải sao chép gói tin tới toàn bộ subscribers ($O(M)$ với $M$ là số node).
- **Định hướng Phase 7 (Nếu scale lớn hơn):**
  - Chuyển sang mô hình **Room-based Partitioning** (Dynamic Sharding) sử dụng Redis Cluster hoặc Message Broker chuyên dụng như NATS JetStream / Apache Kafka.
  - Phân vùng phòng thi theo cụm máy chủ để hạn chế phạm vi broadcast chỉ trong các node có người chơi tham gia phòng đó.

---

## 6. Kết luận & Quyết định

Kiến trúc **Redis Pub/Sub kết hợp Nginx `least_conn`** chính thức được phê duyệt và triển khai toàn diện trong QuizArena. Toàn bộ các tiêu chí hiệu năng (NF-1, NF-2, NF-3, NF-5) đều được kiểm chứng thực tế và vượt chỉ tiêu đề ra.
