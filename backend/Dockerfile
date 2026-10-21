# ---------- 阶段1：构建依赖 ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_HTTP_TIMEOUT=300

WORKDIR /backend

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# ---------- 阶段2：运行 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/backend/.venv/bin:$PATH"

WORKDIR /backend

COPY --from=builder /backend/.venv .venv
COPY ./app ./app
COPY alembic.ini .
COPY alembic ./alembic
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["sh", "-c", "fastapi run --workers ${WORKERS:-4} app/main.py"]
