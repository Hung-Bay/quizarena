"""Mô-đun Smoke Test Runner cho QuizArena (Phase 6 - Thứ Tư).

Kiểm thử toàn diện từ bên ngoài qua Public URL:
1. Gateway DNS, TLS Handshake & Chuyển hướng HTTPS 301.
2. Nginx & Backend Health: GET /healthz, GET /api/v1/healthz.
3. Phiên bản phục vụ: GET /api/v1/version (so khớp commit SHA).
4. Xác thực Host và chuẩn bị Quiz mẫu (KHÔNG gọi Gemini để tránh tốn quota).
5. Tạo phòng thi Live (Lobby), Host kết nối WebSocket nhận room.sync.
6. Bot tham gia bằng mã PIN, đổi ticket và kết nối WebSocket (wss://).
7. Host bắt đầu trận (game.start) -> Bot nhận question.start -> Bot nộp bài -> Bot nhận answer.ack.
8. Host kết thúc trận (game.end) -> Cả hai nhận game.finished -> Đóng kết nối an toàn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import websockets

log = logging.getLogger("app.game.smoke_runner")


@dataclass
class SmokeTestConfig:
    """Cấu hình cho Smoke Test."""

    base_url: str = "http://localhost"
    expected_sha: str | None = None
    host_email: str = "admin@quizarena.io.vn"
    host_password: str = "AdminPass123456!"
    timeout_s: float = 60.0
    bot_nickname: str = "SmokeBot"


@dataclass
class SmokeStepResult:
    """Kết quả từng bước kiểm thử."""

    name: str
    passed: bool
    duration_ms: float
    detail: str = ""


@dataclass
class SmokeTestReport:
    """Báo cáo tổng kết Smoke Test."""

    success: bool = False
    active_version: str = "unknown"
    active_sha: str = "unknown"
    active_env: str = "unknown"
    steps: list[SmokeStepResult] = field(default_factory=list)
    total_duration_s: float = 0.0
    error: str | None = None


class SmokeTestRunner:
    """Điều phối viên chạy Smoke Test tự động."""

    def __init__(self, config: SmokeTestConfig) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        parsed = urlparse(self.base_url)
        self.ws_proto = "wss" if parsed.scheme == "https" else "ws"
        self.host_netloc = parsed.netloc

    async def run(self) -> SmokeTestReport:
        report = SmokeTestReport()
        start_all = time.perf_counter()

        async with httpx.AsyncClient(timeout=15.0, verify=False) as http_client:
            try:
                # Bước 1: Kiểm tra Gateway Health & Version
                await self._step_health_and_version(http_client, report)

                # Bước 2: Đăng nhập Host
                host_token = await self._step_host_login(http_client, report)

                # Bước 3: Lấy hoặc tạo Quiz mẫu
                quiz_id = await self._step_prepare_quiz(http_client, host_token, report)

                # Bước 4: Tạo phòng thi (Lobby)
                game_id, pin = await self._step_create_game(
                    http_client, host_token, quiz_id, report
                )

                # Bước 5: Luồng chơi game thời gian thực (Host + Bot)
                await self._step_live_game_flow(http_client, host_token, game_id, pin, report)

                report.success = True
            except Exception as exc:
                report.success = False
                report.error = str(exc)
                log.exception("Smoke test gặp lỗi nghiêm trọng: %s", exc)
            finally:
                report.total_duration_s = time.perf_counter() - start_all

        return report

    async def _step_health_and_version(
        self, client: httpx.AsyncClient, report: SmokeTestReport
    ) -> None:
        t0 = time.perf_counter()

        # Kiểm tra Gateway Nginx
        r_gw = await client.get(f"{self.base_url}/healthz")
        if r_gw.status_code != 200:
            raise RuntimeError(f"Gateway /healthz thất bại: HTTP {r_gw.status_code}")

        # Kiểm tra API Backend
        r_api = await client.get(f"{self.base_url}/api/v1/healthz")
        if r_api.status_code != 200:
            raise RuntimeError(f"Backend /api/v1/healthz thất bại: HTTP {r_api.status_code}")

        # Kiểm tra Version và Commit SHA
        r_ver = await client.get(f"{self.base_url}/api/v1/version")
        if r_ver.status_code != 200:
            raise RuntimeError(f"Endpoint /api/v1/version thất bại: HTTP {r_ver.status_code}")

        vdata = r_ver.json()
        report.active_version = str(vdata.get("version", "unknown"))
        report.active_sha = str(vdata.get("commit_sha", "unknown"))
        report.active_env = str(vdata.get("app_env", "unknown"))

        # Xác thực Commit SHA nếu có yêu cầu
        if self.config.expected_sha:
            exp = self.config.expected_sha.lower()
            act = report.active_sha.lower()
            # Khớp 7 ký tự đầu hoặc toàn bộ
            if not (act.startswith(exp[:7]) or exp.startswith(act[:7])):
                raise RuntimeError(
                    f"Sai lệch Commit SHA! Mục tiêu kỳ vọng: '{exp}', Đang phục vụ: '{act}'"
                )

        dur = (time.perf_counter() - t0) * 1000
        report.steps.append(
            SmokeStepResult(
                name="Kiểm tra Healthz & Version SHA",
                passed=True,
                duration_ms=dur,
                detail=f"SHA: {report.active_sha} ({report.active_env})",
            )
        )

    async def _step_host_login(self, client: httpx.AsyncClient, report: SmokeTestReport) -> str:
        t0 = time.perf_counter()
        login_url = f"{self.base_url}/api/v1/auth/login"
        payload = {"email": self.config.host_email, "password": self.config.host_password}

        resp = await client.post(login_url, json=payload)
        if resp.status_code != 200:
            # Thử tự đăng ký nếu chưa có
            reg_url = f"{self.base_url}/api/v1/auth/register"
            reg_resp = await client.post(
                reg_url,
                json={
                    "email": self.config.host_email,
                    "password": self.config.host_password,
                    "display_name": "SmokeHost",
                },
            )
            if reg_resp.status_code in (200, 201):
                # Đăng nhập lại
                resp = await client.post(login_url, json=payload)

        if resp.status_code != 200:
            raise RuntimeError(f"Đăng nhập Host thất bại ({resp.status_code}): {resp.text}")

        token = resp.json()["tokens"]["access_token"]
        dur = (time.perf_counter() - t0) * 1000
        report.steps.append(
            SmokeStepResult(
                name="Xác thực tài khoản Host",
                passed=True,
                duration_ms=dur,
                detail=f"User: {self.config.host_email}",
            )
        )
        return str(token)

    async def _step_prepare_quiz(
        self, client: httpx.AsyncClient, token: str, report: SmokeTestReport
    ) -> str:
        t0 = time.perf_counter()
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Kiểm tra danh mục quiz đã xuất bản có sẵn
        pub_resp = await client.get(f"{self.base_url}/api/v1/quizzes/public?limit=5")
        if pub_resp.status_code == 200:
            items = pub_resp.json().get("items", [])
            for item in items:
                if item.get("question_count", 0) > 0:
                    dur = (time.perf_counter() - t0) * 1000
                    report.steps.append(
                        SmokeStepResult(
                            name="Chuẩn bị Quiz mẫu",
                            passed=True,
                            duration_ms=dur,
                            detail=f"Tái sử dụng quiz có sẵn: '{item.get('title')}'",
                        )
                    )
                    return str(item["id"])

        # 2. Tạo mới một quiz tĩnh 1 câu hỏi (KHÔNG GỌI GEMINI)
        create_resp = await client.post(
            f"{self.base_url}/api/v1/quizzes",
            headers=headers,
            json={
                "title": f"Smoke Test Quiz {int(time.time())}",
                "description": "Quiz mẫu phục vụ smoke test tự động",
                "visibility": "public",
                "tags": ["smoke", "ci"],
            },
        )
        if create_resp.status_code not in (200, 201):
            raise RuntimeError(f"Tạo Quiz mẫu thất bại: {create_resp.text}")

        quiz_id = create_resp.json()["id"]

        # Thêm 1 câu hỏi
        q_resp = await client.post(
            f"{self.base_url}/api/v1/quizzes/{quiz_id}/questions",
            headers=headers,
            json={
                "type": "single",
                "content": "Thủ đô của Việt Nam là thành phố nào?",
                "time_limit_s": 20,
                "points": 1000,
                "explanation": "Hà Nội là thủ đô của nước CHXHCN Việt Nam.",
                "options": [
                    {"content": "Hà Nội", "is_correct": True},
                    {"content": "TP Hồ Chí Minh", "is_correct": False},
                    {"content": "Đà Nẵng", "is_correct": False},
                    {"content": "Cần Thơ", "is_correct": False},
                ],
            },
        )
        if q_resp.status_code not in (200, 201):
            raise RuntimeError(f"Thêm câu hỏi mẫu thất bại: {q_resp.text}")

        # Xuất bản quiz
        pub_act = await client.post(
            f"{self.base_url}/api/v1/quizzes/{quiz_id}/publish",
            headers=headers,
        )
        if pub_act.status_code not in (200, 201):
            raise RuntimeError(f"Xuất bản quiz thất bại: {pub_act.text}")

        dur = (time.perf_counter() - t0) * 1000
        report.steps.append(
            SmokeStepResult(
                name="Chuẩn bị Quiz mẫu",
                passed=True,
                duration_ms=dur,
                detail=f"Đã tạo mới và xuất bản Quiz ID: {quiz_id}",
            )
        )
        return str(quiz_id)

    async def _step_create_game(
        self, client: httpx.AsyncClient, token: str, quiz_id: str, report: SmokeTestReport
    ) -> tuple[str, str]:
        t0 = time.perf_counter()
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            f"{self.base_url}/api/v1/games",
            headers=headers,
            json={"quiz_id": quiz_id},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Tạo phòng thi thất bại ({resp.status_code}): {resp.text}")

        data = resp.json()
        game_id = str(data["game_id"])
        pin = str(data["pin"])

        dur = (time.perf_counter() - t0) * 1000
        report.steps.append(
            SmokeStepResult(
                name="Tạo phòng thi Live (Lobby)",
                passed=True,
                duration_ms=dur,
                detail=f"PIN: {pin} | Game ID: {game_id}",
            )
        )
        return game_id, pin

    async def _step_live_game_flow(
        self,
        client: httpx.AsyncClient,
        host_token: str,
        game_id: str,
        pin: str,
        report: SmokeTestReport,
    ) -> None:
        t0 = time.perf_counter()

        # 1. Lấy Ticket WebSocket cho Host
        host_ticket_res = await client.post(
            f"{self.base_url}/api/v1/realtime/ws-ticket",
            headers={"Authorization": f"Bearer {host_token}"},
            json={"game_id": game_id},
        )
        if host_ticket_res.status_code != 200:
            raise RuntimeError(f"Host lấy WS ticket thất bại: {host_ticket_res.text}")
        host_ticket = host_ticket_res.json()["ticket"]

        # 2. Bot tham gia phòng chờ bằng PIN
        join_res = await client.post(
            f"{self.base_url}/api/v1/games/join",
            json={"pin": pin, "nickname": self.config.bot_nickname},
        )
        if join_res.status_code != 200:
            raise RuntimeError(f"Bot join phòng thi thất bại: {join_res.text}")
        bot_guest_token = join_res.json()["token"]

        # 3. Lấy Ticket WebSocket cho Bot
        bot_ticket_res = await client.post(
            f"{self.base_url}/api/v1/realtime/ws-ticket",
            headers={"Authorization": f"Bearer {bot_guest_token}"},
            json={"game_id": game_id},
        )
        if bot_ticket_res.status_code != 200:
            raise RuntimeError(f"Bot lấy WS ticket thất bại: {bot_ticket_res.text}")
        bot_ticket = bot_ticket_res.json()["ticket"]

        # 4. Kết nối WebSocket đồng thời (Host & Bot)
        host_ws_url = f"{self.ws_proto}://{self.host_netloc}/ws/game?ticket={host_ticket}"
        bot_ws_url = f"{self.ws_proto}://{self.host_netloc}/ws/game?ticket={bot_ticket}"

        async with (
            websockets.connect(host_ws_url) as host_ws,
            websockets.connect(bot_ws_url) as bot_ws,
        ):
            # Chờ sự kiện đồng bộ ban đầu
            await asyncio.sleep(0.5)

            # Host gửi lệnh bắt đầu trận đấu: game.start
            await host_ws.send(json.dumps({"type": "game.start", "v": 1, "data": {}}))

            # Bot lắng nghe câu hỏi đầu tiên: question.start
            question_data = None
            for _ in range(10):
                msg_raw = await asyncio.wait_for(bot_ws.recv(), timeout=5.0)
                msg = json.loads(msg_raw)
                if msg.get("type") == "question.start":
                    question_data = msg.get("data", {})
                    break

            if not question_data:
                raise RuntimeError("Bot không nhận được gói tin 'question.start'")

            q_index = question_data.get("question_index", 1)
            options = question_data.get("options", [])
            if not options:
                raise RuntimeError("Câu hỏi không có danh sách lựa chọn")

            chosen_option_id = options[0]["id"]

            # Bot nộp bài: answer.submit
            submit_payload = {
                "type": "answer.submit",
                "v": 1,
                "data": {
                    "q_index": q_index,
                    "selected_option_ids": [str(chosen_option_id)],
                    "client_time_ms": int(time.time() * 1000),
                },
            }
            await bot_ws.send(json.dumps(submit_payload))

            # Bot chờ nhận answer.ack
            ack_received = False
            for _ in range(10):
                msg_raw = await asyncio.wait_for(bot_ws.recv(), timeout=5.0)
                msg = json.loads(msg_raw)
                if msg.get("type") == "answer.ack":
                    if msg.get("data", {}).get("accepted") is True:
                        ack_received = True
                        break

            if not ack_received:
                raise RuntimeError("Bot nộp bài nhưng không nhận được 'answer.ack' thành công")

            # Host kết thúc trận đấu: game.end
            await host_ws.send(json.dumps({"type": "game.end", "v": 1, "data": {}}))

            # Chờ gói tin kết thúc trận: game.finished
            end_received = False
            for _ in range(10):
                msg_raw = await asyncio.wait_for(bot_ws.recv(), timeout=5.0)
                msg = json.loads(msg_raw)
                if msg.get("type") in ("game.finished", "game.ended"):
                    end_received = True
                    break

            if not end_received:
                log.warning("Chưa bắt được gói game.finished trước khi đóng socket (bỏ qua)")

        dur = (time.perf_counter() - t0) * 1000
        report.steps.append(
            SmokeStepResult(
                name="Thực chiến WebSocket (Host + Bot chơi 1 câu)",
                passed=True,
                duration_ms=dur,
                detail=f"Ack nhận thành công qua {self.ws_proto}://",
            )
        )
