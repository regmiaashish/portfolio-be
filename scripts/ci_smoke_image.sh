#!/usr/bin/env bash
# CI only: start an image the way production runs it (read-only filesystem, no Linux
# capabilities, no privilege escalation) with dummy settings and no database, and check that it
# reports healthy, runs as uid 10001, and answers /health with the security headers.
#
#   scripts/ci_smoke_image.sh <image>          e.g. portfolio-api:ci or ghcr.io/...@sha256:...
set -euo pipefail

IMAGE="${1:?usage: scripts/ci_smoke_image.sh <image>}"
NAME="smoke-$$"
PORT="${SMOKE_PORT:-8001}"
WAIT_SECONDS="${SMOKE_WAIT_SECONDS:-90}"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8001" \
    --read-only --tmpfs /tmp:size=128m --cap-drop ALL --security-opt no-new-privileges:true \
    -e ENV=prod \
    -e DATABASE_URL=postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none \
    -e SMTP_HOST=localhost -e SMTP_USER=ci@example.com -e SMTP_PASSWORD=dummy \
    -e SUPER_ADMIN_EMAIL=ci@example.com -e PORTFOLIO_URL=https://example.com \
    -e GITHUB_URL=https://github.com/example -e LINKEDIN_URL=https://linkedin.com/in/example \
    -e JWT_SECRET_KEY=ci-dummy-secret-key-that-is-long-enough-123456 \
    "$IMAGE" >/dev/null

status=starting
for _ in $(seq $((WAIT_SECONDS / 2))); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$NAME")"
    [ "$status" = healthy ] && break
    [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || { status=exited; break; }
    sleep 2
done
if [ "$status" != healthy ]; then
    docker logs "$NAME" >&2 || true
    echo "FAIL: $IMAGE is '$status' after up to ${WAIT_SECONDS} s" >&2
    exit 1
fi

uid="$(docker exec "$NAME" id -u)"
[ "$uid" = 10001 ] || { echo "FAIL: runs as uid $uid, expected 10001" >&2; exit 1; }

headers="$(curl -fsS -m 10 -D - -o /dev/null "http://127.0.0.1:$PORT/health")"
for header in \
    "x-content-type-options: nosniff" \
    "content-security-policy: default-src 'none'" \
    "strict-transport-security:"; do
    grep -qiF "$header" <<<"$headers" || { echo "FAIL: missing header '$header'" >&2; exit 1; }
done

echo "OK: $IMAGE is healthy, runs as uid 10001, and sends the security headers"
