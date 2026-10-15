# Base images and uv are pinned by version and digest (multi-arch indexes), so a build never
# runs code nobody reviewed. Dependabot (.github/dependabot.yml) proposes updates.

# Stage 1: build the virtual environment
FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS builder

# uv from its official image instead of a downloaded install script. Keep the version in step
# with UV_VERSION in .github/workflows/ci.yml; Dependabot may not bump this COPY --from line.
COPY --from=ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /uvx /bin/

WORKDIR /app

# Compilers for any dependency without a musl wheel.
RUN apk add --no-cache build-base

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev
# Byte-compile the app too: the runtime filesystem is read-only.
RUN /app/.venv/bin/python -m compileall -q src alembic scripts \
    && chmod +x scripts/run.sh

# Stage 2: runtime
FROM python:3.14-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72

WORKDIR /app

RUN apk upgrade --no-cache && apk add --no-cache bash \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app -s /sbin/nologin app

# Owned by root (world-readable, not writable by uid 10001): a compromised process can read the
# code but can't rewrite it, and the filesystem is read-only at runtime anyway.
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER 10001:10001

EXPOSE 8001

# Liveness only (no database): Docker marks the container unhealthy if the process hangs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

ENTRYPOINT ["scripts/run.sh"]
