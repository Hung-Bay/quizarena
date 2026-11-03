"""Đăng ký, đăng nhập, refresh rotation, reuse detection (FR-AUTH-01…05). Cần Postgres đã migrate."""

import asyncio
import hashlib
import uuid
from http.cookies import SimpleCookie

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import create_session_factory
from app.core.security import decode_token
from app.main_api import app
from app.services.seed import seed_rbac

PASSWORD = "Matkhau123"
REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"


# ------------------------------------------------------------------ hạ tầng test


@pytest_asyncio.fixture
async def client(db_engine):
    async with create_session_factory(db_engine)() as session, session.begin():
        await seed_rbac(session)  # register cần vai trò PLAYER đã có
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    async with (
        db_engine.begin() as conn
    ):  # dọn user thử nghiệm (token, user_roles tự xóa theo CASCADE)
        await conn.execute(text("DELETE FROM users WHERE email LIKE 'u-%@example.com'"))


@pytest.fixture
def db(db_engine):
    async def run(sql: str, **params):
        async with db_engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return result.all() if result.returns_rows else None

    return run


def new_email() -> str:
    return f"u-{uuid.uuid4().hex[:10]}@example.com"


async def register(client, email=None, password=PASSWORD, **extra):
    return await client.post(
        REGISTER, json={"email": email or new_email(), "password": password, **extra}
    )


async def login(client, email, password=PASSWORD):
    return await client.post(LOGIN, json={"email": email, "password": password})


def refresh_cookie(response) -> SimpleCookie:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie


async def post_refresh(client, token):
    # Cookie gửi tay: httpx không gửi cookie `Secure` qua http://, trình duyệt thật thì có.
    return await client.post(REFRESH, headers={"Cookie": f"refresh_token={token}"})


async def registered_and_logged_in(client):
    email = new_email()
    assert (await register(client, email)).status_code == 201
    response = await login(client, email)
    assert response.status_code == 200
    return email, response, refresh_cookie(response)["refresh_token"].value


# ------------------------------------------------------------------ AU-1: đăng ký


async def test_dang_ky_thanh_cong(client, db):
    email = new_email()
    r = await register(client, email, display_name="An")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == email
    assert body["display_name"] == "An"
    assert body["roles"] == ["PLAYER"]
    assert "password" not in r.text and "hash" not in r.text

    (row,) = await db("SELECT password_hash FROM users WHERE email = :e", e=email)
    assert row.password_hash.startswith("$argon2id$")
    assert PASSWORD not in row.password_hash


async def test_ten_hien_thi_mac_dinh_la_phan_truoc_dau_a(client):
    email = new_email()
    r = await register(client, email)
    assert r.json()["display_name"] == email.split("@")[0]


async def test_email_duoc_luu_chu_thuong(client, db):
    local = uuid.uuid4().hex[:10]
    r = await register(client, f"U-{local}@Example.COM")
    assert r.status_code == 201
    assert r.json()["email"] == f"u-{local}@example.com"


async def test_dang_ky_trung_email_khong_phan_biet_hoa_thuong(client):
    local = uuid.uuid4().hex[:10]
    assert (await register(client, f"u-{local}@example.com")).status_code == 201
    r = await register(client, f"U-{local}@EXAMPLE.com")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.parametrize(
    "password",
    ["abc123", "abcdefgh", "12345678", "a1" * 70],
    ids=["qua-ngan", "khong-co-so", "khong-co-chu", "dai-hon-128"],
)
async def test_mat_khau_yeu_bi_tu_choi(client, password):
    r = await register(client, password=password)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_email_sai_dinh_dang(client):
    r = await register(client, "khong-phai-email")
    assert r.status_code == 422


async def test_dang_ky_dong_thoi_cung_email_chi_mot_thanh_cong(client):
    email = new_email()
    results = await asyncio.gather(*[register(client, email) for _ in range(6)])
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409, 409, 409]


# ------------------------------------------------------------------ AU-2, AU-3: đăng nhập


async def test_dang_nhap_tra_access_token_dung_noi_dung(client):
    _, r, _ = await registered_and_logged_in(client)
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().access_token_minutes * 60
    claims = decode_token(body["access_token"], expected_type="access")
    assert claims["sub"] == body["user"]["id"]
    assert claims["roles"] == ["PLAYER"]
    assert claims["permissions"] == ["game:play", "history:read_own", "quiz:read_public"]
    assert claims["ver"] == 0


async def test_cookie_refresh_co_dung_4_thuoc_tinh(client):
    _, r, raw = await registered_and_logged_in(client)
    morsel = refresh_cookie(r)["refresh_token"]
    assert morsel["httponly"]
    assert morsel["secure"]
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/api/v1/auth"
    assert int(morsel["max-age"]) == get_settings().refresh_token_days * 24 * 3600
    assert raw not in r.text  # refresh token KHÔNG nằm trong body, chỉ nằm trong cookie


async def test_refresh_token_chi_luu_ban_bam_sha256(client, db):
    email, _, raw = await registered_and_logged_in(client)
    rows = await db(
        "SELECT t.token_hash FROM refresh_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE u.email = :e",
        e=email,
    )
    assert [r.token_hash for r in rows] == [hashlib.sha256(raw.encode()).hexdigest()]
    assert raw not in rows[0].token_hash


async def test_sai_mat_khau_va_email_khong_ton_tai_cho_ket_qua_giong_het_nhau(client):
    email, _, _ = await registered_and_logged_in(client)
    wrong_password = await login(client, email, "SaiMatKhau1")
    unknown_email = await login(client, new_email())
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]
    assert "set-cookie" not in wrong_password.headers


async def test_dang_nhap_khong_phan_biet_hoa_thuong_email(client):
    email, _, _ = await registered_and_logged_in(client)
    r = await login(client, email.upper().replace("@EXAMPLE.COM", "@example.com"))
    assert r.status_code == 200


async def test_tai_khoan_bi_khoa(client, db):
    email, _, _ = await registered_and_logged_in(client)
    await db("UPDATE users SET is_active = false WHERE email = :e", e=email)
    r = await login(client, email)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_DISABLED"
    # sai mật khẩu thì vẫn là 401 chung chung, không lộ tài khoản bị khóa
    assert (await login(client, email, "SaiMatKhau1")).status_code == 401


async def test_moi_lan_dang_nhap_la_mot_family_rieng(client, db):
    email, _, _ = await registered_and_logged_in(client)
    await login(client, email)
    rows = await db(
        "SELECT DISTINCT t.family_id FROM refresh_tokens t JOIN users u ON u.id=t.user_id "
        "WHERE u.email=:e",
        e=email,
    )
    assert len(rows) == 2


# ------------------------------------------------------------------ AU-4: xoay vòng refresh token


async def test_refresh_xoay_vong_va_cung_family(client, db):
    email, _, r1 = await registered_and_logged_in(client)
    resp = await post_refresh(client, r1)
    assert resp.status_code == 200
    r2 = refresh_cookie(resp)["refresh_token"].value
    assert r2 != r1
    claims = decode_token(resp.json()["access_token"], expected_type="access")
    assert claims["roles"] == ["PLAYER"]

    rows = await db(
        "SELECT t.token_hash, t.family_id, t.revoked_at FROM refresh_tokens t "
        "JOIN users u ON u.id=t.user_id WHERE u.email=:e",
        e=email,
    )
    by_hash = {r.token_hash: r for r in rows}
    old = by_hash[hashlib.sha256(r1.encode()).hexdigest()]
    new = by_hash[hashlib.sha256(r2.encode()).hexdigest()]
    assert old.revoked_at is not None  # token cũ đã bị vô hiệu
    assert new.revoked_at is None
    assert old.family_id == new.family_id


async def test_refresh_thieu_cookie_hoac_token_rac_bi_tu_choi(client):
    no_cookie = await client.post(REFRESH)
    garbage = await post_refresh(client, "khong-phai-token-that")
    for r in (no_cookie, garbage):
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "REFRESH_TOKEN_INVALID"


async def test_refresh_that_bai_thi_xoa_cookie(client):
    r = await post_refresh(client, "khong-phai-token-that")
    morsel = refresh_cookie(r)["refresh_token"]
    assert morsel.value == "" and int(morsel["max-age"]) == 0
    assert morsel["path"] == "/api/v1/auth"


async def test_refresh_token_het_han_bi_tu_choi(client, db):
    email, _, r1 = await registered_and_logged_in(client)
    await db(
        "UPDATE refresh_tokens SET expires_at = now() - interval '1 minute' "
        "WHERE user_id = (SELECT id FROM users WHERE email = :e)",
        e=email,
    )
    r = await post_refresh(client, r1)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "REFRESH_TOKEN_INVALID"


# ------------------------------------------------------------------ AU-5: phát hiện dùng lại


async def test_dung_lai_refresh_token_cu_thu_hoi_ca_family(client, db):
    email, _, r1 = await registered_and_logged_in(client)

    ok = await post_refresh(client, r1)  # R1 → R2
    r2 = refresh_cookie(ok)["refresh_token"].value

    reuse = await post_refresh(client, r1)  # kẻ cắp (hoặc lỗi) gửi lại R1
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"

    after = await post_refresh(client, r2)  # R2 hợp lệ nhưng cùng family → cũng bị thu hồi
    assert after.status_code == 401

    (row,) = await db(
        "SELECT count(*) FILTER (WHERE t.revoked_at IS NULL) AS active "
        "FROM refresh_tokens t JOIN users u ON u.id=t.user_id WHERE u.email=:e",
        e=email,
    )
    assert row.active == 0  # việc thu hồi đã được COMMIT dù request trả 401


async def test_thu_hoi_family_khong_anh_huong_phien_dang_nhap_khac(client):
    email, _, r1 = await registered_and_logged_in(client)
    other = refresh_cookie(await login(client, email))["refresh_token"].value  # thiết bị thứ hai

    await post_refresh(client, r1)
    await post_refresh(client, r1)  # reuse → chỉ family của r1 bị thu hồi

    assert (await post_refresh(client, other)).status_code == 200


async def test_hai_refresh_dong_thoi_chi_mot_thanh_cong(client, db):
    email, _, r1 = await registered_and_logged_in(client)
    results = await asyncio.gather(post_refresh(client, r1), post_refresh(client, r1))
    assert sorted(r.status_code for r in results) == [200, 401]  # không bao giờ có 2 token con

    (row,) = await db(
        "SELECT count(*) FILTER (WHERE t.revoked_at IS NULL) AS active "
        "FROM refresh_tokens t JOIN users u ON u.id=t.user_id WHERE u.email=:e",
        e=email,
    )
    assert row.active <= 1


async def test_tai_khoan_bi_khoa_thi_refresh_bi_tu_choi_va_thu_hoi_family(client, db):
    email, _, r1 = await registered_and_logged_in(client)
    await db("UPDATE users SET is_active = false WHERE email = :e", e=email)
    r = await post_refresh(client, r1)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_DISABLED"
    (row,) = await db(
        "SELECT count(*) FILTER (WHERE t.revoked_at IS NULL) AS active "
        "FROM refresh_tokens t JOIN users u ON u.id=t.user_id WHERE u.email=:e",
        e=email,
    )
    assert row.active == 0
