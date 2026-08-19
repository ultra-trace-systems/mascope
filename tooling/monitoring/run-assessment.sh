#!/usr/bin/env bash
#
# Scheduled Mascope security assessment, origin vantage.
#
# Runs the pen-test suite ON the staging target and brings the report back
# here. It runs from this host rather than on the target because a report
# stored on the system under test is a report a successful attacker can edit,
# and because the target is rebuilt from scratch as a matter of routine.
#
# The target is treated as hostile throughout, which is the whole premise of
# the box:
#   * the report is pulled as ONE stream, never a remote glob - a hostile
#     server chooses the filenames it sends, which would be a file-write
#     primitive on this host;
#   * the stream is size-bounded before it touches the disk, so the target
#     cannot fill the volume that holds the fleet's backups;
#   * the archive is never extracted to disk. Only the JSON is read, to
#     stdout, and parsed by a JSON parser - never a shell;
#   * nothing derived from the report is rendered, evaluated, or forwarded.
#
# Exit code is the suite's, so the heartbeat below reflects the assessment
# rather than the plumbing - with two deliberate overrides: an unusable
# report (1) and a target behind the current release (90). Both are cases
# where pytest succeeded and the run is still not evidence.
#
# Config (env vars, or an optional sourced file - see CONFIG below). The
# credentials live only in that file, never here:
#   TARGET_HOST          host running the assessed deployment (required)
#   TARGET_USER          unprivileged account on it (default pentest)
#   SSH_KEY              dedicated key; the account must not have sudo
#   SUITE_DIR            where security/pentest/ is deployed on the target
#   RECORDS              where logs, report archives and summaries are kept
#   MAX_BYTES            cap on the pulled report stream (default 20 MB)
#   HC_URL               optional healthchecks.io-style ping URL
#   RELEASE_API          release endpoint used by the lag check
#   RELEASE_TIMEOUT      lookup timeout; a failed lookup is never fatal
#   MASCOPE_PENTEST_*    target credentials, passed through to the suite
#
# Each run leaves three files in RECORDS: <ts>-origin.log (the suite's output),
# <ts>-origin.tar.gz (the report), and <ts>-origin.summary (this script's own
# verdict, so an archived run still states what it concluded).
set -uo pipefail

CONFIG="${PENTEST_RUNNER_ENV:-/opt/pentest-runner/config.env}"
[ -r "$CONFIG" ] && . "$CONFIG"

TARGET_HOST="${TARGET_HOST:?TARGET_HOST not set}"
TARGET_USER="${TARGET_USER:-pentest}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_staging_pentest}"
SUITE_DIR="${SUITE_DIR:-/home/pentest/suite/pentest}"
RECORDS="${RECORDS:-/mnt/md0/pentest-records}"
MAX_BYTES="${MAX_BYTES:-20000000}"      # 20 MB; a real report is ~10 KB
HC_URL="${HC_URL:-}"                     # healthchecks.io check, optional
RELEASE_API="${RELEASE_API:-https://api.github.com/repos/ultra-trace-systems/mascope/releases/latest}"
RELEASE_TIMEOUT="${RELEASE_TIMEOUT:-10}"   # lookup only; never fatal

SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o ForwardAgent=no
          -o BatchMode=yes -o StrictHostKeyChecking=accept-new
          -o ConnectTimeout=20)

ts="$(date -u +%Y-%m-%dT%H%M%SZ)"
archive="${RECORDS}/${ts}-origin.tar.gz"
log="${RECORDS}/${ts}-origin.log"

ping_hc() {  # $1 = path suffix ("/start", "/fail", "")
    [ -n "$HC_URL" ] || return 0
    curl -fsS -m 10 --retry 3 --data-binary "${2:-}" "${HC_URL}$1" >/dev/null 2>&1 || true
}

ping_hc "/start"

# The suite reads the deployed version from the target itself, so the report is
# attributable without this job having to know which release is running.
remote_cmd="cd ${SUITE_DIR} && \
  MASCOPE_PENTEST_TARGET='${MASCOPE_PENTEST_TARGET}' \
  MASCOPE_PENTEST_EMAIL='${MASCOPE_PENTEST_EMAIL}' \
  MASCOPE_PENTEST_PASSWORD='${MASCOPE_PENTEST_PASSWORD}' \
  MASCOPE_PENTEST_LOWPRIV_EMAIL='${MASCOPE_PENTEST_LOWPRIV_EMAIL:-}' \
  MASCOPE_PENTEST_LOWPRIV_PASSWORD='${MASCOPE_PENTEST_LOWPRIV_PASSWORD:-}' \
  MASCOPE_PENTEST_VERIFY_TLS=false \
  ./.venv/bin/python -m pytest -q"

ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" "$remote_cmd" > "$log" 2>&1
suite_rc=$?

# head -c bounds the stream before it lands: an unbounded "report" from a
# compromised target must not be able to starve the backup volume.
ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
    "cd ${SUITE_DIR} && tar -cz report" 2>/dev/null | head -c "$MAX_BYTES" > "$archive"

summary="$(tar -xzOf "$archive" report/pentest-report.json 2>/dev/null \
  | head -c 4000000 \
  | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("report unreadable"); raise SystemExit
s = d.get("summary", {})
print("build=%s findings=%s accepted=%s stale=%s verified=%s not_assessed=%s" % (
    (d.get("build") or {}).get("identity", "?"),
    s.get("findings", "?"), s.get("accepted_findings", 0),
    s.get("stale_baseline_entries", 0), s.get("controls_verified", "?"),
    s.get("not_assessed", "?")))
' 2>/dev/null)"
summary="${summary:-report unreadable}"

# A run that produced no readable report is a failed run even if pytest exited
# 0: the report is the deliverable, and a silent empty archive would otherwise
# ping success and leave a zero-byte file as the evidence.
if [ ! -s "$archive" ] || [ "$summary" = "report unreadable" ]; then
    [ "$suite_rc" -eq 0 ] && suite_rc=1
fi

# Is the report about the build anyone cares about? A green assessment of an
# artifact that is deployed nowhere is honest and useless at the same time,
# which is worse than a red one because it looks like coverage.
#
# The comparison happens HERE and never on the target. The suite executes on the
# box under test, so a release lookup performed there would be the deployment
# whose currency is in question answering the question about itself.
#
# A lookup that fails must never redden an assessment - an unreachable third
# party is not a security finding - so an undeterminable answer is recorded as
# "unknown" and leaves the exit code alone.
if [ "$summary" != "report unreadable" ]; then
    assessed="$(printf '%s' "$summary" | sed -n 's/.*build=\([^ ]*\).*/\1/p')"
    current="$(curl -fsS -m "$RELEASE_TIMEOUT" --retry 2 \
        -H 'Accept: application/vnd.github+json' "$RELEASE_API" 2>/dev/null \
      | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("tag_name") or "")
except Exception:
    print("")' 2>/dev/null)"

    semver='^v[0-9]+\.[0-9]+\.[0-9]+$'
    lag=unknown
    if [[ "$assessed" =~ $semver ]] && [[ "$current" =~ $semver ]]; then
        if [ "$assessed" = "$current" ]; then
            lag=current
        elif [ "$(printf '%s\n%s\n' "$assessed" "$current" \
                  | sort -V | tail -1)" = "$current" ]; then
            lag=behind
        else
            # Ahead is legitimate: a release candidate is assessed before its
            # tag exists. Recorded, not failed.
            lag=ahead
        fi
    fi

    if [ "$lag" = "current" ]; then
        summary="${summary} lag=current"
    else
        summary="${summary} lag=${lag}${current:+ current=${current}}"
    fi

    # Same class as an unreadable report: the run itself succeeded and the
    # evidence is not about the artifact anyone wanted assessed. Distinct exit
    # code so a stale target is never read as a security finding.
    if [ "$lag" = "behind" ] && [ "$suite_rc" -eq 0 ]; then
        suite_rc=90
    fi
fi

# The summary carries the runner's own verdict - the counts and, now, whether
# the target was current - and until this existed only in the heartbeat ping,
# i.e. in a third party's UI. Keep a copy beside the archive so an archived run
# still states what it concluded, and so the evidence is readable without
# leaving this host. Matches the '*-origin.*' prune glob, so retention is
# unchanged.
printf '%s\n' "$summary" > "${RECORDS}/${ts}-origin.summary"

find "$RECORDS" -maxdepth 1 -name '*-origin.*' -mtime +400 -delete 2>/dev/null

if [ "$suite_rc" -eq 0 ]; then
    ping_hc "" "$summary"
else
    # Only counts and the exit code leave this host. Finding detail stays here:
    # it describes weaknesses in our own deployment and the heartbeat service is
    # a third party.
    ping_hc "/fail" "suite exit ${suite_rc} | ${summary}"
fi
exit "$suite_rc"
