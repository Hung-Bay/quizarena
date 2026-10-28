"""Đặt biến môi trường mặc định TRƯỚC khi import app (Settings đọc lúc import).

Giá trị trong môi trường thật (CI, .env) luôn được ưu tiên nhờ setdefault.
"""

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://quizarena:test@localhost:5432/quizarena"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
