"""Vai trò và quyền (ma trận §3.2 của đề).

Các hằng số này là DỮ LIỆU MẪU cho `seed` và là nguồn đối chiếu cho test.
Lúc chạy, hệ thống KHÔNG đọc ma trận từ đây mà đọc từ database (và từ `permissions` trong JWT),
để không hard-code vai trò trong logic phân quyền (FR-AUTHZ-01).
"""

from enum import StrEnum


class RoleName(StrEnum):
    GUEST = "GUEST"
    PLAYER = "PLAYER"
    HOST = "HOST"
    ADMIN = "ADMIN"


class PermissionCode(StrEnum):
    GAME_PLAY = "game:play"  # chỉ trận đã join
    HISTORY_READ_OWN = "history:read_own"
    QUIZ_READ_PUBLIC = "quiz:read_public"
    QUIZ_WRITE_OWN = "quiz:write_own"
    QUIZ_AI_GENERATE = "quiz:ai_generate"
    GAME_HOST_OWN = "game:host_own"
    REPORT_READ_OWN_GAMES = "report:read_own_games"
    QUIZ_MODERATE = "quiz:moderate"
    USERS_MANAGE = "users:manage"
    SYSTEM_READ = "system:read"  # dashboard, quota


_GUEST = frozenset({PermissionCode.GAME_PLAY})

_PLAYER = _GUEST | {
    PermissionCode.HISTORY_READ_OWN,
    PermissionCode.QUIZ_READ_PUBLIC,
}

_HOST = _PLAYER | {
    PermissionCode.QUIZ_WRITE_OWN,
    PermissionCode.QUIZ_AI_GENERATE,
    PermissionCode.GAME_HOST_OWN,
    PermissionCode.REPORT_READ_OWN_GAMES,
}

_ADMIN = frozenset(PermissionCode)  # ADMIN có mọi quyền

ROLE_PERMISSIONS: dict[RoleName, frozenset[PermissionCode]] = {
    RoleName.GUEST: _GUEST,
    RoleName.PLAYER: _PLAYER,
    RoleName.HOST: _HOST,
    RoleName.ADMIN: _ADMIN,
}
