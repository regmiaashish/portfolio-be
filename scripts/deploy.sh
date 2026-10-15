#!/usr/bin/env bash
# Deploy the latest main on the EC2 VM: pull -> build -> migrate -> restart -> wait healthy -> smoke.
#
#   cd ~/portfolio-be && scripts/deploy.sh
#
# Never prints secrets: the only .env value it reads is API_HOST_PORT. Stops at the first
# failure and, once anything has changed, prints how to roll back.
set -euo pipefail

cd "$(dirname "$0")/.."

DOCKER=(docker)
SUDO=()
if [ "$(id -u)" -ne 0 ]; then
    DOCKER=(sudo docker)
    SUDO=(sudo)
fi
API_HOST="api.aashish-regmi.com.np"
CONTAINER="portfolio-server"
HEALTH_WAIT_SECONDS=90

fail() { echo "DEPLOY FAILED: $*" >&2; exit 1; }

# .env may be root-owned with mode 600; read it with sudo then.
read_env() {
    if [ -r .env ]; then cat .env; else "${SUDO[@]}" cat .env; fi
}

[ -f .env ] || fail ".env is missing in $(pwd)"
port_line="$(read_env | grep -E '^API_HOST_PORT=' | tail -n 1 || true)"
[[ "$port_line" =~ ^API_HOST_PORT=([0-9]+)[[:space:]]*$ ]] \
    || fail "API_HOST_PORT must be set to a port number in .env (the VM uses 80).
       See section 14 of docs/EC2_DEPLOYMENT_CHECKLIST.md."
HOST_PORT="${BASH_REMATCH[1]}"

if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "tracked files have local changes (git status). Phase 7 moved the port into .env:
       run 'git checkout -- docker-compose.yaml' if that is the only change."
fi

PREVIOUS="$(git rev-parse --short HEAD)"
PREVIOUS_REVISION=""
CURRENT_REVISION=""
changed=0
ok=0
rollback_hint() {
    if [ "$ok" = 1 ] || [ "$changed" = 0 ]; then
        return
    fi
    echo >&2
    if [ -n "$PREVIOUS_REVISION" ] && [ -n "$CURRENT_REVISION" ] \
        && [ "$PREVIOUS_REVISION" != "$CURRENT_REVISION" ]; then
        echo "This deploy migrated the database from $PREVIOUS_REVISION to $CURRENT_REVISION." >&2
        echo "If the old code can't run on the new schema, downgrade first:" >&2
        echo "  ${DOCKER[*]} compose run --rm --entrypoint alembic api downgrade $PREVIOUS_REVISION" >&2
    fi
    cat >&2 <<EOF
Roll back to the code that was running before ($PREVIOUS):
  git checkout $PREVIOUS && ${DOCKER[*]} compose up -d --build --force-recreate api
Then run 'git switch main' before the next deploy (checkout leaves a detached HEAD).
EOF
}

alembic_revision() {
    "${DOCKER[@]}" compose run --rm --entrypoint alembic api current 2>/dev/null \
        | awk 'NF {revision=$1} END {print revision}'
}
trap rollback_hint EXIT

echo "==> Pulling (currently at $PREVIOUS)"
changed=1
git pull --ff-only
echo "    now at $(git rev-parse --short HEAD)"

echo "==> Checking that the API will be published on host port $HOST_PORT"
"${DOCKER[@]}" compose config | grep -Eq "published: \"?${HOST_PORT}\"?[[:space:]]*$" \
    || fail "docker compose config does not publish port $HOST_PORT (check docker-compose.yaml)"

echo "==> Building the image"
"${DOCKER[@]}" compose build api

echo "==> Applying database migrations"
PREVIOUS_REVISION="$(alembic_revision)"
echo "    database was at ${PREVIOUS_REVISION:-an unknown revision}"
"${DOCKER[@]}" compose run --rm --entrypoint alembic api upgrade head
CURRENT_REVISION="$(alembic_revision)"
echo "    database is now at ${CURRENT_REVISION:-an unknown revision}"

echo "==> Restarting the API"
"${DOCKER[@]}" compose up -d --force-recreate api

echo "==> Waiting up to ${HEALTH_WAIT_SECONDS} s for the container to report healthy"
status="unknown"
for _ in $(seq $((HEALTH_WAIT_SECONDS / 3))); do
    status="$("${DOCKER[@]}" inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)"
    [ "$status" = healthy ] && break
    sleep 3
done
if [ "$status" != healthy ]; then
    "${DOCKER[@]}" compose logs --tail=50 api >&2
    fail "container is '$status' after ${HEALTH_WAIT_SECONDS} s"
fi

echo "==> Smoke test"
port="$("${DOCKER[@]}" compose port api 8001 | awk -F: '{print $NF}')"
[ "$port" = "$HOST_PORT" ] || fail "the API is published on port '$port', expected $HOST_PORT"
curl -fsS -o /dev/null -H "Host: $API_HOST" "http://127.0.0.1:${port}/health" || fail "/health"
ready="$(curl -sS -o /dev/null -w '%{http_code}' -H "Host: $API_HOST" "http://127.0.0.1:${port}/health/ready")"
[ "$ready" = 200 ] || fail "/health/ready answered $ready (database unreachable?)"

ok=1
echo "==> Deployed $(git rev-parse --short HEAD): healthy on port $port, /health and /health/ready OK"
