# ---------- 阶段1：构建依赖 ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /code

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# ---------- 阶段2：运行 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/code/.venv/bin:$PATH"

WORKDIR /code

COPY --from=builder /code/.venv .venv
COPY ./app ./app
COPY alembic.ini .
COPY alembic ./alembic

EXPOSE 8000

CMD ["sh", "-c", "fastapi run --workers ${WORKERS:-1} app/main.py"]
