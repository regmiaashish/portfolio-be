#!/bin/bash
set -e

# Migrations run once here, before Gunicorn forks its workers, and only when asked.
# Default off: an existing database must first be stamped onto the migration history
# (see docs/DB_MIGRATION_RUNBOOK.md), otherwise the first migration would fail.
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "Running database migrations..."
    alembic upgrade head
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
    --access-logfile - \
    --error-logfile -