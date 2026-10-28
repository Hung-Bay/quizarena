ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------------------
# Stage 1: builder — cài thư viện Python bằng uv
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

# Ghim phiên bản uv để build lặp lại được (cập nhật khi nâng cấp uv)
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Chỉ cần pyproject.toml + uv.lock để cài thư viện.
# Layer này được cache, chỉ build lại khi hai file này đổi.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# Stage 2: runtime — chỉ chứa venv + mã nguồn, không có uv, không có công cụ build
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

# User không phải root (uid cố định để dễ phân quyền volume về sau)
RUN groupadd --system --gid 10001 app \
    && useradd  --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
# Mã nguồn: app/, alembic/, alembic.ini, ... (những gì .dockerignore không loại)
COPY --chown=app:app . /app

USER app

EXPOSE 8000

# Kiểm tra sức khỏe bằng Python có sẵn (image slim không có curl).
# Mặc định kiểm tra endpoint của service api. Service khác ghi đè bằng biến HEALTH_URL
# hoặc `healthcheck:` trong docker-compose.yml (ví dụ worker không có HTTP).
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request as u; sys.exit(0 if u.urlopen(os.environ.get('HEALTH_URL', 'http://127.0.0.1:8000/api/v1/healthz'), timeout=2).status == 200 else 1)"]

# Mặc định chạy api. realtime và worker ghi đè `command` trong compose.
CMD ["uvicorn", "app.main_api:app", "--host", "0.0.0.0", "--port", "8000"]
