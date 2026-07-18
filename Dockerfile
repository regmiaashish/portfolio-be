# Stage 1: Build Stage
FROM python:3.12-alpine AS builder

WORKDIR /app

RUN apk add --no-cache curl ca-certificates build-base

ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh

ENV PATH="/root/.local/bin/:$PATH"
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Stage 2: Runtime Stage
FROM python:3.12-alpine

WORKDIR /app

RUN apk upgrade --no-cache && apk add --no-cache bash

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

RUN chmod +x scripts/run.sh

ENTRYPOINT ["scripts/run.sh"]