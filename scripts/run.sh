#!/bin/bash
set -e

# No need to run migrations in this script since we are not using alembic for migrations for now
# echo "Running database migrations..."  
# alembic upgrade head

echo "Starting Portfolio API..."

exec gunicorn src.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers "${WEB_CONCURRENCY:-2}" \
    --bind "0.0.0.0:${PORT:-8001}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --keep-alive "${GUNICORN_KEEPALIVE:-5}" \
    --access-logfile - \
    --error-logfile -