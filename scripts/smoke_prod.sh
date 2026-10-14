#!/usr/bin/env bash
# Post-deploy smoke test (Phase 0 register lock-down + Phase 1 routes).
#
# Read-only by design. It sends:
#   - GET /health          (liveness, no database)
#   - GET /health/ready    (readiness: the API can reach its database)
#   - GET /openapi.json    (inspects the RegisterRequest schema; skipped if docs are off)
#   - POST /api/v1/auth/register with an EMPTY body {} and NO cookies
#
# The empty POST cannot create anything on either version:
#   fixed version -> 401 (auth is checked before the body)
#   old version   -> 422 (body validation fails), meaning the fix is NOT deployed
# This script never sends a valid registration body.
#
# Usage: scripts/smoke_prod.sh [BASE_URL]
#        default BASE_URL: https://api.aashish-regmi.com.np

set -uo pipefail

BASE_URL="${1:-https://api.aashish-regmi.com.np}"
BASE_URL="${BASE_URL%/}"
CURL=(curl --silent --show-error --max-time 15 --proto '=https,http' --no-buffer)

failures=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; failures=$((failures + 1)); }
info() { printf '  INFO  %s\n' "$1"; }

body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

echo "Smoke test against ${BASE_URL}"

# 1. Service is up, and can reach its database.
echo "[1] GET /health and /health/ready"
status="$("${CURL[@]}" -o "$body_file" -w '%{http_code}' "${BASE_URL}/health")" || status="000"
if [ "$status" = "200" ]; then pass "GET /health returned 200"; else fail "GET /health returned ${status}"; fi
status="$("${CURL[@]}" -o "$body_file" -w '%{http_code}' "${BASE_URL}/health/ready")" || status="000"
case "$status" in
  200) pass "GET /health/ready returned 200 (database reachable)" ;;
  503) fail "GET /health/ready returned 503: the API cannot reach its database" ;;
  *)   fail "GET /health/ready returned ${status}" ;;
esac

# 2. The deployed schema no longer accepts privilege fields.
echo "[2] GET /openapi.json"
status="$("${CURL[@]}" -o "$body_file" -w '%{http_code}' "${BASE_URL}/openapi.json")" || status="000"
if [ "$status" = "200" ]; then
  verdict="$(python3 - "$body_file" <<'EOF'
import json, sys
try:
    spec = json.load(open(sys.argv[1]))
    schema = spec["components"]["schemas"]["RegisterRequest"]
except Exception as exc:
    print(f"UNKNOWN could not read RegisterRequest schema: {exc}")
    sys.exit()
props = set(schema.get("properties", {}))
leaked = sorted(props & {"is_superuser", "is_active"})
if leaked:
    print(f"FAIL RegisterRequest still exposes {leaked}")
elif schema.get("additionalProperties") is False:
    print("PASS RegisterRequest has no privilege fields and forbids extra fields")
else:
    print("FAIL RegisterRequest does not forbid extra fields")
EOF
)"
  case "$verdict" in
    PASS*) pass "${verdict#PASS }" ;;
    FAIL*) fail "${verdict#FAIL }" ;;
    *)     info "${verdict#UNKNOWN }" ;;
  esac
else
  info "openapi.json returned ${status} (docs may be disabled); skipping schema check"
fi

# 3. Register refuses anonymous callers. Empty body only; never a valid one.
echo "[3] POST /api/v1/auth/register with empty body {} and no cookies"
status="$("${CURL[@]}" -o "$body_file" -w '%{http_code}' \
  -X POST "${BASE_URL}/api/v1/auth/register" \
  -H 'Content-Type: application/json' \
  --data '{}')" || status="000"
case "$status" in
  401) pass "register returned 401 for an anonymous caller (fix is deployed)" ;;
  422) fail "register returned 422: body validated before auth, so the OLD unprotected version is deployed" ;;
  404) fail "register returned 404: Phase 1 routes not deployed (old path was /auth/api/v1/register)" ;;
  *)   fail "register returned unexpected status ${status}: $(head -c 200 "$body_file")" ;;
esac

echo
if [ "$failures" -eq 0 ]; then
  echo "All smoke checks passed."
else
  echo "${failures} check(s) failed."
fi
exit "$failures"
