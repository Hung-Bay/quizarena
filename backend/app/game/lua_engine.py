"""Lua Engine quản lý nạp và thực thi các Redis Lua Scripts cho Live Game (GM-1, GM-7, GM-14)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from redis.asyncio import Redis

_LUA_DIR = Path(__file__).parent / "lua"
_TRANSITION_LUA_PATH = _LUA_DIR / "transition.lua"
_SUBMIT_ANSWER_LUA_PATH = _LUA_DIR / "submit_answer.lua"

_TRANSITION_SCRIPT: str | None = None
_SUBMIT_ANSWER_SCRIPT: str | None = None

_TRANSITION_SHA: str | None = None
_SUBMIT_ANSWER_SHA: str | None = None


def get_transition_script() -> str:
    """Đọc nội dung script transition.lua."""
    global _TRANSITION_SCRIPT
    if _TRANSITION_SCRIPT is None:
        _TRANSITION_SCRIPT = _TRANSITION_LUA_PATH.read_text(encoding="utf-8")
    return _TRANSITION_SCRIPT


def get_submit_answer_script() -> str:
    """Đọc nội dung script submit_answer.lua."""
    global _SUBMIT_ANSWER_SCRIPT
    if _SUBMIT_ANSWER_SCRIPT is None:
        _SUBMIT_ANSWER_SCRIPT = _SUBMIT_ANSWER_LUA_PATH.read_text(encoding="utf-8")
    return _SUBMIT_ANSWER_SCRIPT


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Kết quả trả về từ transition.lua."""

    ok: bool
    status: str
    q_index: int
    deadline: int
    server_time: int
    deadline_version: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubmitAnswerResult:
    """Kết quả trả về từ submit_answer.lua."""

    accepted: bool
    is_correct: bool
    points_awarded: int
    new_streak: int
    total_score: int
    all_answered: bool
    response_ms: int
    error_code: str | None = None


async def execute_transition(
    redis: Redis,
    *,
    game_id: uuid.UUID | str,
    action: str,
    expected_q_index: int = 0,
    time_limit_s: int = 15,
    override_now_ms: int = 0,
) -> TransitionResult:
    """Thực thi kịch bản chuyển trạng thái trận đấu (start, next, close, end, pause)."""
    script = get_transition_script()
    state_key = f"game:{game_id}:state"
    keys = [state_key]

    if action == "start":
        args = [action, str(time_limit_s), str(override_now_ms)]
    elif action == "next":
        args = [action, str(expected_q_index), str(time_limit_s), str(override_now_ms)]
    elif action == "close":
        args = [action, str(expected_q_index)]
    elif action == "end":
        args = [action, str(override_now_ms)]
    elif action == "pause":
        args = [action]
    else:
        args = [action]

    raw_res = redis.eval(script, len(keys), *keys, *args)
    if asyncio.iscoroutine(raw_res) or hasattr(raw_res, "__await__"):
        res = await raw_res  # type: ignore[misc]
    else:
        res = raw_res

    # Parse kết quả: [ok (1/0), status_or_error, q_index, deadline, server_time, dl_ver]
    ok = bool(int(res[0]))
    if not ok:
        error = str(res[1]) if len(res) > 1 else "UNKNOWN_ERROR"
        current_status = str(res[2]) if len(res) > 2 else ""
        return TransitionResult(
            ok=False,
            status=current_status,
            q_index=0,
            deadline=0,
            server_time=0,
            deadline_version=0,
            error=error,
        )

    status = str(res[1])
    q_index = int(res[2]) if len(res) > 2 and res[2] else 0
    deadline = int(res[3]) if len(res) > 3 and res[3] else 0
    server_time = int(res[4]) if len(res) > 4 and res[4] else 0
    dl_ver = int(res[5]) if len(res) > 5 and res[5] else 0

    return TransitionResult(
        ok=True,
        status=status,
        q_index=q_index,
        deadline=deadline,
        server_time=server_time,
        deadline_version=dl_ver,
        error=None,
    )


async def execute_submit_answer(
    redis: Redis,
    *,
    game_id: uuid.UUID | str,
    player_id: uuid.UUID | str,
    q_index: int,
    selected_option_ids: list[str] | set[str],
    override_now_ms: int = 0,
) -> SubmitAnswerResult:
    """Thực thi kịch bản chấm điểm và ghi nhận đáp án atomic trên Redis (submit_answer.lua)."""
    script = get_submit_answer_script()

    gid = str(game_id)
    pid = str(player_id)
    opts_csv = ",".join(str(opt).strip() for opt in selected_option_ids if str(opt).strip())

    keys = [
        f"game:{gid}:state",
        f"game:{gid}:q:{q_index}:answered",
        f"game:{gid}:q:{q_index}:correct_keys",
        f"game:{gid}:q:{q_index}:meta",
        f"game:{gid}:lb",
        f"game:{gid}:streaks",
        f"game:{gid}:q:{q_index}:pts",
        f"game:{gid}:q:{q_index}:dist",
        f"game:{gid}:answers",
        f"game:{gid}:players",
    ]

    args = [
        pid,
        str(q_index),
        opts_csv,
        str(override_now_ms),
    ]

    raw_res = redis.eval(script, len(keys), *keys, *args)
    if asyncio.iscoroutine(raw_res) or hasattr(raw_res, "__await__"):
        res = await raw_res  # type: ignore[misc]
    else:
        res = raw_res

    # Parse kết quả:
    # Nếu bị từ chối: [0, error_code, extra]
    accepted = bool(int(res[0]))
    if not accepted:
        error_code = str(res[1]) if len(res) > 1 else "REJECTED"
        return SubmitAnswerResult(
            accepted=False,
            is_correct=False,
            points_awarded=0,
            new_streak=0,
            total_score=0,
            all_answered=False,
            response_ms=0,
            error_code=error_code,
        )

    # Chấp nhận: [1, is_correct (1/0), points_awarded, new_streak, new_total_score, all_answered (1/0), response_ms]
    is_correct = bool(int(res[1]))
    points_awarded = int(res[2])
    new_streak = int(res[3])
    total_score = int(float(res[4]))
    all_answered = bool(int(res[5]))
    response_ms = int(res[6]) if len(res) > 6 else 0

    return SubmitAnswerResult(
        accepted=True,
        is_correct=is_correct,
        points_awarded=points_awarded,
        new_streak=new_streak,
        total_score=total_score,
        all_answered=all_answered,
        response_ms=response_ms,
        error_code=None,
    )
