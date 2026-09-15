#!/usr/bin/env bash
# Demo loader for the containerized Mascope demo (docker-compose.demo.yaml).
#
# Runs as an init container in the published backend image (which bundles the
# full workspace + the mascope CLI + the postgres client). It prepares the
# `mascope_demo` database from the published demo bundle so the app comes up
# preloaded with real data and a ready-to-use login:
#   - writes throwaway local-only secret files the app reads on startup,
#   - waits for PostgreSQL,
#   - fetches + checksum-verifies the demo bundle from Zenodo (CLI registry),
#   - restores the snapshot database + filestore,
#   - upgrades the schema to head (in case the image is newer than the bundle),
#   - seeds the fixed demo credentials.
#
# Idempotent: a second `up` with the data already present skips the fetch +
# restore and only re-seeds (which is itself idempotent).
#
# Rebuild mode (MASCOPE_DEMO_REBUILD=1): restores the bundle's reference *seed*
# dump (ionization modes, instrument config, calibration/diagnostic collections)
# instead of the full snapshot, and skips the filestore restore. The stack comes
# up with reference data but no samples, so the bundle's raw files can be
# ingested through the real upload -> convert -> match pipeline. This is the
# substrate the reproducibility test drives (see docs/demo_dataset.md).
#
# Required environment (set by docker-compose.demo.yaml):
#   MASCOPE_ENV, MASCOPE_DB_NAME, MASCOPE_DB_USER, MASCOPE_DEMO_DB_PASSWORD,
#   MASCOPE_DEMO_JWT_SECRET, MASCOPE_DEMO_OWNER_SECRET, MASCOPE_DEMO_MFA_KEY
# Optional:
#   MASCOPE_DEMO_REBUILD (default 0)

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

PGHOST="postgres"
PGUSER="${MASCOPE_DB_USER}"
DB="${MASCOPE_DB_NAME}"

# Mascope is installed as a uv tool, so its Python + entry points live in the
# tool venv (the same place db-init.sh finds alembic), not the base interpreter.
TOOL_BIN="/opt/uv/tools/mascope/bin"
PYTHON="$TOOL_BIN/python"
ALEMBIC_BIN="$TOOL_BIN/alembic"

# --- Write the local-only secret files the backend reads on startup ----------
# runtime.secret() resolves these from /app/.runtime/secrets/<name>.txt. The
# values are throwaway and for the localhost demo only - never reuse them.
SECRETS_DIR="/app/.runtime/secrets"
mkdir -p "$SECRETS_DIR"
printf '%s' "${MASCOPE_DEMO_DB_PASSWORD}" > "$SECRETS_DIR/postgres_password.txt"
printf '%s' "${MASCOPE_DEMO_JWT_SECRET}" > "$SECRETS_DIR/jwt_secret_key.txt"
printf '%s' "${MASCOPE_DEMO_OWNER_SECRET}" > "$SECRETS_DIR/server_owner_secret_key.txt"
printf '%s' "${MASCOPE_DEMO_MFA_KEY}" > "$SECRETS_DIR/mfa_encryption_key.txt"
log_info "Wrote demo secret files to $SECRETS_DIR"

export PGPASSWORD="${MASCOPE_DEMO_DB_PASSWORD}"

# --- Wait for PostgreSQL -----------------------------------------------------
log_info "Waiting for PostgreSQL server..."
MAX_WAIT=60
WAITED=0
until pg_isready -h "$PGHOST" -U "$PGUSER" -d postgres >/dev/null 2>&1; do
    if [[ $WAITED -ge $MAX_WAIT ]]; then
        log_error "PostgreSQL not ready after ${MAX_WAIT}s"
        exit 1
    fi
    echo -n "."
    sleep 2
    WAITED=$((WAITED + 2))
done
log_info "PostgreSQL is ready"

# --- Resolve mode: full snapshot (default) or reference seed (rebuild) -------
# Each mode has its own dump and its own "already loaded" sentinel table:
# the snapshot populates sample_item, while the seed only carries reference
# data (target collections) and deliberately no samples.
REBUILD="${MASCOPE_DEMO_REBUILD:-0}"
if [[ "$REBUILD" == "1" ]]; then
    DUMP_REL="seed/mascope_demo.dump"
    SENTINEL_TABLE="target_collection"
    SENTINEL_DESC="target collections"
else
    DUMP_REL="snapshot/mascope_demo.dump"
    SENTINEL_TABLE="sample_item"
    SENTINEL_DESC="sample items"
fi

# --- Skip if the demo data is already loaded (fast re-up) --------------------
# A positive sentinel count means a previous run already restored the bundle
# into this database volume. In rebuild mode this also protects ingested
# samples from being wiped by a container restart (the seed's reference data
# stays present throughout).
LOADED=$(psql -h "$PGHOST" -U "$PGUSER" -d "$DB" -tAc \
    "SELECT count(*) FROM $SENTINEL_TABLE" 2>/dev/null || echo "0")

if [[ "${LOADED//[[:space:]]/}" =~ ^[0-9]+$ ]] && [[ "${LOADED//[[:space:]]/}" -gt 0 ]]; then
    log_info "Demo data already present ($LOADED $SENTINEL_DESC); skipping restore"
else
    # --- Fetch + locate the published bundle (via the in-image CLI) ----------
    log_info "Fetching the demo bundle (downloads + checksum-verifies)..."
    "$PYTHON" -c "import os; os.environ['MASCOPE_ENV']='${MASCOPE_ENV}'; \
from mascope_cli.cmd.demo import _fetch; _fetch.fetch()"
    BUNDLE=$("$PYTHON" -c "from mascope_cli.cmd.demo import bundles; print(bundles.bundle_dir())")
    log_info "Bundle ready at $BUNDLE"

    DUMP="$BUNDLE/$DUMP_REL"
    if [[ ! -f "$DUMP" ]]; then
        log_error "Dump not found in bundle: $DUMP"
        exit 1
    fi

    # --- Restore the dump into a clean database -----------------------------
    log_info "Recreating '$DB' and restoring $DUMP_REL..."
    psql -h "$PGHOST" -U "$PGUSER" -d postgres -c "DROP DATABASE IF EXISTS \"$DB\"" >/dev/null
    psql -h "$PGHOST" -U "$PGUSER" -d postgres -c "CREATE DATABASE \"$DB\"" >/dev/null
    # pg_restore continues past individual errors and then exits non-zero - e.g.
    # a snapshot dumped by a newer pg_dump carries SET directives (like
    # transaction_timeout) that an older server rejects. That is harmless, so
    # don't abort on its exit code; confirm the data actually landed instead.
    pg_restore -h "$PGHOST" -U "$PGUSER" --no-owner --no-acl -d "$DB" "$DUMP" \
        || log_warn "pg_restore reported ignored errors (verifying the result)..."
    RESTORED=$(psql -h "$PGHOST" -U "$PGUSER" -d "$DB" -tAc \
        "SELECT count(*) FROM $SENTINEL_TABLE" 2>/dev/null || echo "0")
    if ! { [[ "${RESTORED//[[:space:]]/}" =~ ^[0-9]+$ ]] && [[ "${RESTORED//[[:space:]]/}" -gt 0 ]]; }; then
        log_error "Restore failed: '$DB' has no $SENTINEL_DESC"
        exit 1
    fi
    log_info "Restored $DUMP_REL into '$DB' ($RESTORED $SENTINEL_DESC)"

    # --- Restore the filestore (snapshot mode only) -------------------------
    # In rebuild mode the filestore starts empty; the real pipeline fills it
    # as raw files are uploaded and ingested.
    FILESTORE_DST="/app/.runtime/env/${MASCOPE_ENV}/filestore"
    mkdir -p "$FILESTORE_DST"
    if [[ "$REBUILD" == "1" ]]; then
        log_info "Rebuild mode: filestore starts empty (filled by ingestion)"
    else
        FILESTORE_SRC="$BUNDLE/snapshot/filestore"
        if [[ -d "$FILESTORE_SRC" ]]; then
            cp -a "$FILESTORE_SRC"/. "$FILESTORE_DST"/
            log_info "Filestore restored to $FILESTORE_DST"
        else
            log_warn "Bundle has no filestore tree at $FILESTORE_SRC; skipping"
        fi
    fi
fi

# --- Standard env subdirectories the app expects on startup ------------------
# The dev-mode CLI creates these when it builds the demo env; in the container
# the env volume starts empty, so make sure they exist in both modes (the
# upload pipeline writes to filestreams, the backend logs to logs, etc.).
for SUB in filestore filestreams temp logs agents; do
    mkdir -p "/app/.runtime/env/${MASCOPE_ENV}/$SUB"
done

# --- Worker and pool sizing for this stack (config layer 3) ------------------
# The demo stack runs in prod mode, so it inherits prod.mascope.toml's pool -
# 3 + 7 connections per worker, sized in that file for "auto workers (12
# usually): 12 x (3 + 7) = 120 peak connections". Worker count is half the
# CPUs, so a small host runs 1-2 workers and the whole ingest has to squeeze
# through 10-20 connections while this stack's stock postgres offers 100. The
# burst does not fit: the pool blocks for pool_timeout, the file-converter's
# POST /api/instrument_configs exceeds its read timeout, and its single
# processing thread wedges in the retry loop with files still in filestreams.
#
# Per-worker sizing that assumes many workers is wrong here, so size this
# stack against its own postgres instead. Pinning the worker count is what
# makes that arithmetic hold: left on "auto" the ceiling would scale with the
# host's CPUs (4 workers x 30 = 120) and overrun a stock max_connections = 100
# on a larger runner - trading a pool timeout for a refused connection.
#
#   2 workers x (3 + 27) = 60 of 100, leaving headroom for db_init, backups,
#   psql and the superuser reservation.
#
# Two workers is ample here: one file-converter feeds this stack, and its
# processing loop is single-threaded. Overflow connections are opened only
# under load and closed after, so the wider ceiling costs nothing at idle.
# prod.mascope.toml is deliberately not touched - its numbers are tuned for a
# 12-worker production deployment against a 200-connection postgres.
cat > "/app/.runtime/env/${MASCOPE_ENV}/prod.mascope.toml" <<'POOL_TOML'
# Written by tooling/demo-init.sh - see the rationale there before editing.
[backend]
workers = 2

[backend.database]
max_overflow = 27
POOL_TOML
log_info "Pinned demo stack to 2 workers x (3 + 27) connections"

# --- Upgrade the schema to head (image may be newer than the snapshot) -------
cd /app/server/backend
log_info "Applying any pending migrations..."
"$ALEMBIC_BIN" upgrade head

# --- Seed the fixed demo credentials (idempotent) ----------------------------
log_info "Seeding demo credentials..."
"$PYTHON" -m mascope_backend.db.scripts.seed_demo

log_info "✅ Demo database ready"
