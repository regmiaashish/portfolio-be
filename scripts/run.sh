#!/bin/bash
set -e

# Migrations run once here, before Gunicorn forks its workers, and only when asked.
# Default off: an existing database must first be stamped onto the migration history
# (see docs/DB_MIGRATION_RUNBOOK.md), otherwise the first migration would fail.
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "Running database migrations..."
    alembic upgrade head
fi

# Origin TLS (docs/ORIGIN_TLS.md): with both paths set, Gunicorn serves HTTPS instead of HTTP
# on the same port. Gunicorn applies TLS to every bind, so it's one or the other.
TLS_ARGS=()
if [ -n "${TLS_CERT_FILE:-}" ] || [ -n "${TLS_KEY_FILE:-}" ]; then
    if [ -z "${TLS_CERT_FILE:-}" ] || [ -z "${TLS_KEY_FILE:-}" ]; then
        echo "TLS: set both TLS_CERT_FILE and TLS_KEY_FILE, or neither." >&2
        exit 1
    fi
    for file in "$TLS_CERT_FILE" "$TLS_KEY_FILE"; do
        if [ ! -r "$file" ]; then
            echo "TLS: can't read $file as uid $(id -u). Is the directory mounted" \
                "(docker-compose.tls.yaml) and the file readable by uid/gid 10001?" >&2
            exit 1
        fi
    done
    TLS_ARGS=(--certfile "$TLS_CERT_FILE" --keyfile "$TLS_KEY_FILE")
    echo "Serving HTTPS (origin certificate $TLS_CERT_FILE)."
fi

echo "Starting Portfolio API..."

exec gunicorn src.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers "${WEB_CONCURRENCY:-2}" \
    --bind "0.0.0.0:${PORT:-8001}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-5}" \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
    --worker-tmp-dir /dev/shm \
    --no-control-socket \
    --access-logfile - \
    --error-logfile - \
    "${TLS_ARGS[@]}"
