import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core import security
from app.core.config import get_settings
from app.core.security import TokenError

SECRET = get_settings().jwt_secret.get_secret_value()


# ---------------------------------------------------------------- mật khẩu


def test_hash_la_argon2id_va_khong_chua_mat_khau_goc():
    h = security.hash_password("Matkhau123")
    assert h.startswith("$argon2id$")
    assert "Matkhau123" not in h


def test_hai_lan_bam_cung_mat_khau_cho_ket_qua_khac_nhau_do_salt():
    assert security.hash_password("Matkhau123") != security.hash_password("Matkhau123")


def test_verify_dung_va_sai():
    h = security.hash_password("Matkhau123")
    assert security.verify_password("Matkhau123", h) is True
    assert security.verify_password("matkhau123", h) is False


def test_verify_voi_chuoi_bam_hong_tra_false_khong_nem_loi():
    assert security.verify_password("x", "day-khong-phai-ban-bam") is False


def test_ban_bam_moi_khong_can_bam_lai():
    assert security.password_needs_rehash(security.hash_password("Matkhau123")) is False


def test_verify_or_dummy_khi_email_khong_ton_tai_luon_false():
    assert security.verify_password_or_dummy("bat-ky", None) is False


def test_verify_or_dummy_khi_co_ban_bam_hoat_dong_binh_thuong():
    h = security.hash_password("Matkhau123")
    assert security.verify_password_or_dummy("Matkhau123", h) is True
    assert security.verify_password_or_dummy("sai", h) is False


async def test_ban_async_cho_cung_ket_qua():
    h = await security.hash_password_async("Matkhau123")
    assert await security.verify_password_or_dummy_async("Matkhau123", h) is True
    assert await security.verify_password_or_dummy_async("sai", h) is False


# ---------------------------------------------------------------- access token

USER_ID = uuid.uuid4()


def _access(**kw):
    return security.create_access_token(
        user_id=USER_ID,
        roles=["HOST"],
        permissions=["quiz:write_own", "game:play"],
        token_version=3,
        **kw,
    )


def test_access_token_du_cac_truong_theo_AU3():
    p = security.decode_token(_access(), expected_type="access")
    assert set(p) == {"sub", "roles", "permissions", "exp", "iat", "jti", "ver", "type"}
    assert p["sub"] == str(USER_ID)
    assert p["roles"] == ["HOST"]
    assert p["permissions"] == ["quiz:write_own", "game:play"]
    assert p["ver"] == 3
    assert p["type"] == "access"


def test_access_token_song_dung_so_phut_cau_hinh():
    p = security.decode_token(_access(), expected_type="access")
    assert p["exp"] - p["iat"] == get_settings().access_token_minutes * 60 == 15 * 60


def test_moi_token_co_jti_rieng():
    a = security.decode_token(_access(), expected_type="access")["jti"]
    b = security.decode_token(_access(), expected_type="access")["jti"]
    assert a != b


def test_token_het_han_bao_expired():
    old = datetime.now(UTC) - timedelta(hours=1)
    with pytest.raises(TokenError) as e:
        security.decode_token(_access(now=old), expected_type="access")
    assert e.value.reason == "expired"


def test_chu_ky_sai_bao_invalid():
    bad = jwt.encode({"sub": "x"}, "khoa-khac-" + "y" * 40, algorithm="HS256")
    with pytest.raises(TokenError) as e:
        security.decode_token(bad, expected_type="access")
    assert e.value.reason == "invalid"


def test_token_bi_sua_payload_bi_tu_choi():
    head, payload, sig = _access().split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["roles"] = ["ADMIN"]
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    with pytest.raises(TokenError):
        security.decode_token(f"{head}.{forged}.{sig}", expected_type="access")


def test_tan_cong_alg_none_bi_tu_choi():
    now = datetime.now(UTC)
    claims = {
        "sub": "x",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": "j",
        "type": "access",
    }
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    with pytest.raises(TokenError):
        security.decode_token(unsigned, expected_type="access")


def test_token_thieu_truong_bat_buoc_bi_tu_choi():
    no_exp = jwt.encode({"sub": "x", "type": "access"}, SECRET, algorithm="HS256")
    with pytest.raises(TokenError):
        security.decode_token(no_exp, expected_type="access")


def test_chuoi_rac_bi_tu_choi():
    with pytest.raises(TokenError):
        security.decode_token("khong.phai.jwt", expected_type="access")


# ---------------------------------------------------------------- guest token

GAME_ID = uuid.uuid4()


def test_guest_token_dung_pham_vi_theo_AU9():
    p = security.decode_token(security.create_guest_token(game_id=GAME_ID), expected_type="guest")
    assert p["type"] == "guest"
    assert p["sub"].startswith("guest:")
    assert uuid.UUID(p["sub"].removeprefix("guest:"))
    assert p["game_id"] == str(GAME_ID)
    assert p["permissions"] == ["game:play"]
    assert "ver" not in p  # khách không có tài khoản nên không có token_version


def test_guest_token_song_toi_da_3_gio():
    p = security.decode_token(security.create_guest_token(game_id=GAME_ID), expected_type="guest")
    assert p["exp"] - p["iat"] <= 3 * 3600


def test_guest_token_het_han():
    old = datetime.now(UTC) - timedelta(hours=4)
    with pytest.raises(TokenError) as e:
        security.decode_token(
            security.create_guest_token(game_id=GAME_ID, now=old), expected_type="guest"
        )
    assert e.value.reason == "expired"


def test_guest_token_khong_dung_duoc_nhu_access_token_va_nguoc_lai():
    guest = security.create_guest_token(game_id=GAME_ID)
    with pytest.raises(TokenError):
        security.decode_token(guest, expected_type="access")
    with pytest.raises(TokenError):
        security.decode_token(_access(), expected_type="guest")


def test_guest_token_thieu_game_id_bi_tu_choi():
    now = datetime.now(UTC)
    claims = {
        "sub": "guest:abc",
        "iat": now,
        "exp": now + timedelta(hours=1),
        "jti": "j",
        "type": "guest",
        "permissions": ["game:play"],
    }
    with pytest.raises(TokenError):
        security.decode_token(jwt.encode(claims, SECRET, algorithm="HS256"), expected_type="guest")


# ---------------------------------------------------------------- refresh token


def test_refresh_token_ngau_nhien_va_du_dai():
    a, b = security.generate_refresh_token(), security.generate_refresh_token()
    assert a != b
    assert len(a) >= 64


def test_hash_token_la_sha256_hex_on_dinh():
    t = security.generate_refresh_token()
    h = security.hash_token(t)
    assert len(h) == 64 and h == security.hash_token(t)
    assert h != t


def test_refresh_token_het_han_sau_so_ngay_cau_hinh():
    now = datetime(2026, 11, 2, tzinfo=UTC)
    days = get_settings().refresh_token_days  # đọc từ cấu hình (.env), không hard-code số ngày
    assert security.refresh_token_expires_at(now) - now == timedelta(days=days)
