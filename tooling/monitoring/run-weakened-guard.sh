#!/usr/bin/env bash
#
# Scheduled weakened-target guard: can the security suite's controls fail?
#
# Brings up a throwaway demo stack on this host, weakens it one configuration
# edit at a time (security/pentest/mutations/weakened.compose.yaml), and
# requires each control the edit should trip to report a finding
# (security/pentest/mutations/README.md, "The weakened target"). A control
# that stays green on a target known to be weakened is not evidence, and the
# weekly assessment (run-assessment.sh) reads its green as clean.
#
# It runs here, not on the staging target: it needs Docker, which this host
# has for GlitchTip and Uptime Kuma, and it touches no deployment at all. The
# stack is the published demo, its frontend published on loopback only, with
# its own compose project, and it is taken down again however the run ends.
#
# Exit code is the guard's: 0 every expected control reported its weakening,
# 1 one stayed green (a control that cannot fail), 2 nothing was established
# (a weakening that did not take, a suite that would not run). A stack that
# never came up is 2 as well, since nothing was assessed. Any non-zero pings
# /fail, so "the guard could not run" never reads as "the controls are fine".
# A run that finds another still in progress exits 75 and does nothing.
#
# Config (env vars, or the file run-assessment.sh reads):
#   WEAKENED_SUITE_DIR     local copy of security/pentest/ with its .venv
#                          (default CDN_SUITE_DIR, else
#                          /opt/pentest-runner/suite/pentest)
#   WEAKENED_DEMO_COMPOSE  docker-compose.demo.yaml of the same tag (default
#                          /opt/pentest-runner/docker-compose.demo.yaml)
#   WEAKENED_PORT          loopback port the frontend is published on
#                          (default 18080)
#   WEAKENED_PROJECT       compose project name (default mascope-weakened)
#   WEAKENED_START_TIMEOUT seconds the stack has to answer (default 1800; the
#                          first start downloads the ~150 MB demo bundle)
#   WEAKENED_HC_URL        heartbeat for this run (default none, never HC_URL:
#                          its pings must not keep the assessment's dead-man
#                          switch green)
#   MASCOPE_VERSION        the images to weaken (default latest); keep it the
#                          tag the suite copy was synced to
#   RECORDS                where logs and summaries are kept
#
# Leaves <ts>-weakened.log (the guard's output) and <ts>-weakened.summary (its
# verdict in one line) in RECORDS.
set -uo pipefail

CONFIG="${PENTEST_RUNNER_ENV:-/opt/pentest-runner/config.env}"
[ -r "$CONFIG" ] && . "$CONFIG"

WEAKENED_SUITE_DIR="${WEAKENED_SUITE_DIR:-${CDN_SUITE_DIR:-/opt/pentest-runner/suite/pentest}}"
WEAKENED_DEMO_COMPOSE="${WEAKENED_DEMO_COMPOSE:-/opt/pentest-runner/docker-compose.demo.yaml}"
WEAKENED_PORT="${WEAKENED_PORT:-18080}"
WEAKENED_PROJECT="${WEAKENED_PROJECT:-mascope-weakened}"
WEAKENED_START_TIMEOUT="${WEAKENED_START_TIMEOUT:-1800}"
WEAKENED_HC_URL="${WEAKENED_HC_URL:-}"
RECORDS="${RECORDS:-/mnt/md0/pentest-records}"

ping_hc() {  # $1 = path suffix ("/start", "/fail", ""), $2 = body
    [ -n "$WEAKENED_HC_URL" ] || return 0
    curl -fsS -m 10 --retry 3 --data-binary "${2:-}" "${WEAKENED_HC_URL}$1" >/dev/null 2>&1 || true
}

# Its own lock: this run spends nothing of the staging target's, so it neither
# waits for an assessment nor holds one up. Two of these at once would share
# one compose project and recreate each other's frontend.
exec 8>>"${RECORDS}/.weakened.lock"
if ! flock -n 8; then
    echo "weakened guard: a run is in progress"
    exit 75
fi

ts="$(date -u +%Y-%m-%dT%H%M%SZ)"
log="${RECORDS}/${ts}-weakened.log"
ping_hc "/start"

# The guard recreates the frontend and the backend with these too, so they are
# exported rather than given to the first command alone: a recreate without the
# port would publish it on the demo's default, on every interface. Loopback
# only, because the stack is weakened on purpose and its login is published.
export MASCOPE_HTTP_PORT="127.0.0.1:${WEAKENED_PORT}"
export MASCOPE_PENTEST_WEAKEN=""
export MASCOPE_PENTEST_WEAKEN_BACKEND=""
export MASCOPE_VERSION="${MASCOPE_VERSION:-latest}"
compose=(docker compose -p "$WEAKENED_PROJECT"
         -f "$WEAKENED_DEMO_COMPOSE"
         -f "${WEAKENED_SUITE_DIR}/mutations/weakened.compose.yaml")
target="http://127.0.0.1:${WEAKENED_PORT}"

# Down, not down -v: the volumes keep the downloaded bundle and the restored
# database, which the demo's init step reuses on the next start. Nothing in
# them stays weakened: the frontend's edits are made in its container, which
# down removes, and the backend's to the env's config layer, which the guard
# puts back when it restores the healthy stack and the backend's next start
# puts back if a run was cut short.
trap '"${compose[@]}" down >> "$log" 2>&1' EXIT

finish() {  # $1 = exit code, $2 = summary
    printf '%s\n' "$2" > "${RECORDS}/${ts}-weakened.summary"
    if [ "$1" -eq 0 ]; then
        ping_hc "" "$2"
    else
        ping_hc "/fail" "guard exit $1 | $2"
    fi
    find "$RECORDS" -maxdepth 1 -name '*-weakened.*' -mtime +400 -delete 2>/dev/null
    exit "$1"
}

{
    "${compose[@]}" pull -q && "${compose[@]}" up -d
} > "$log" 2>&1 || finish 2 "stack did not start (see ${ts}-weakened.log)"

deadline=$(( $(date +%s) + WEAKENED_START_TIMEOUT ))
until curl -fsS -o /dev/null -m 10 "$target/"; do
    [ "$(date +%s)" -lt "$deadline" ] \
        || finish 2 "stack did not answer within ${WEAKENED_START_TIMEOUT}s"
    sleep 10
done

# The staging credentials and the edge's header from the config file are not
# this run's: the controls that sign in use the demo's published login (the
# suite's default when these are empty), and the demo is not behind the edge.
# Cleared, so nothing of staging's reaches the demo stack.
(
    cd "$WEAKENED_SUITE_DIR" || exit 2
    MASCOPE_PENTEST_EMAIL= \
    MASCOPE_PENTEST_PASSWORD= \
    MASCOPE_PENTEST_LOWPRIV_EMAIL= \
    MASCOPE_PENTEST_LOWPRIV_PASSWORD= \
    MASCOPE_PENTEST_HEADER_NAME= \
    MASCOPE_PENTEST_HEADER_VALUE= \
    MASCOPE_PENTEST_VANTAGE= \
    exec ./.venv/bin/python -m mutations.run_weakened \
        --target "$target" \
        --compose-file "$WEAKENED_DEMO_COMPOSE" \
        --project "$WEAKENED_PROJECT"
) >> "$log" 2>&1
rc=$?

# Counts only: the guard's own last lines, which name control and weakening
# ids and nothing of any deployment.
summary="$(grep -E '^detected [0-9]+  missed [0-9]+  unproven [0-9]+$' "$log" | tail -1)"
finish "$rc" "${summary:-guard printed no verdict} version=${MASCOPE_VERSION}"
