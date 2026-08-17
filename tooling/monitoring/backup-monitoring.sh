#!/usr/bin/env bash
#
# Back up the monitoring stack (GlitchTip + Uptime Kuma) on the box that hosts
# it. Mirrors the pattern of tooling/backup-cron.sh: restic + optional
# healthchecks.io ping. Run it from root's cron/systemd on the monitoring box, e.g.:
#   30 4 * * * /opt/monitoring/backup-monitoring.sh 2>&1 | logger -t monitoring-backup
#
# What it backs up (and why each is handled differently):
#   1. GlitchTip Postgres  -> a LOGICAL pg_dumpall streamed into restic. NEVER
#      restic the live data dir - a hot copy of Postgres files is inconsistent.
#   2. GlitchTip uploads   -> a named docker volume of static blobs (safe live).
#   3. Uptime Kuma data    -> SQLite; quiesced (brief stop) for a consistent copy.
#
# Config (env vars, or an optional sourced file - see below):
#   RESTIC_REPOSITORY       restic repo (required)
#   RESTIC_PASSWORD_FILE    path to the repo password file, chmod 600 (required)
#   GLITCHTIP_COMPOSE       default /opt/glitchtip/compose.yaml
#   UPTIME_KUMA_COMPOSE     default /opt/uptime-kuma/compose.yaml
#   UPTIME_KUMA_DATA        default /opt/uptime-kuma/data
#   GLITCHTIP_UPLOADS_VOL   default glitchtip_uploads (docker volume name)
#   KEEP_DAILY/WEEKLY/MONTHLY  restic retention (default 7/4/6)
#   HEALTHCHECK_URL         optional healthchecks.io-style ping URL

set -euo pipefail

log() { echo "[monitoring-backup] $1"; }

CONFIG="${MONITORING_BACKUP_ENV:-/opt/monitoring/backup-monitoring.env}"
if [[ -f "$CONFIG" ]]; then
    # shellcheck source=/dev/null
    source "$CONFIG"
fi

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY must be set}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE must be set}"
export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE

GLITCHTIP_COMPOSE="${GLITCHTIP_COMPOSE:-/opt/glitchtip/compose.yaml}"
UPTIME_KUMA_COMPOSE="${UPTIME_KUMA_COMPOSE:-/opt/uptime-kuma/compose.yaml}"
UPTIME_KUMA_DATA="${UPTIME_KUMA_DATA:-/opt/uptime-kuma/data}"
GLITCHTIP_UPLOADS_VOL="${GLITCHTIP_UPLOADS_VOL:-glitchtip_uploads}"
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-6}"

on_error() {
    log "ERROR: backup failed (line $1)"
    # Never leave monitoring down: if we failed inside the quiesced Uptime Kuma
    # window, restart it (a no-op when it is already running).
    docker compose -f "$UPTIME_KUMA_COMPOSE" start uptime-kuma >/dev/null 2>&1 || true
    if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
        curl -fsS -m 10 --retry 3 "${HEALTHCHECK_URL}/fail" >/dev/null || true
    fi
    exit 1
}
trap 'on_error $LINENO' ERR

# The Postgres dump relies on `restic backup --stdin-from-command` (restic
# >= 0.16). Ubuntu's apt ships older; probe the feature so the failure is a
# clear message (and a /fail ping) instead of "unknown flag" mid-backup.
if ! restic backup --help 2>/dev/null | grep -q -- --stdin-from-command; then
    log "ERROR: restic >= 0.16 required (--stdin-from-command missing; found: $(restic version 2>/dev/null | head -1))"
    log "       install the current binary from https://github.com/restic/restic/releases"
    false  # routes through the ERR trap -> /fail ping + exit 1
fi

# Initialize the repo on first use (idempotent).
if ! restic cat config >/dev/null 2>&1; then
    log "restic repository not initialized - running restic init..."
    restic init
fi

# 1) GlitchTip Postgres: logical dump streamed straight into restic.
#    --stdin-from-command propagates pg_dumpall's exit code, so a failed dump
#    aborts instead of silently storing an empty snapshot.
log "backing up GlitchTip Postgres (pg_dumpall)..."
restic backup --tag monitoring --tag glitchtip-db \
    --stdin-from-command --stdin-filename glitchtip-db.sql -- \
    docker compose -f "$GLITCHTIP_COMPOSE" exec -T postgres pg_dumpall -U postgres

# 2) GlitchTip uploads (named volume; static blobs, safe to copy live).
UPLOADS_PATH="/var/lib/docker/volumes/${GLITCHTIP_UPLOADS_VOL}/_data"
if [[ -d "$UPLOADS_PATH" ]]; then
    log "backing up GlitchTip uploads..."
    restic backup --tag monitoring --tag glitchtip-uploads \
        "$UPLOADS_PATH" --exclude='*.tmp' --exclude-caches
else
    log "WARNING: uploads volume not found at $UPLOADS_PATH - skipping"
fi

# 3) Uptime Kuma SQLite: brief stop for a consistent copy, then restart.
#    (Alternative without downtime: sqlite3 <db> ".backup <copy>" then restic the
#    copy - needs the sqlite3 CLI installed on the host.)
log "backing up Uptime Kuma (quiesced)..."
docker compose -f "$UPTIME_KUMA_COMPOSE" stop uptime-kuma
restic backup --tag monitoring --tag uptime-kuma "$UPTIME_KUMA_DATA"
docker compose -f "$UPTIME_KUMA_COMPOSE" start uptime-kuma

# Retention.
log "applying retention (daily=${KEEP_DAILY} weekly=${KEEP_WEEKLY} monthly=${KEEP_MONTHLY})..."
restic forget --tag monitoring \
    --keep-daily "$KEEP_DAILY" \
    --keep-weekly "$KEEP_WEEKLY" \
    --keep-monthly "$KEEP_MONTHLY" \
    --prune

restic check >/dev/null

if [[ -n "${HEALTHCHECK_URL:-}" ]]; then
    curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL" >/dev/null || true
fi
log "monitoring backup completed successfully"
