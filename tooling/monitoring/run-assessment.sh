#!/usr/bin/env bash
#
# Scheduled Mascope security assessment, from the origin vantage and, when
# configured, the CDN vantage beside it.
#
# Origin: runs the pen-test suite ON the staging target and brings the report
# back here. It runs from this host rather than on the target because a report
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
# CDN (when CDN_TARGET is set): runs the suite HERE, from a local copy, against
# the deployment's public URL, so its requests cross the edge as an internet
# client's do. The edge's WAF lets it through on the operator header, a secret
# that is sent only to the edge, over TLS, and never to the target host. The
# two reports are kept under one timestamp with a comparison that says, per
# control, where the vantages disagree and which layer answered.
#
# Two triggers, answering different questions. Without arguments this is the
# periodic run, from cron: "has the ground shifted?" - certificates, edge rules,
# base images, drift, none of which has a commit - and its heartbeat is the
# dead-man switch, so it always runs, waiting its turn if it must. With
# --on-deploy it is the deploy watch, from a frequent cron: "did this build
# break a control?". It asks the target which release it runs, and assesses
# only a release no run has started on yet; otherwise it does nothing at all.
#
# The watch reads the deployment rather than being told by it. Whatever
# promotes a release - the auto-update timer, an operator, a fleet playbook,
# a rollback - changes what the target reports, so one watch covers them all,
# and the target, treated as hostile, holds no credential for this host. All
# it can do is name a version, which is validated before it is used.
#
# Runs never overlap and are spaced: a lock is held for the whole run, and the
# next one starts no sooner than MIN_INTERVAL after the last ended. The login
# rate limits the suite verifies have windows of a minute or five, so two runs
# back to back would throttle each other's controls; ten minutes apart they do
# not. A deploy run that meets the lock or the interval is deferred - the next
# watch tick looks again - and a periodic one waits.
#
# Exit code is the suite's, so the heartbeat below reflects the assessment
# rather than the plumbing - with two deliberate overrides: an unusable
# report (1) and a target behind the current release (90). Both are cases
# where pytest succeeded and the run is still not evidence. A periodic run
# that could not get the lock within LOCK_WAIT exits 75 and assesses nothing.
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
#   RELEASE_API          release endpoint used by the lag check; the tag it
#                        names goes to both vantages' suites as
#                        MASCOPE_PENTEST_CURRENT_RELEASE, a record only
#   RELEASE_TIMEOUT      lookup timeout; a failed lookup is never fatal
#   MASCOPE_PENTEST_*    target credentials, passed through to the suite
#   CDN_TARGET           public URL through the CDN; unset = origin run only
#   CDN_SUITE_DIR        local copy of security/pentest/ (with its .venv)
#   VANTAGE_GAP          seconds between the two runs (default 300), so the
#                        CDN run does not start inside the login windows the
#                        origin run spent for the same account
#   MASCOPE_PENTEST_HEADER_NAME / _VALUE
#                        the header the edge's skip rule matches (CDN run only)
#   MIN_INTERVAL         seconds from the end of one run to the start of the
#                        next (default 600)
#   LOCK_WAIT            seconds a periodic run waits for a run in progress
#                        (default 7200)
#   DEPLOY_HC_URL        heartbeat for deploy runs (default HC_URL), so a
#                        deploy run that fails still pages
#
# Each vantage leaves three files in RECORDS: <ts>-<vantage>.log (the suite's
# output), <ts>-<vantage>.tar.gz (the report), and <ts>-<vantage>.summary
# (this script's own verdict, so an archived run still states what it
# concluded). With both vantages, <ts>-vantage.{md,json} holds the comparison.
# The summary ends in the trigger, trigger=schedule or trigger=deploy. Three
# dotfiles in RECORDS hold the triggers' state: .run.lock, .last-run-end (when
# the last run ended) and .last-run-version (the release the last run started
# on).
set -uo pipefail

case "${1:-}" in
    "") TRIGGER=schedule ;;
    --on-deploy) TRIGGER=deploy ;;
    *) echo "usage: ${0##*/} [--on-deploy]" >&2; exit 2 ;;
esac

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
CDN_TARGET="${CDN_TARGET:-}"
CDN_SUITE_DIR="${CDN_SUITE_DIR:-/opt/pentest-runner/suite/pentest}"
VANTAGE_GAP="${VANTAGE_GAP:-300}"
MIN_INTERVAL="${MIN_INTERVAL:-600}"
LOCK_WAIT="${LOCK_WAIT:-7200}"
DEPLOY_HC_URL="${DEPLOY_HC_URL:-$HC_URL}"

SSH_OPTS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o ForwardAgent=no
          -o BatchMode=yes -o StrictHostKeyChecking=accept-new
          -o ConnectTimeout=20)

ping_hc() {  # $1 = path suffix ("/start", "/fail", "")
    [ -n "$HC_URL" ] || return 0
    curl -fsS -m 10 --retry 3 --data-binary "${2:-}" "${HC_URL}$1" >/dev/null 2>&1 || true
}

# The release the target says it runs, from its anonymous version route, read
# on the target for the same reason the suite runs there: the origin may answer
# only the CDN. Empty when it cannot be read or names nothing usable. The answer
# is the target's, so it is bounded, parsed by a JSON parser and held to the
# shape of a version before it is compared, stored or printed.
deployed_version() {
    ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
        "curl -fsS -k -m 10 '${MASCOPE_PENTEST_TARGET}/api/version/public'" \
        2>/dev/null | head -c 4096 | python3 -c 'import json, re, sys
try:
    v = json.load(sys.stdin)["data"]["version"]
    ok = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}", v) and v != "unknown"
except Exception:
    ok = False
print(v if ok else "")' 2>/dev/null
}

# Seconds since the last run ended; empty when no run has recorded its end. An
# end in the future (the clock stepped back) counts as just now, so no run
# waits longer than MIN_INTERVAL.
since_last_run() {
    local end elapsed
    end="$(cat "${RECORDS}/.last-run-end" 2>/dev/null)"
    [[ "$end" =~ ^[0-9]+$ ]] || return 0
    elapsed=$(( $(date +%s) - end ))
    echo $(( elapsed < 0 ? 0 : elapsed ))
}

# One run at a time, for as long as this process lives: the lock goes with it,
# however it ends.
exec 9>>"${RECORDS}/.run.lock"
deployed=""
if [ "$TRIGGER" = deploy ]; then
    # Checked in this order so a tick that cannot run touches nothing, the
    # target included.
    if ! flock -n 9; then
        echo "deploy run deferred: a run is in progress"
        exit 0
    fi
    elapsed="$(since_last_run)"
    if [ -n "$elapsed" ] && [ "$elapsed" -lt "$MIN_INTERVAL" ]; then
        echo "deploy run deferred: the last run ended ${elapsed}s ago"
        exit 0
    fi
    deployed="$(deployed_version)"
    if [ -z "$deployed" ]; then
        echo "deploy watch: cannot read the deployed release"
        exit 0
    fi
    # Assessed once per release, whatever the run concluded: a red run pages,
    # and repeating it every tick would only spend the target's rate limits.
    [ "$deployed" = "$(cat "${RECORDS}/.last-run-version" 2>/dev/null)" ] && exit 0
    HC_URL="$DEPLOY_HC_URL"
else
    if ! flock -w "$LOCK_WAIT" 9; then
        ping_hc "/fail" "no run: another held the lock for ${LOCK_WAIT}s"
        exit 75
    fi
    elapsed="$(since_last_run)"
    if [ -n "$elapsed" ] && [ "$elapsed" -lt "$MIN_INTERVAL" ]; then
        sleep $(( MIN_INTERVAL - elapsed ))
    fi
    deployed="$(deployed_version)"
fi
# Recorded at the start, by either trigger, so a periodic run on a new release
# leaves the watch nothing to repeat.
[ -n "$deployed" ] && printf '%s\n' "$deployed" > "${RECORDS}/.last-run-version"
trap 'date +%s > "${RECORDS}/.last-run-end"' EXIT

ts="$(date -u +%Y-%m-%dT%H%M%SZ)"
ping_hc "/start"

# Runs the suite on the target and pulls its report back. $1 = log, $2 = archive.
run_origin() {
    # The suite reads the deployed version from the target itself, so the
    # report is attributable without this job having to know which release is
    # running. The operator header is deliberately not passed: the origin run
    # does not cross the edge, and the target host has no business holding the
    # edge's bypass.
    #
    # The previous run's report is cleared first: report/ persists, and a run
    # that aborts before writing one (a WAF-refused 403, a usage error, a
    # crash) would otherwise archive the last good report under today's date.
    #
    # The current release goes along when the lookup named one, as a record
    # for the report; the verdict and the exit code stay this script's. Only a
    # validated release tag is ever spliced into the remote command line.
    local release_env=""
    [ -n "$current_release" ] && \
        release_env="MASCOPE_PENTEST_CURRENT_RELEASE='${current_release}' "
    local remote_cmd="cd ${SUITE_DIR} && \
  rm -f report/pentest-report.json report/pentest-report.md && \
  MASCOPE_PENTEST_TARGET='${MASCOPE_PENTEST_TARGET}' \
  MASCOPE_PENTEST_EMAIL='${MASCOPE_PENTEST_EMAIL}' \
  MASCOPE_PENTEST_PASSWORD='${MASCOPE_PENTEST_PASSWORD}' \
  MASCOPE_PENTEST_LOWPRIV_EMAIL='${MASCOPE_PENTEST_LOWPRIV_EMAIL:-}' \
  MASCOPE_PENTEST_LOWPRIV_PASSWORD='${MASCOPE_PENTEST_LOWPRIV_PASSWORD:-}' \
  MASCOPE_PENTEST_VERIFY_TLS=false \
  MASCOPE_PENTEST_VANTAGE=origin \
  ${release_env}./.venv/bin/python -m pytest -q"

    ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" "$remote_cmd" > "$1" 2>&1
    local rc=$?

    # head -c bounds the stream before it lands: an unbounded "report" from a
    # compromised target must not be able to starve the backup volume.
    ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
        "cd ${SUITE_DIR} && tar -cz report" 2>/dev/null | head -c "$MAX_BYTES" > "$2"
    return "$rc"
}

# Runs the suite here, through the edge. $1 = log, $2 = archive.
run_cdn() {
    # Prefix assignments, not arguments: they reach the suite's environment
    # without appearing in any process's command line, where the passwords and
    # the header value would be readable by every account on this host. TLS is
    # verified: the edge serves a real certificate, and this is the one run
    # that sends the bypass, so it must not send it to an impostor.
    (
        cd "$CDN_SUITE_DIR" || exit 2
        # As at the origin: an aborted run must leave no report to archive.
        # The release is set even when empty, so none inherited from cron's
        # environment can stand in for a lookup that failed.
        rm -f report/pentest-report.json report/pentest-report.md
        MASCOPE_PENTEST_TARGET="$CDN_TARGET" \
        MASCOPE_PENTEST_EMAIL="${MASCOPE_PENTEST_EMAIL}" \
        MASCOPE_PENTEST_PASSWORD="${MASCOPE_PENTEST_PASSWORD}" \
        MASCOPE_PENTEST_LOWPRIV_EMAIL="${MASCOPE_PENTEST_LOWPRIV_EMAIL:-}" \
        MASCOPE_PENTEST_LOWPRIV_PASSWORD="${MASCOPE_PENTEST_LOWPRIV_PASSWORD:-}" \
        MASCOPE_PENTEST_HEADER_NAME="${MASCOPE_PENTEST_HEADER_NAME:-}" \
        MASCOPE_PENTEST_HEADER_VALUE="${MASCOPE_PENTEST_HEADER_VALUE:-}" \
        MASCOPE_PENTEST_VERIFY_TLS=true \
        MASCOPE_PENTEST_VANTAGE=cdn \
        MASCOPE_PENTEST_CURRENT_RELEASE="${current_release}" \
        exec ./.venv/bin/python -m pytest -q
    ) > "$1" 2>&1
    local rc=$?
    # The local copy is ours, but the report quotes what the deployment
    # answered: bounded the same way as the origin's.
    tar -cz -C "$CDN_SUITE_DIR" report 2>/dev/null | head -c "$MAX_BYTES" > "$2"
    return "$rc"
}

# The report JSON out of an archive, to stdout, bounded. Never extracted to disk.
report_json() {
    tar -xzOf "$1" report/pentest-report.json 2>/dev/null | head -c 4000000
}

# Keep this in step with RELEASE_TAG_PATTERN in mascope_runtime: a box
# piloting a pre-release runs a real release tag, and leaving it out here
# would silently disarm the staleness check for the deployments most likely to
# be forgotten - a candidate is short-lived by intention, not by mechanism.
release_tag='^v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.?[0-9]+)?$'

# The current release, looked up HERE, once, before either vantage runs, so
# both are measured against the same release and each report can record it.
# A failed lookup leaves it empty and is never fatal. $current keeps whatever
# the API named, for the summary; $current_release only a release tag, so
# nothing unvalidated reaches the target's command line.
lookup_release() {
    current="$(curl -fsS -m "$RELEASE_TIMEOUT" --retry 2 \
        -H 'Accept: application/vnd.github+json' "$RELEASE_API" 2>/dev/null \
      | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("tag_name") or "")
except Exception:
    print("")' 2>/dev/null)"
    current_release=""
    [[ "$current" =~ $release_tag ]] && current_release="$current"
    return 0
}

# Runs one vantage and leaves its verdict in $summary and $suite_rc.
assess() {  # $1 = origin | cdn
    local vantage="$1"
    local archive="${RECORDS}/${ts}-${vantage}.tar.gz"
    local log="${RECORDS}/${ts}-${vantage}.log"

    "run_${vantage}" "$log" "$archive"
    suite_rc=$?

    summary="$(report_json "$archive" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("report unreadable"); raise SystemExit
s = d.get("summary", {})
print("build=%s findings=%s accepted=%s stale=%s verified=%s not_assessed=%s"
      " throttled=%s" % (
    (d.get("build") or {}).get("identity", "?"),
    s.get("findings", "?"), s.get("accepted_findings", 0),
    s.get("stale_baseline_entries", 0), s.get("controls_verified", "?"),
    s.get("not_assessed", "?"), s.get("throttled", "?")))
' 2>/dev/null)"
    summary="${summary:-report unreadable}"

    # A run that produced no readable report is a failed run even if pytest
    # exited 0: the report is the deliverable, and a silent empty archive would
    # otherwise ping success and leave a zero-byte file as the evidence.
    if [ ! -s "$archive" ] || [ "$summary" = "report unreadable" ]; then
        [ "$suite_rc" -eq 0 ] && suite_rc=1
    fi

    # Is the report about the build anyone cares about? A green assessment of
    # an artifact that is deployed nowhere is honest and useless at the same
    # time, which is worse than a red one because it looks like coverage.
    #
    # The comparison happens HERE and never on the target. The suite executes
    # on the box under test, so a release lookup performed there would be the
    # deployment whose currency is in question answering the question about
    # itself.
    #
    # A lookup that fails must never redden an assessment - an unreachable
    # third party is not a security finding - so an undeterminable answer is
    # recorded as "unknown" and leaves the exit code alone. The release was
    # looked up once, before either run, so both vantages are measured
    # against the same one (see lookup_release).
    if [ "$summary" != "report unreadable" ]; then
        local assessed
        assessed="$(printf '%s' "$summary" | sed -n 's/.*build=\([^ ]*\).*/\1/p')"
        local lag=unknown
        if [[ "$assessed" =~ $release_tag ]] && [[ "$current" =~ $release_tag ]]; then
            if [ "$assessed" = "$current" ]; then
                lag=current
            elif [ "$(printf '%s\n%s\n' "$(_precedence "$assessed")" \
                      "$(_precedence "$current")" \
                      | sort -V | tail -1)" = "$(_precedence "$current")" ]; then
                lag=behind
            else
                # Ahead is legitimate: `releases/latest` excludes pre-releases,
                # so a box piloting a candidate outranks the newest release the
                # API will name. Recorded, not failed.
                lag=ahead
            fi
        fi

        if [ "$lag" = "current" ]; then
            summary="${summary} lag=current"
        else
            summary="${summary} lag=${lag}${current:+ current=${current}}"
        fi

        # Same class as an unreadable report: the run itself succeeded and the
        # evidence is not about the artifact anyone wanted assessed. Distinct
        # exit code so a stale target is never read as a security finding.
        if [ "$lag" = "behind" ] && [ "$suite_rc" -eq 0 ]; then
            suite_rc=90
        fi
    fi

    # The summary carries the runner's own verdict - the counts and whether
    # the target was current - and until this existed only in the heartbeat
    # ping, i.e. in a third party's UI. Keep a copy beside the archive so an
    # archived run still states what it concluded, and so the evidence is
    # readable without leaving this host. It ends in the trigger, so a deploy
    # run reads as one in the archive and in the heartbeat. A periodic run
    # that could not read the deployed release says so: the deploy watch reads
    # it the same way, so it is blind too, and would otherwise be blind in
    # silence.
    summary="${summary} trigger=${TRIGGER}"
    [ -n "$deployed" ] || summary="${summary} deployed=unreadable"
    printf '%s\n' "$summary" > "${RECORDS}/${ts}-${vantage}.summary"
}

# `sort -V` reads a pre-release suffix as a longer version and orders
# v2.0.0-rc.1 ABOVE v2.0.0, which inverts precedence. It does understand
# `~` as sorting before everything, so map the suffix onto that: the rc
# then ranks below its own release and rc.10 above rc.9.
_precedence() { printf '%s' "${1#v}" | sed -E 's/-(alpha|beta|rc)/~\1/'; }

lookup_release
assess origin
origin_rc=$suite_rc origin_summary=$summary
final_rc=$origin_rc final_summary=$origin_summary

if [ -n "$CDN_TARGET" ]; then
    sleep "$VANTAGE_GAP"
    assess cdn
    cdn_rc=$suite_rc cdn_summary=$summary

    # The comparison reads both reports through file descriptors, so neither
    # is extracted to disk, and runs the local suite copy's script isolated
    # (-I) from anything in the working directory. It writes only bounded,
    # sanitised control ids, outcomes and evidence, and nothing at all when
    # either report is unreadable.
    vantages="$(python3 -I "${CDN_SUITE_DIR}/vantage_diff.py" \
        <(report_json "${RECORDS}/${ts}-origin.tar.gz") \
        <(report_json "${RECORDS}/${ts}-cdn.tar.gz") \
        --json "${RECORDS}/${ts}-vantage.json" \
        --md "${RECORDS}/${ts}-vantage.md" 2>/dev/null)"
    vantages="${vantages:-comparison unavailable}"
    final_summary="origin: ${origin_summary} | cdn: ${cdn_summary} | vantages: ${vantages}"

    # Either vantage failing fails the run; the origin's code wins when both
    # do, as the one that has always been read. A disagreement is not a
    # failure: the vantages legitimately differ, and the comparison records it.
    [ "$final_rc" -eq 0 ] && final_rc=$cdn_rc
fi

find "$RECORDS" -maxdepth 1 \
    \( -name '*-origin.*' -o -name '*-cdn.*' -o -name '*-vantage.*' \) \
    -mtime +400 -delete 2>/dev/null

if [ "$final_rc" -eq 0 ]; then
    ping_hc "" "$final_summary"
else
    # Only counts and the exit code leave this host. Finding detail stays here:
    # it describes weaknesses in our own deployment and the heartbeat service is
    # a third party.
    ping_hc "/fail" "suite exit ${final_rc} | ${final_summary}"
fi
exit "$final_rc"
