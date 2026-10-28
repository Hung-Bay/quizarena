import pytest
from pydantic import ValidationError

from app.core.config import Settings

BASE = {
    "database_url": "postgresql+asyncpg://u:p@postgres:5432/d",
    "redis_url": "redis://:p@redis:6379/0",
    "jwt_secret": "x" * 40,
}


def test_jwt_secret_qua_ngan_bi_tu_choi():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**BASE, "jwt_secret": "short"})


def test_prod_tu_choi_gia_tri_mau_change_me():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, **{**BASE, "app_env": "prod", "jwt_secret": "change-me-" + "x" * 30}
        )


def test_bien_gemini_de_trong_la_none():
    s = Settings(_env_file=None, gemini_rpm=None, **BASE)
    assert s.gemini_rpm is None and s.gemini_api_key is None
