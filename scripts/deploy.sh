#!/usr/bin/env bash
# Deploy the API on the EC2 VM from the image CI published. The VM never builds (Phase 8).
#
#   scripts/deploy.sh                 the latest main: ghcr.io/regmiaashish/portfolio-be:sha-<commit>
#   scripts/deploy.sh sha-1a2b3c4     a specific published image (also how you roll back)
#   scripts/deploy.sh --no-pull       redeploy the image already in .env (configuration changes)
#   scripts/deploy.sh --build-local   build on the VM instead (fallback; needs the swap file)
#
# One deploy at a time -> refuses local edits -> git pull (not with --no-pull) -> waits for and
# pulls the image -> checks it before anything changes, all with the NEW image: compose config,
# the TLS certificate and key (when TLS is on), migrations -> points API_IMAGE in .env at it ->
# recreates the container -> waits for healthy -> smoke tests. If anything fails after the
# switch, it points API_IMAGE back at the exact image that was running (by image ID), recreates,
# and says so. Never prints .env values: it reads only API_HOST_PORT, API_IMAGE and whether
# TLS_CERT_FILE is set.
set -euo pipefail

cd "$(dirname "$0")/.."

# .env decides these, not whatever happens to be exported in this shell.
unset API_IMAGE API_HOST_PORT COMPOSE_FILE TLS_HOST_DIR

IMAGE_REPO="${DEPLOY_IMAGE_REPO:-ghcr.io/regmiaashish/portfolio-be}"
ACTIONS_URL="https://github.com/regmiaashish/portfolio-be/actions"
API_HOST="api.aashish-regmi.com.np"
CONTAINER="portfolio-server"
LOCAL_REPO="portfolio-be-api"
LOCK_FILE="${DEPLOY_LOCK_FILE:-/tmp/portfolio-be-deploy.lock}"
HEALTH_WAIT_SECONDS="${DEPLOY_HEALTH_WAIT_SECONDS:-90}"
PUBLISH_WAIT_SECONDS="${DEPLOY_PUBLISH_WAIT_SECONDS:-600}"
PUBLISH_POLL_SECONDS=30
KEEP_IMAGES=3

DOCKER=(docker)
SUDO=()
if [ "$(id -u)" -ne 0 ]; then
    DOCKER=(sudo docker)
    SUDO=(sudo)
fi

usage() {
    sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
}

fail() {
    echo "DEPLOY FAILED: $*" >&2
    exit 1
}

MODE=pull
TAG=""
case "${1:-}" in
    "") ;;
    --build-local) MODE=build ;;
    --no-pull) MODE=current ;;
    -h | --help)
        usage
        exit 0
        ;;
    -*)
        usage >&2
        exit 2
        ;;
    *) TAG="$1" ;;
esac
if [ $# -gt 1 ]; then
    usage >&2
    exit 2
fi
if [ -n "$TAG" ] && ! [[ "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    fail "'$TAG' is not an image tag (expected something like sha-1a2b3c4)"
fi

# One deploy at a time: a second one would fight over .env and the container.
exec 9>>"$LOCK_FILE"
flock -n 9 || fail "another deploy is already running (lock: $LOCK_FILE)"

# .env may be root-owned with mode 600; read and write it with sudo then.
read_env() {
    if [ -r .env ]; then cat .env; else "${SUDO[@]}" cat .env; fi
}
env_value() {
    read_env | grep -E "^$1=" | tail -n 1 | cut -d= -f2- || true
}
# Adds a missing final newline first, so the new line can't be glued onto the last setting.
set_env() {
    # shellcheck disable=SC2016 # expanded by the inner shell
    local script='[ -z "$(tail -c1 .env)" ] || echo >> .env; sed -i "/^$1=/d" .env; printf "%s=%s\n" "$1" "$2" >> .env'
    if [ -w .env ]; then
        sh -c "$script" sh "$1" "$2"
    else
        "${SUDO[@]}" sh -c "$script" sh "$1" "$2"
    fi
}

[ -f .env ] || fail ".env is missing in $(pwd)"
port_value="$(env_value API_HOST_PORT)"
[[ "$port_value" =~ ^([0-9]+)[[:space:]]*$ ]] \
    || fail "API_HOST_PORT must be set to a port number in .env (80, or 443 with origin TLS).
       See docs/EC2_DEPLOYMENT_CHECKLIST.md."
HOST_PORT="${BASH_REMATCH[1]}"
TLS_ON=0
SCHEME=http
CURL_TLS=()
if [ -n "$(env_value TLS_CERT_FILE)" ]; then
    TLS_ON=1
    SCHEME=https
    CURL_TLS=(-k) # the origin certificate is for the public hostname, not 127.0.0.1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "tracked files have local changes (git status). Settings belong in .env, not in tracked files."
fi

# docker compose with API_IMAGE set for this one command (the environment wins over .env), so
# the new image can be checked and can migrate the database before .env or the container change.
compose_new() {
    "${SUDO[@]}" env "API_IMAGE=$TARGET" docker compose "$@"
}

# Prints the database's current Alembic revision, as seen by the new image. Its errors are
# shown (not swallowed), and a failure is the caller's to report.
alembic_revision() {
    local out err
    err="$(mktemp)"
    if ! out="$(compose_new run --rm --entrypoint alembic api current 2>"$err")"; then
        cat "$err" >&2
        rm -f "$err"
        return 1
    fi
    rm -f "$err"
    awk 'NF {revision=$1} END {print revision}' <<<"$out"
}

health_status() {
    "${DOCKER[@]}" inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing
}

# Polls the container's HEALTHCHECK; prints the last status, succeeds only on "healthy".
wait_healthy() {
    local status=unknown
    for _ in $(seq $((HEALTH_WAIT_SECONDS / 3))); do
        status="$(health_status)"
        [ "$status" = healthy ] && break
        sleep 3
    done
    echo "$status"
    [ "$status" = healthy ]
}

recreate() {
    "${DOCKER[@]}" compose up -d --no-build --force-recreate api
}

# The command that undoes this deploy's migrations. It must run with the NEW image: only that
# image's alembic/versions contains the revision the database is now at.
downgrade_command() {
    local prefix=""
    [ "${#SUDO[@]}" -gt 0 ] && prefix="sudo "
    echo "${prefix}env API_IMAGE=$TARGET docker compose run --rm --entrypoint alembic api downgrade $PREVIOUS_REVISION"
}

# What is running now, by image ID: the exact image to go back to if this deploy fails. A name
# like portfolio-be-api (a local build) may already point at a different image.
PREVIOUS_ID="$("${DOCKER[@]}" inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null || true)"
PREVIOUS_NAME="$("${DOCKER[@]}" inspect -f '{{.Config.Image}}' "$CONTAINER" 2>/dev/null || true)"
if [ -n "$PREVIOUS_ID" ]; then
    if [[ "$PREVIOUS_NAME" == "$IMAGE_REPO:sha-"* ]] \
        && [ "$("${DOCKER[@]}" image inspect -f '{{.Id}}' "$PREVIOUS_NAME" 2>/dev/null || true)" = "$PREVIOUS_ID" ]; then
        ROLLBACK_IMAGE="$PREVIOUS_NAME" # a published sha-<commit> tag never moves
    else
        # Named after its own ID, so it can never be re-pointed at another image.
        id_hex="${PREVIOUS_ID#sha256:}"
        ROLLBACK_IMAGE="$LOCAL_REPO:pre-${id_hex:0:12}"
        "${DOCKER[@]}" tag "$PREVIOUS_ID" "$ROLLBACK_IMAGE"
    fi
else
    ROLLBACK_IMAGE="$(env_value API_IMAGE)"
    [ -n "$ROLLBACK_IMAGE" ] || ROLLBACK_IMAGE="$LOCAL_REPO:latest"
fi

TARGET=""
PREVIOUS_REVISION=""
CURRENT_REVISION=""
upgraded=0
switched=0
recreated=0
ok=0

roll_back() {
    if [ "$ok" = 1 ]; then
        return
    fi
    set +e
    if [ "$switched" = 1 ]; then
        echo >&2
        echo "==> Rolling back: API_IMAGE -> $ROLLBACK_IMAGE" >&2
        set_env API_IMAGE "$ROLLBACK_IMAGE"
        if [ "$recreated" = 1 ]; then
            recreate >&2
            local status
            status="$(wait_healthy)"
            if [ "$status" = healthy ]; then
                echo "    The previous image is running again and healthy." >&2
            else
                echo "    The previous image is '$status' too: check 'sudo docker compose logs api'." >&2
            fi
        else
            echo "    The running container was never replaced; it is still the previous image." >&2
        fi
        if [ "$MODE" = current ]; then
            echo "    This was a configuration-only deploy (--no-pull): the new settings are still in" >&2
            echo "    .env. Restore your backup (for example: cp ~/env.backup-before-tls .env) and run" >&2
            echo "    scripts/deploy.sh --no-pull again." >&2
        fi
    fi
    if [ "$upgraded" = 1 ] && [ -n "$PREVIOUS_REVISION" ] \
        && [ "$PREVIOUS_REVISION" != "$CURRENT_REVISION" ]; then
        echo "    This deploy migrated the database from $PREVIOUS_REVISION to ${CURRENT_REVISION:-a newer revision}." >&2
        echo "    If the previous image can't run on the new schema, downgrade (with the new image," >&2
        echo "    which is the one that knows the new revision):" >&2
        echo "      $(downgrade_command)" >&2
    fi
}
trap roll_back EXIT

case "$MODE" in
    current)
        TARGET="$(env_value API_IMAGE)"
        [ -n "$TARGET" ] || TARGET="$LOCAL_REPO:latest"
        "${DOCKER[@]}" image inspect "$TARGET" >/dev/null 2>&1 \
            || fail "$TARGET (API_IMAGE in .env) isn't on this VM. Use scripts/deploy.sh [sha-<commit>] instead."
        echo "==> Redeploying $TARGET with the current checkout ($(git rev-parse --short HEAD)) and .env"
        echo "    (--no-pull: no git pull, no image pull)"
        ;;
    build)
        echo "==> Pulling the repository (at $(git rev-parse --short HEAD))"
        git pull --ff-only
        COMMIT="$(git rev-parse HEAD | cut -c1-7)"
        TARGET="$LOCAL_REPO:local-$COMMIT"
        echo "==> Building $TARGET on this VM"
        "${DOCKER[@]}" build -t "$TARGET" .
        ;;
    pull)
        echo "==> Pulling the repository (at $(git rev-parse --short HEAD))"
        git pull --ff-only
        COMMIT="$(git rev-parse HEAD | cut -c1-7)"
        echo "    now at $COMMIT"
        TARGET="$IMAGE_REPO:${TAG:-sha-$COMMIT}"
        echo "==> Pulling $TARGET"
        waited=0
        while true; do
            if out="$("${DOCKER[@]}" pull "$TARGET" 2>&1)"; then
                echo "$out" | tail -n 2
                break
            fi
            if grep -qiE 'denied|unauthorized|authentication required' <<<"$out"; then
                fail "the registry refused $TARGET. Either it isn't published yet ('Publish
       multi-arch image' must be green: $ACTIONS_URL) or the GHCR package is private (make it
       public, or 'sudo docker login ghcr.io'; docs/EC2_DEPLOYMENT_CHECKLIST.md section 15)."
            fi
            if ! grep -qiE 'not found|manifest unknown' <<<"$out"; then
                echo "$out" >&2
                fail "docker pull $TARGET failed"
            fi
            if [ -n "$TAG" ]; then
                fail "$TARGET doesn't exist. Published tags: GitHub -> regmiaashish -> Packages -> portfolio-be."
            fi
            if [ "$waited" -ge "$PUBLISH_WAIT_SECONDS" ]; then
                fail "no image for commit $COMMIT after ${PUBLISH_WAIT_SECONDS} s. CI publishes it only
       after every check passes on main: $ACTIONS_URL"
            fi
            echo "    not published yet (CI builds it after the checks pass); retrying in" \
                "${PUBLISH_POLL_SECONDS} s (waited ${waited} s of ${PUBLISH_WAIT_SECONDS} s)"
            sleep "$PUBLISH_POLL_SECONDS"
            waited=$((waited + PUBLISH_POLL_SECONDS))
        done
        ;;
esac

echo "==> Checking the new image before switching anything"
config="$(compose_new config)" || fail "docker compose config failed"
grep -Fq "image: $TARGET" <<<"$config" || fail "docker compose config doesn't use $TARGET"
grep -Eq "published: \"?${HOST_PORT}\"?[[:space:]]*$" <<<"$config" \
    || fail "docker compose config doesn't publish host port $HOST_PORT"
if [ "$TLS_ON" = 1 ]; then
    grep -Fq "target: /run/tls" <<<"$config" \
        || fail "TLS_CERT_FILE is set but the TLS directory isn't mounted. Add
       COMPOSE_FILE=docker-compose.yaml:docker-compose.tls.yaml to .env (docs/ORIGIN_TLS.md)."
    # Loads the pair exactly as Gunicorn will: as uid 10001, from the mount, in the new image.
    # Catches an unreadable key and a key that doesn't belong to the certificate.
    compose_new run --rm --entrypoint python api -c 'import os, ssl
ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(os.environ["TLS_CERT_FILE"], os.environ["TLS_KEY_FILE"])
print("    TLS certificate and key load fine")' \
        || fail "the new container can't load the TLS certificate and key (unreadable by uid 10001,
       or the key doesn't match the certificate). Nothing was changed. See docs/ORIGIN_TLS.md."
fi

echo "==> Applying database migrations with the new image"
PREVIOUS_REVISION="$(alembic_revision)" \
    || fail "'alembic current' failed with the new image (output above). Is the database reachable
       (DATABASE_URL in .env, Neon status)? If the database is at a revision this image doesn't
       know, it needs the newer image's downgrade first. Nothing was changed."
echo "    database is at ${PREVIOUS_REVISION:-no revision yet}"
compose_new run --rm --entrypoint alembic api upgrade head \
    || fail "'alembic upgrade head' failed (output above). Nothing was switched."
upgraded=1
CURRENT_REVISION="$(alembic_revision)" \
    || fail "'alembic current' failed after the upgrade (output above)."
echo "    database is now at ${CURRENT_REVISION:-an unknown revision}"

echo "==> Switching API_IMAGE to $TARGET (was ${PREVIOUS_NAME:-$ROLLBACK_IMAGE})"
switched=1
set_env API_IMAGE "$TARGET"

echo "==> Recreating the API container"
recreated=1
recreate

echo "==> Waiting up to ${HEALTH_WAIT_SECONDS} s for the container to report healthy"
if ! status="$(wait_healthy)"; then
    "${DOCKER[@]}" compose logs --tail=50 api >&2 || true
    fail "container is '$status' after ${HEALTH_WAIT_SECONDS} s"
fi

echo "==> Smoke test ($SCHEME on host port $HOST_PORT)"
port="$("${DOCKER[@]}" compose port api 8001 | awk -F: '{print $NF}')"
[ "$port" = "$HOST_PORT" ] || fail "the API is published on port '$port', expected $HOST_PORT"
base="$SCHEME://127.0.0.1:$port"
curl -fsS -m 20 "${CURL_TLS[@]}" -o /dev/null -H "Host: $API_HOST" "$base/health" || fail "/health"
ready="$(curl -sS -m 20 "${CURL_TLS[@]}" -o /dev/null -w '%{http_code}' -H "Host: $API_HOST" "$base/health/ready" || true)"
[ "$ready" = 200 ] || fail "/health/ready answered '$ready' (database unreachable?)"

ok=1
echo "==> Deployed $TARGET: healthy, /health and /health/ready OK"

# Keep the newest few images of each kind, never the one running or the rollback image, so a
# rollback stays a local switch and the disk doesn't fill up.
prune() {
    local repo="$1" keep="$2"
    "${DOCKER[@]}" image ls "$repo" --format '{{.Repository}}:{{.Tag}}' \
        | grep -v ':<none>$' | tail -n "+$((keep + 1))" \
        | while read -r image; do
            case "$image" in
                "$TARGET" | "$ROLLBACK_IMAGE") continue ;;
            esac
            "${DOCKER[@]}" image rm "$image" >/dev/null 2>&1 && echo "    removed old image $image"
        done || true
}
prune "$IMAGE_REPO" "$KEEP_IMAGES"
prune "$LOCAL_REPO" "$KEEP_IMAGES"

if [ "$ROLLBACK_IMAGE" != "$TARGET" ]; then
    if [[ "$ROLLBACK_IMAGE" == "$IMAGE_REPO:"* ]]; then
        echo "    To go back: scripts/deploy.sh ${ROLLBACK_IMAGE#"$IMAGE_REPO:"}"
    else
        echo "    The previous image is kept as $ROLLBACK_IMAGE. To go back to it: set"
        echo "    API_IMAGE=$ROLLBACK_IMAGE in .env, then scripts/deploy.sh --no-pull"
    fi
fi
