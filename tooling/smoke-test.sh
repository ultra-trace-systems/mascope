#!/usr/bin/env bash
# Smoke test a running Mascope stack: the frontend serves, the demo login
# authenticates, and the API answers authenticated reads with seeded data.
#
# Works against any stack with known credentials; defaults match the demo
# stack (docker-compose.demo.yaml). Used as the release gate in
# .github/workflows/release.yaml and runnable locally:
#
#   docker compose -f docker-compose.demo.yaml up -d
#   bash tooling/smoke-test.sh
#
# Environment:
#   SMOKE_BASE_URL  frontend origin        (default http://localhost:8080)
#   SMOKE_EMAIL     login email            (default demo@mascope.app)
#   SMOKE_PASSWORD  login password         (default mascope-demo)
#   SMOKE_TIMEOUT   seconds to wait for the stack (default 600)
#   SMOKE_INSECURE  set to 1 to skip TLS verification (a production stack
#                   serving the self-signed `mascope cert gen` certificate)
set -euo pipefail

BASE_URL=${SMOKE_BASE_URL:-http://localhost:8080}
EMAIL=${SMOKE_EMAIL:-demo@mascope.app}
PASSWORD=${SMOKE_PASSWORD:-mascope-demo}
TIMEOUT=${SMOKE_TIMEOUT:-600}

CURL=(curl -fsS)
if [ -n "${SMOKE_INSECURE:-}" ]; then
  CURL+=(-k)
fi

echo "[smoke] waiting for $BASE_URL (up to ${TIMEOUT}s)..."
elapsed=0
until "${CURL[@]}" -o /dev/null "$BASE_URL"; do
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "[smoke] FAIL: frontend did not answer within ${TIMEOUT}s" >&2
    exit 1
  fi
  sleep 10
  elapsed=$((elapsed + 10))
done
echo "[smoke] frontend is up after ~${elapsed}s"

echo "[smoke] frontend serves the app shell..."
"${CURL[@]}" "$BASE_URL" | grep -qi "<!doctype html" || {
  echo "[smoke] FAIL: response is not an HTML document" >&2
  exit 1
}

echo "[smoke] bundled docs are served at /docs/..."
"${CURL[@]}" "$BASE_URL/docs/" | grep -qi "Mascope" || {
  echo "[smoke] FAIL: /docs/ does not serve the documentation site" >&2
  exit 1
}
"${CURL[@]}" "$BASE_URL/docs/help-content.json" | grep -q "{" || {
  echo "[smoke] FAIL: /docs/help-content.json is missing (in-app help popovers)" >&2
  exit 1
}

echo "[smoke] login as $EMAIL..."
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
"${CURL[@]}" -c "$jar" -o /dev/null -X POST "$BASE_URL/api/auth/login" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=password' \
  --data-urlencode "username=$EMAIL" \
  --data-urlencode "password=$PASSWORD" || {
  echo "[smoke] FAIL: login rejected" >&2
  exit 1
}

echo "[smoke] authenticated API answers..."
me=$("${CURL[@]}" -b "$jar" "$BASE_URL/api/users/me")
echo "$me" | grep -q "$EMAIL" || {
  echo "[smoke] FAIL: /api/users/me does not identify $EMAIL: $me" >&2
  exit 1
}

echo "[smoke] seeded data is present..."
workspaces=$("${CURL[@]}" -b "$jar" "$BASE_URL/api/workspaces")
echo "$workspaces" | grep -q '"workspace_id"' || {
  echo "[smoke] FAIL: /api/workspaces returned no workspaces: $workspaces" >&2
  exit 1
}

echo "[smoke] PASS"
