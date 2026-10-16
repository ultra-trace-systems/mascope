#!/usr/bin/env bash
#
# Mascope disk-space monitor - alert before a full disk crashes the stack.
#
# A Mascope host writes to one or more filesystems that grow over time: the
# Postgres data, the filestore (uploaded raw files) and database dumps under
# $MASCOPE_PATH/.runtime, plus docker's image store. If any of them fills the
# disk, Postgres can wedge and the whole stack goes down. This check measures
# the free space on each and, when one drops below a floor, pings a
# healthchecks.io-style URL so a human is alerted with lead time to act.
#
# A floor alone gives a fast-filling disk little lead time, so the check also
# remembers how much was free on each run, works out how many GiB a day the
# filesystem has been filling over the last 7 and 30 days, and alerts as well
# when that pace would reach the floor within MIN_DAYS_TO_FLOOR days.
#
# It never deletes anything it monitors: the only file it writes is its own
# free-space history ($MASCOPE_PATH/.runtime/disk-check/history.tsv), which it
# keeps to the last 60 days. Run it from the systemd timer tooling/ubuntu.sh
# installs (mascope-disk-check.timer), or from cron:
#   */15 * * * * $MASCOPE_PATH/tooling/disk-check.sh 2>&1 | logger -t mascope-disk
#
# Configuration is all optional (template: tooling/disk-check.env.example). The
# systemd unit reads /etc/mascope/disk-check.env directly; when run standalone
# (e.g. from cron) this script instead sources
# $MASCOPE_PATH/.runtime/secrets/disk-check.env (override with
# MASCOPE_DISK_CHECK_ENV).

set -euo pipefail

log() { echo "[disk-check] $1"; }

: "${MASCOPE_PATH:?MASCOPE_PATH must be set (systemd reads it from /etc/environment)}"

# --- Configuration -----------------------------------------------------------

CONFIG="${MASCOPE_DISK_CHECK_ENV:-${MASCOPE_PATH}/.runtime/secrets/disk-check.env}"
if [[ -f "$CONFIG" ]]; then
    # shellcheck source=disk-check.env.example
    source "$CONFIG"
fi

# Absolute floor (GiB) and early-warning floor (percent of the filesystem).
# A path below EITHER is reported and alerts. Set MIN_FREE_PCT=0 to disable the
# percentage check (useful on very large disks where 10% is still plenty).
MIN_FREE_GB="${MIN_FREE_GB:-10}"
MIN_FREE_PCT="${MIN_FREE_PCT:-10}"
# Alert when the filesystem, at the pace it has been filling, would drop below
# that floor within this many days. Set 0 to disable the projection alert.
MIN_DAYS_TO_FLOOR="${MIN_DAYS_TO_FLOOR:-30}"
# Where docker stores image layers; override if your data-root is elsewhere.
DOCKER_ROOT="${DOCKER_ROOT:-/var/lib/docker}"

# --- History -----------------------------------------------------------------
#
# One tab-separated line per filesystem per run: epoch seconds, available
# bytes, filesystem size in bytes, mount point. `mascope prod doctor` reads the
# same file for its own projection (tooling/cli/src/mascope_cli/cmd/prod/
# doctor.py), so change the format in both places or neither.

HISTORY_DIR="${MASCOPE_PATH}/.runtime/disk-check"
HISTORY="${HISTORY_DIR}/history.tsv"
# Samples older than this are dropped; the longest rate window is 30 days.
HISTORY_DAYS=60
# A rate is only reported once this much history backs it, so one large upload
# in the first hour after install is not projected forward as a daily pace.
MIN_HISTORY_DAYS=1

now="$(date +%s)"

history_ok=1
if ! mkdir -p "$HISTORY_DIR" 2>/dev/null; then
    log "warn: cannot create ${HISTORY_DIR}; fill rates are unavailable"
    history_ok=0
fi

# Append this run's sample for one filesystem. A failed write (a full disk is
# exactly when it happens) only costs the history, never the check itself.
record_sample() {
    local mount="$1" avail="$2" size="$3"
    [[ "$history_ok" -eq 1 ]] || return 0
    if ! { printf '%s\t%s\t%s\t%s\n' "$now" "$avail" "$size" "$mount" >>"$HISTORY"; } 2>/dev/null; then
        log "warn: cannot write ${HISTORY}; fill rates will not update"
        history_ok=0
    fi
}

# Drop samples older than HISTORY_DAYS. The file is only rewritten once its
# oldest sample is a day past the cutoff, so about once a day, not every run.
prune_history() {
    [[ "$history_ok" -eq 1 && -f "$HISTORY" ]] || return 0
    local cutoff oldest tmp="${HISTORY}.tmp"
    cutoff=$((now - HISTORY_DAYS * 86400))
    oldest="$(head -n 1 "$HISTORY" | cut -f 1)" || return 0
    if [[ "$oldest" =~ ^[0-9]+$ ]] && ((oldest >= cutoff - 86400)); then
        return 0
    fi
    if awk -F'\t' -v c="$cutoff" '$1 ~ /^[0-9]+$/ && $1 >= c' "$HISTORY" >"$tmp" 2>/dev/null &&
        mv -f "$tmp" "$HISTORY" 2>/dev/null; then
        return 0
    fi
    rm -f "$tmp" 2>/dev/null || true
    log "warn: could not trim ${HISTORY}"
}

# Fill trend of one filesystem, printed as "<alert>\t<summary>": alert is 1
# when the floor is projected within MIN_DAYS_TO_FLOOR days, else 0.
#
# The rate is the least-squares slope of used space (size - avail, so growing
# the filesystem does not read as freeing space) against time over the last 7
# and 30 days of history plus the current reading. A slope over every sample
# rides out a transient - a dump being written at the moment of a reading -
# that a first-to-last difference would project forward. The projection takes
# the faster of the two rates: the 7-day one catches an instrument that has
# just started a long run, the 30-day one keeps a quiet week from hiding a
# disk that fills in bursts.
fill_trend() {
    local mount="$1" avail="$2" size="$3" floor="$4" src=/dev/null
    [[ -r "$HISTORY" ]] && src="$HISTORY"
    awk -F'\t' -v m="$mount" -v now="$now" -v a="$avail" -v s="$size" \
        -v floor="$floor" -v warn="$MIN_DAYS_TO_FLOOR" -v min_span="$MIN_HISTORY_DAYS" '
        # t: days relative to now (<= 0); used: GiB.
        function add(t, used,   i) {
            for (i = 1; i <= 2; i++) {
                if (t < -win[i]) continue
                n[i]++
                st[i] += t; su[i] += used; stt[i] += t * t; stu[i] += t * used
                if (n[i] == 1 || t < first[i]) first[i] = t
            }
        }
        function days(d) {
            if (d < 1) return "<1 day"
            if (d > 3650) return ">10 years"
            return sprintf("~%.0f days", d)
        }
        BEGIN { win[1] = 7; win[2] = 30; GiB = 1073741824 }
        $4 == m && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ && $1 <= now {
            add(($1 - now) / 86400, ($3 - $2) / GiB)
        }
        END {
            add(0, (s - a) / GiB)
            rates = ""; have = 0
            for (i = 1; i <= 2; i++) {
                span = 0 - first[i]
                if (span < min_span) continue
                # The 30-day window only says something new once the history
                # reaches a day past the 7-day one.
                if (i == 2 && span <= win[1] + 1) continue
                d = n[i] * stt[i] - st[i] * st[i]
                if (d <= 0) continue
                r = (n[i] * stu[i] - st[i] * su[i]) / d
                rates = rates (have ? ", " : "") sprintf("%+.1f GiB/day (%.0fd)", r, span)
                if (!have || r > rate) rate = r
                have = 1
            }
            if (!have) {
                printf "0\tno rate yet (%.0fh of history, needs %gd)", (0 - first[1]) * 24, min_span
                exit
            }
            if (rate <= 0) {
                printf "0\trate %s; not filling", rates
                exit
            }
            full = (a / GiB) / rate
            if (a < floor) {
                printf "0\trate %s; full in %s", rates, days(full)
                exit
            }
            to_floor = ((a - floor) / GiB) / rate
            alert = (warn > 0 && to_floor < warn) ? 1 : 0
            printf "%d\trate %s; %.1f GiB floor in %s, full in %s", alert, rates, floor / GiB, days(to_floor), days(full)
        }
    ' "$src"
}

# --- Checks ------------------------------------------------------------------

low_count=0
filling_count=0
declare -A seen_mounts

check_path() {
    local label="$1" path="$2"

    # Resolve to the nearest existing ancestor: a not-yet-created directory
    # still lives on some filesystem whose free space we can measure.
    while [[ ! -e "$path" && "$path" != "/" ]]; do path="$(dirname "$path")"; done
    if [[ ! -e "$path" ]]; then
        log "skip ${label}: ${path} not found"
        return 0
    fi

    # df -P: one line per filesystem (no wrapping); -B1: sizes in bytes.
    local line mount size avail
    line="$(df -P -B1 "$path" | awk 'NR==2')"
    mount="$(awk '{print $6}' <<<"$line")"
    size="$(awk '{print $2}' <<<"$line")"
    avail="$(awk '{print $4}' <<<"$line")"

    # Guard against a malformed or zero-size df line (pseudo-filesystems) before
    # any arithmetic, so a weird mount can never crash the monitor.
    if ! [[ "$size" =~ ^[0-9]+$ && "$avail" =~ ^[0-9]+$ ]] || [[ "$size" -eq 0 ]]; then
        log "skip ${label} (${mount:-$path}): could not read filesystem size"
        return 0
    fi

    # One filesystem, one report (MASCOPE_PATH and docker often share a mount).
    if [[ -n "${seen_mounts[$mount]:-}" ]]; then
        return 0
    fi
    seen_mounts[$mount]=1

    # Pass values as awk variables and guard divisions with `if`: keeps the
    # arithmetic out of printf's argument list (a bare '>' there is parsed as
    # output redirection) and never divides by a zero-size pseudo-filesystem.
    local free_gb free_pct
    free_gb="$(awk -v a="$avail" 'BEGIN{ printf "%.1f", a/1073741824 }')"
    free_pct="$(awk -v s="$size" -v a="$avail" \
        'BEGIN{ if (s > 0) printf "%.0f", a*100/s; else printf "0" }')"

    # The floor in bytes: the higher of the absolute and percentage floors, so
    # being below it means being below EITHER.
    local floor
    floor="$(awk -v s="$size" -v g="$MIN_FREE_GB" -v p="$MIN_FREE_PCT" \
        'BEGIN{ f = g * 1073741824; if (p > 0 && s * p / 100 > f) f = s * p / 100; printf "%.0f", f }')"

    # The trend is read before this run's sample is appended (it adds the
    # current reading itself), so it holds even when the append fails.
    local trend alert summary
    trend="$(fill_trend "$mount" "$avail" "$size" "$floor")" || trend=$'0\tno rate (could not read the history)'
    alert="${trend%%$'\t'*}"
    summary="${trend#*$'\t'}"
    record_sample "$mount" "$avail" "$size"

    if awk -v a="$avail" -v f="$floor" 'BEGIN{ exit !(a < f) }'; then
        log "LOW: ${label} (${mount}) has ${free_gb} GiB / ${free_pct}% free (floor ${MIN_FREE_GB} GiB / ${MIN_FREE_PCT}%); ${summary}"
        low_count=$((low_count + 1))
    elif [[ "$alert" == 1 ]]; then
        log "FILLING: ${label} (${mount}) has ${free_gb} GiB / ${free_pct}% free; ${summary} (alerts under ${MIN_DAYS_TO_FLOOR} days)"
        filling_count=$((filling_count + 1))
    else
        log "ok:  ${label} (${mount}) has ${free_gb} GiB / ${free_pct}% free; ${summary}"
    fi
}

check_path "state (.runtime)" "$MASCOPE_PATH"
check_path "docker images" "$DOCKER_ROOT"
prune_history

# --- Alerting ----------------------------------------------------------------

if [[ $((low_count + filling_count)) -gt 0 ]]; then
    reason=""
    if [[ "$low_count" -gt 0 ]]; then
        reason="${low_count} filesystem(s) below the free-space floor"
    fi
    if [[ "$filling_count" -gt 0 ]]; then
        reason="${reason:+${reason}; }${filling_count} filesystem(s) projected to reach the floor within ${MIN_DAYS_TO_FLOOR} days"
    fi
    log "ALERT: ${reason}"
    if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
        curl -fsS -m 10 --retry 3 "${HEALTHCHECK_URL}/fail" >/dev/null || true
    fi
    exit 1
fi

# All good - ping the success URL so a silently stalled monitor is itself
# noticed (healthchecks.io flags a check that stops reporting).
if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
    curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL" >/dev/null || true
fi
log "ok: all monitored filesystems have enough free space"
