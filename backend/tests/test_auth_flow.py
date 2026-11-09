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
LOGOUT = "/api/v1/auth/logout"
LOGOUT_ALL = "/api/v1/auth/logout-all"
CHANGE_PASSWORD = "/api/v1/auth/change-password"
ME = "/api/v1/auth/me"


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


async def logout(client, access_token=None, refresh_token=None):
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if refresh_token:
        headers["Cookie"] = f"refresh_token={refresh_token}"
    return await client.post(LOGOUT, headers=headers)


async def logout_all(client, access_token):
    return await client.post(LOGOUT_ALL, headers={"Authorization": f"Bearer {access_token}"})


async def change_password(client, access_token, old_password, new_password):
    return await client.post(
        CHANGE_PASSWORD,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"old_password": old_password, "new_password": new_password},
    )


async def get_me(client, access_token=None):
    headers = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return await client.get(ME, headers=headers)


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


# ------------------------------------------------------------------ AU-6: logout + denylist jti


async def test_get_me_thanh_cong(client):
    email, resp, _ = await registered_and_logged_in(client)
    acc = resp.json()["access_token"]
    me_resp = await get_me(client, acc)
    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["email"] == email
    assert body["roles"] == ["PLAYER"]


async def test_get_me_thieu_token_hoac_token_sai(client):
    no_token = await get_me(client)
    assert no_token.status_code == 401
    assert no_token.json()["error"]["code"] == "TOKEN_MISSING"

    bad_header = await client.get(ME, headers={"Authorization": "SaiDinhDang abc"})
    assert bad_header.status_code == 401
    assert bad_header.json()["error"]["code"] == "TOKEN_INVALID"

    garbage = await get_me(client, "khong-phai-jwt")
    assert garbage.status_code == 401
    assert garbage.json()["error"]["code"] == "TOKEN_INVALID"


async def test_logout_dua_jti_vao_denylist_va_access_token_cu_bi_tu_choi(client):
    _, resp, ref = await registered_and_logged_in(client)
    acc = resp.json()["access_token"]

    # Đang dùng bình thường
    assert (await get_me(client, acc)).status_code == 200

    # Đăng xuất
    out_resp = await logout(client, access_token=acc, refresh_token=ref)
    assert out_resp.status_code == 200
    assert out_resp.json()["message"] == "Đăng xuất thành công"

    # Cookie đã bị xóa
    morsel = refresh_cookie(out_resp)["refresh_token"]
    assert morsel.value == "" and int(morsel["max-age"]) == 0

    # Token cũ dùng lại ngay lập tức bị từ chối 401 qua denylist Redis (AU-6)
    me_after = await get_me(client, acc)
    assert me_after.status_code == 401
    assert me_after.json()["error"]["code"] == "TOKEN_REVOKED"

    # Refresh token cũ cũng bị thu hồi (gửi lại token đã thu hồi kích hoạt reuse detection - AU-5)
    ref_after = await post_refresh(client, ref)
    assert ref_after.status_code == 401
    assert ref_after.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"


# ------------------------------------------------------------------ AU-7: logout-all & đổi mật khẩu


async def test_logout_all_tang_token_version_va_vo_hieu_moi_token_cu(client, db):
    email, resp1, ref1 = await registered_and_logged_in(client)
    acc1 = resp1.json()["access_token"]

    # Đăng nhập thiết bị thứ 2
    resp2 = await login(client, email)
    acc2 = resp2.json()["access_token"]
    ref2 = refresh_cookie(resp2)["refresh_token"].value

    # Gọi logout-all từ thiết bị 1
    r = await logout_all(client, acc1)
    assert r.status_code == 200
    assert r.json()["message"] == "Đã đăng xuất khỏi tất cả thiết bị"

    # Cả hai access token đều bị vô hiệu vì token_version đã tăng (AU-7)
    assert (await get_me(client, acc1)).status_code == 401
    assert (await get_me(client, acc2)).status_code == 401

    # Cả hai refresh token đều bị thu hồi
    assert (await post_refresh(client, ref1)).status_code == 401
    assert (await post_refresh(client, ref2)).status_code == 401

    # Kiểm tra token_version đã tăng trong DB
    (row,) = await db("SELECT token_version FROM users WHERE email = :e", e=email)
    assert row.token_version == 1

    (row_ref,) = await db(
        "SELECT count(*) FILTER (WHERE revoked_at IS NULL) AS active FROM refresh_tokens t "
        "JOIN users u ON u.id = t.user_id WHERE u.email = :e",
        e=email,
    )
    assert row_ref.active == 0


async def test_doi_mat_khau_thanh_cong_va_vo_hieu_moi_token_cu(client, db):
    email, resp, ref = await registered_and_logged_in(client)
    acc = resp.json()["access_token"]
    new_pass = "MatkhauMoi456"

    # Đổi mật khẩu
    r = await change_password(client, acc, PASSWORD, new_pass)
    assert r.status_code == 200
    assert r.json()["message"] == "Đổi mật khẩu thành công"

    # Access token cũ bị vô hiệu
    assert (await get_me(client, acc)).status_code == 401
    # Refresh token cũ bị vô hiệu
    assert (await post_refresh(client, ref)).status_code == 401

    # Mật khẩu cũ không đăng nhập được nữa
    assert (await login(client, email, PASSWORD)).status_code == 401

    # Mật khẩu mới đăng nhập thành công
    login_new = await login(client, email, new_pass)
    assert login_new.status_code == 200

    # token_version trong DB đã tăng
    (row,) = await db("SELECT token_version FROM users WHERE email = :e", e=email)
    assert row.token_version == 1


async def test_doi_mat_khau_sai_mat_khau_cu_bi_tu_choi(client):
    _, resp, _ = await registered_and_logged_in(client)
    acc = resp.json()["access_token"]

    r = await change_password(client, acc, "SaiMatKhau123", "MatkhauMoi456")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_PASSWORD"


async def test_doi_mat_khau_trung_mat_khau_cu_bi_tu_choi(client):
    _, resp, _ = await registered_and_logged_in(client)
    acc = resp.json()["access_token"]

    r = await change_password(client, acc, PASSWORD, PASSWORD)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SAME_PASSWORD"


async def test_doi_mat_khau_yeu_bi_tu_choi(client):
    _, resp, _ = await registered_and_logged_in(client)
    acc = resp.json()["access_token"]

    r = await change_password(client, acc, PASSWORD, "weak")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ AU-8: chống brute-force


async def test_brute_force_sai_5_lan_bi_khoa_tam(client):
    email, _, _ = await registered_and_logged_in(client)

    # Thử sai 5 lần liên tiếp
    for i in range(5):
        resp = await login(client, email, f"SaiMatKhau{i}")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"

    # Lần thứ 6 bị khóa tạm thời 429
    blocked = await login(client, email, "SaiMatKhauX")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"

    # Dù nhập đúng mật khẩu ở lần sau vẫn bị khóa tạm thời (AU-8)
    blocked_correct = await login(client, email, PASSWORD)
    assert blocked_correct.status_code == 429
    assert blocked_correct.json()["error"]["code"] == "RATE_LIMITED"


async def test_brute_force_email_khong_ton_tai_cho_thong_diep_va_hanh_vi_giong_het(client):
    fake_email = new_email()

    # Thử sai 5 lần với email không tồn tại
    for i in range(5):
        resp = await login(client, fake_email, f"SaiMatKhau{i}")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"
        assert resp.json()["error"]["message"] == "Email hoặc mật khẩu không đúng"

    # Lần thứ 6 với email không tồn tại cũng bị khóa 429 giống hệt
    blocked = await login(client, fake_email, "SaiMatKhauX")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


async def test_dang_nhap_thanh_cong_xoa_bo_dem_brute_force(client):
    email, _, _ = await registered_and_logged_in(client)

    # Thử sai 4 lần (< 5)
    for i in range(4):
        r = await login(client, email, f"SaiMatKhau{i}")
        assert r.status_code == 401

    # Lần thứ 5 nhập đúng mật khẩu -> thành công và reset bộ đếm
    ok = await login(client, email, PASSWORD)
    assert ok.status_code == 200

    # Lần tiếp theo thử sai -> vẫn trả 401 (chứ không bị khóa 429) vì bộ đếm đã được reset
    retry_wrong = await login(client, email, "SaiMatKhauMoi")
    assert retry_wrong.status_code == 401
    assert retry_wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
