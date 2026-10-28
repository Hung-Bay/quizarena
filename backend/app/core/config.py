from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env còn chứa POSTGRES_USER/PASSWORD/DB, REDIS_PASSWORD (docker compose dùng,
        # app không cần) → bỏ qua biến lạ thay vì báo lỗi.
        extra="ignore",
        # Biến để trống (vd GEMINI_RPM=) coi như chưa đặt, dùng giá trị mặc định/None.
        env_ignore_empty=True,
        # Lỗi validate không in giá trị đầu vào → không lộ secret vào log.
        hide_input_in_errors=True,
    )

    # --- Chung ---
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- Kết nối dữ liệu ---
    database_url: SecretStr
    redis_url: SecretStr

    # --- JWT ---
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = Field(default=15, gt=0)
    refresh_token_days: int = Field(default=7, gt=0)

    # --- Gemini (để trống đến Phase 2) ---
    gemini_api_key: SecretStr | None = None
    gemini_model_quiz: str | None = None
    gemini_model_report: str | None = None
    gemini_rpm: int | None = Field(default=None, gt=0)
    gemini_rpd: int | None = Field(default=None, gt=0)
    ai_generations_per_host_per_day: int = Field(default=5, gt=0)

    @model_validator(mode="after")
    def _no_placeholder_secrets_in_prod(self) -> "Settings":
        if self.app_env == "prod":
            for name in ("database_url", "redis_url", "jwt_secret"):
                if "change-me" in getattr(self, name).get_secret_value():
                    raise ValueError(f"{name.upper()} vẫn là giá trị mẫu 'change-me'")
        return self


@lru_cache
def get_settings() -> Settings:
    """Tạo Settings một lần rồi dùng lại (cache)."""
    return Settings()
