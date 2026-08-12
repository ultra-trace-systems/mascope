# Maintaining a Mascope deployment

The operator runbook for a self-hosted production server: how it is provisioned,
how it starts, how it updates, how it is backed up, and where to look when
something is off. For internals see
[developer_guide.md](dev/developer_guide.md); for the customer-facing hosting
overview see [hosting.md](hosting.md).

Everything below assumes an Ubuntu host provisioned with
[`tooling/ubuntu.sh`](../tooling/ubuntu.sh).

## At a glance

| Task | Command |
|---|---|
| Find the deployment path | `mascope path` |
| Start / stop the stack | `mascope prod up --detach` / `mascope prod down` |
| Container status / logs | `mascope prod ps` / `mascope prod logs --follow` |
| **Status at a glance** | `mascope prod doctor` (add `--json` to script it) |
| **Check for an update (applies nothing)** | `mascope prod update --check` |
| Update now | `mascope prod update` |
| Approve / defer a pending migration update | `mascope prod update --confirm` / `--snooze 7` |
| Enable unattended updates | edit `/etc/mascope/update.env`, then `sudo systemctl enable --now mascope-update.timer` |
| Update history | `cat "$(mascope path)/.runtime/update/status.log"` |
| Back up now | `mascope prod db backup create` |
| **Disk monitor status / run now** | `systemctl list-timers mascope-disk-check.timer` / `sudo systemctl start mascope-disk-check.service` |
| Disk monitor history | `journalctl -u mascope-disk-check.service` |

## Health at a glance

`mascope prod doctor` gathers the signals you would otherwise check across
several commands into one read-only, network-free report - safe to run anytime
or to poll:

```
$ mascope prod doctor
[OK]
Stack    backend healthy · frontend healthy · postgres healthy · redis healthy · file_converter running
Disk     state 142 GiB / 61% free   ·   docker 38 GiB / 40% free
Updates  no pending migration recorded
Backups  5 local dump(s) · newest 8h ago
Images   11 images · 6.2GB (2.1GB reclaimable)
```

It exits `0` when the stack is healthy and every filesystem is above the
free-space floor (`MASCOPE_UPDATE_MIN_FREE_GB`), and `1` otherwise - so it
doubles as a monitoring probe. `--json` emits the same data for scripting.

## Provisioning

```sh
git clone git@github.com:ultra-trace-systems/mascope.git && cd mascope
./tooling/ubuntu.sh install
```

`ubuntu.sh` installs system dependencies (Docker, uv, Node, restic, jemalloc,
...), builds the `mascope` binary, writes `MASCOPE_PATH` to `/etc/environment`,
and installs the systemd units (below). Re-run with `reinstall` after pulling
new tooling, or `uninstall` to remove the binary and units.

> The provisioning user is the deploy user. `mascope.service` and
> `mascope-update.service` run as that user and read `MASCOPE_PATH` /
> `LD_PRELOAD` from `/etc/environment`.

## The stack (boot service)

`mascope.service` brings the stack up on boot and down on shutdown:

```sh
sudo systemctl status mascope.service
sudo systemctl restart mascope.service     # = mascope prod down && up
```

Day to day you can also drive it directly:

```sh
mascope prod up --detach       # start (db_init runs pending migrations first)
mascope prod ps                # container status
mascope prod logs --follow backend
mascope prod restart backend
mascope prod down
```

## Updating

A release is either a **fast update** (new container images, no schema change,
near-zero downtime) or a **migration update** (a database migration runs on
startup and causes a short outage). The tooling tells the two apart so you only
schedule downtime when it is real.

### Preflight - know before you apply

```sh
mascope prod update --check        # classify the pending update, change nothing
mascope prod update --check --json # machine-readable
```

Outcome (also the exit code): `up-to-date` (0), `fast-update` (10),
`migration-update` (20), error (2).

### Manual update

```sh
mascope prod update                  # follow the rolling `latest` (master) build
mascope prod update --version v1.3.0 # deploy a specific pinned release
```

`mascope prod update` on its own follows the rolling **`latest`** master build,
whose version shows in the UI as a date+hash build id (e.g.
`2026.07.08-ab12cd34`) - *not* the newest `vX.Y.Z` release. To run a pinned
release, pass `--version vX.Y.Z`. To make a server track that release across
future updates, check the tag out in the deployment (a pinned checkout reports
its tag as the version):

```sh
git fetch --tags && git checkout v1.3.0
mascope prod update          # deploys v1.3.0; the UI then shows v1.3.0
```

This pulls the target images and does a rolling restart. Database migrations run
automatically on startup; `db_init` takes a **pre-migration dump** into
`.runtime/database/backups/prod/` first. A failed image pull aborts before the
running stack is touched. (You do **not** need `mascope prod down` first - that
only adds downtime.)

### Unattended updates (the timer)

`mascope-update.timer` runs `mascope prod update --auto` nightly. It is
installed **disabled**. `--auto` automatically tracks the newest GitHub
**release tag** (`vX.Y.Z`) - there is no version to pin by hand. To turn it on:

1. Make sure the stack is running - the applied database revision is read from
   the live Postgres container. **No credentials are needed**: `--auto` reads
   the public GitHub releases API over plain HTTPS.
2. Enable the timer (adjust the window / grace first if you like):

   ```sh
   sudoedit /etc/mascope/update.env      # optional: MASCOPE_UPDATE_WINDOW, grace
   sudo systemctl enable --now mascope-update.timer
   ```

Each run:

- **Up to date** -> nothing.
- **Fast update** -> applied inside the maintenance window
  (`MASCOPE_UPDATE_WINDOW`, e.g. `2-5`), then health-checked. A failed health
  check **alerts and stops - it never rolls back automatically** (see
  Troubleshooting).
- **Migration update** -> recorded and reported (exit 30), then applied at the
  next window once its grace period elapses (`MASCOPE_UPDATE_GRACE_DAYS`,
  default 7 days) **or** you confirm it - unless it has been snoozed.

Steer a pending migration update:

```sh
mascope prod update --confirm    # apply at the next window, skip the grace wait
mascope prod update --snooze 7   # postpone 7 days
```

Configuration lives in `/etc/mascope/update.env` (`MASCOPE_UPDATE_WINDOW`,
`MASCOPE_UPDATE_GRACE_DAYS`, `MASCOPE_UPDATE_REPO`). Observe activity:

```sh
systemctl list-timers mascope-update.timer
journalctl -u mascope-update.service
cat "$(mascope path)/.runtime/update/status.log"   # applied / pending history
cat "$(mascope path)/.runtime/update/state.json"   # the current pending update
```

### Rolling out a release across several servers

When you run more than one server, roll a new release out **canary-first** and
verify each step rather than updating everything at once.
[`tooling/fleet/update.yml`](../tooling/fleet/update.yml) automates the whole
sequence below (serial, fail-fast, doctor-verified, CLI kept in sync); this
section documents the manual per-server equivalent. The procedure per server
(on the server, from the deployment checkout):

```sh
cd <deployment>            # the mascope checkout, e.g. ~/mascope
git fetch --tags origin
git checkout vX.Y.Z        # the release you are rolling out
mascope prod update        # pulls the tagged images, rolling restart (~30 s)
```

Recommended sequence:

1. **Canary.** Update one low-stakes server first. Verify it before touching
   the rest (see the checklist below).
2. **Roll out** to the remaining servers once the canary is healthy.
3. **Watch** error reporting / uptime monitoring for a bit after each wave.

Per-server verification checklist:

```sh
docker ps                                   # mascope_prod_* healthy
mascope prod doctor                          # stack + disk + backups + migrations
curl -sI https://<name>/ | head -1           # app serves through its proxy
```

Two gotchas this procedure exists to avoid:

- **The CLI is not refreshed by `prod update`.** It only pulls images; the
  `mascope` binary is a `uv` tool installed by `tooling/ubuntu.sh`. If a release
  adds or changes CLI commands, reinstall the CLI (`ubuntu.sh reinstall`, or the
  `uv tool install` step it runs) - `prod update` now warns when the running CLI
  has drifted from the checkout.
- **Host env vars apply at login.** If a rollout also changes something in
  `/etc/environment` (e.g. a new `MASCOPE_*` var), start the stack from a
  **fresh** shell session, or the value interpolates empty.

### Expect a burst of restart noise

Each server's rolling restart briefly tears down Postgres, container DNS and
the backend/file-converter socket link, so error monitoring reliably lights up
for ~30 s per server: `the database system is shutting down`,
`Name or service not known`, `... not registered in file converter`,
`/file-converter is not a connected namespace`, and a cluster of socket
token-validation failures as clients reconnect.

This is expected and self-healing - the converter retries, and files are
ingested normally. What matters is whether it **stops**:

```sh
# on a server, a few minutes after its restart - expect 0
docker logs mascope_prod_backend --since 30m 2>&1 | grep -c "not registered in file converter"
# and nothing quarantined anywhere under the filestore
find "$(mascope path)"/.runtime/env/*/filestore -type d -name failed_files
```

Errors that keep arriving after the restart window, or any `failed_files`
directory with contents, are real and worth investigating. Resolve the
restart-window issues in the error tracker so a recurrence stands out.

## Backups

[`tooling/backup-cron.sh`](../tooling/backup-cron.sh) runs a two-layer nightly
backup: a local database dump (`mascope prod db backup create`, pruned by
`LOCAL_RETENTION_DAYS`) plus an encrypted off-site copy of the dumps and
filestore via [restic](https://restic.net/).

Set it up:

1. Copy the template and fill it in (restic repo + password, retention):

   ```sh
   cp tooling/backup.env.example "$(mascope path)/.runtime/secrets/backup.env"
   sudoedit "$(mascope path)/.runtime/secrets/backup.env"
   ```

2. Add a crontab entry (the header must export `MASCOPE_PATH`):

   ```cron
   MASCOPE_PATH=/path/to/mascope
   0 4 * * * $MASCOPE_PATH/tooling/backup-cron.sh 2>&1 | logger -t mascope-backup
   ```

Restore a dump into the active environment:

```sh
mascope prod db backup list
mascope prod db restore <dump-file> --yes    # or omit the file for the latest
```

### Pre-migration dumps

Separately from the backup cron, `db_init` takes a **pre-migration dump** into
`.runtime/database/backups/prod/` whenever a migration update runs on startup.
To keep these from piling up on a server that has auto-updates but no backup
cron, `db_init` keeps only the most recent `MASCOPE_PREMIGRATION_KEEP` of them
(default 5) and prunes older ones - it only ever touches `*_pre-migration.dump`
files, never the cron/manual dumps. Raise the count (or set up the backup cron
above) if you want a longer local history.

## Disk space

A full disk is the classic way to take the whole stack down: Postgres cannot
write and wedges. Everything that grows lands on the host - the Postgres data,
the filestore (uploaded raw files) and dumps under `.runtime/`, and docker's
image store under `/var/lib/docker` - usually sharing one filesystem. Three
guards keep it from filling silently.

### The monitor (early warning)

`tooling/ubuntu.sh` installs and **enables** `mascope-disk-check.timer`, which
runs [`tooling/disk-check.sh`](../tooling/disk-check.sh) every 15 minutes. It is
read-only - it only measures free space on the `.runtime` and docker
filesystems and reports to the journal. When a filesystem drops below the floor
it pings a healthchecks.io-style URL so you are alerted with lead time.

Configure it in `/etc/mascope/disk-check.env` (chmod 600, template
[`tooling/disk-check.env.example`](../tooling/disk-check.env.example)):

- `MIN_FREE_GB` (default 10) - absolute floor; the "about to crash" signal.
- `MIN_FREE_PCT` (default 10) - percentage floor; an earlier warning. Set to
  `0` on a very large disk to avoid paging while tens of GiB are still free.
- `HEALTHCHECK_URL` - **set this to actually get alerted.** On every OK run it
  pings the URL (so a stalled monitor is itself flagged); when low it pings
  `<url>/fail`. Use a check separate from the backup one.

```sh
sudo systemctl start mascope-disk-check.service   # run it now
journalctl -u mascope-disk-check.service          # what it found
```

### The update disk guard

`mascope prod update` (and the unattended `--auto`) refuse to pull new images
when free space on the docker image store is below `MASCOPE_UPDATE_MIN_FREE_GB`
(default 5 GiB) - a pull that fills the disk mid-flight is worse than a deferred
update. Under `--auto` the shortfall is written to the update `status.log` and
exits with the error code, so the timer surfaces it. Tune the floor in
`/etc/mascope/update.env`.

### Automatic image pruning

After a **successful** update the tooling runs `docker image prune -af`, which
removes the superseded release's images (new images are pulled on every update
and the old ones are otherwise left behind, accumulating gigabytes over time -
especially with unattended updates). The running stack's images are referenced
and kept; a manual rollback re-pulls the previous release (guarded by the disk
guard above), the same as the documented rollback flow.

## Monitoring

Beyond the healthchecks.io dead-man's-switch pings (backups, disk monitor), a
small self-hosted stack gives error tracking and external uptime monitoring. It
runs off the Mascope servers - typically on a separate internal machine. See
[`tooling/monitoring/`](../tooling/monitoring/README.md) for the full deploy
runbook (GlitchTip + Uptime Kuma, LAN-only).

### Error reporting to GlitchTip (opt-in)

The backend can forward `WARNING`/`ERROR` log records (with tracebacks and
request context) to a self-hosted [GlitchTip](https://glitchtip.com/) instance,
so you stop grepping log files for problems. It is **off by default** and gated
entirely on one environment variable:

- Unset `MASCOPE_SENTRY_DSN` (the default) - no SDK import, no reporting, zero
  behavior change.
- Set it to a GlitchTip project DSN on the **host** - `docker compose` passes it
  into the backend and file-converter containers, which install a loguru sink
  that reports WARNING+ events.

```sh
# on each Mascope server: append to /etc/environment (read by mascope.service),
# then bring the stack up again so the containers pick it up:
MASCOPE_SENTRY_DSN=http://<public_key>@<monitoring-tailnet-ip>:8000/<project_id>
```

The DSN targets the monitoring box's **tailnet IP** — events travel over
Tailscale (the box's LAN address does not route from the servers, and container
DNS cannot resolve MagicDNS names). One-time per server: the backend container
needs a `tailscale0` masquerade line in the `MASCOPE NAT` block; see the
[monitoring runbook](../tooling/monitoring/README.md) step 7.1.

Backend images ship `sentry-sdk` (the runtime's `[sentry]` extra) since
2026-07, so there is nothing to install per server; the DSN alone toggles
reporting. The event `environment` is the runtime mode and `release` follows
`MASCOPE_VERSION` when set, so events group by deployment. Full setup -
creating the project, copying the DSN, and the Uptime Kuma monitors - is in the
[monitoring runbook](../tooling/monitoring/README.md).

### Performance tracing (opt-in, needs the DSN)

With the DSN set, `MASCOPE_SENTRY_TRACES_RATE` additionally samples that
fraction of backend requests as transactions, giving per-endpoint latency and
a slowest-transactions list under GlitchTip's **Performance** tab - the
cheapest answer to "which API got slow after the release":

```sh
# next to the DSN in /etc/environment; 0.1 = trace 10% of requests
MASCOPE_SENTRY_TRACES_RATE=0.1
```

Unset or `0` (the default) keeps the errors-only behavior; values outside
`[0, 1]` log a warning and keep tracing off. Start low (`0.05`-`0.1`) and watch
GlitchTip's Postgres growth on the monitoring box before raising it -
transactions are far more numerous than errors.

## Optional features

**Peak assignment** (assign a chemical composition to every peak - see
[the user docs](user/how-it-works/peak-assignment.md)) ships **off**. A server
that leaves it off is unaffected by it: samples process exactly as before, the
UI is unchanged, and the `/api/peak-assignments` write routes refuse to launch
runs (403; the read routes stay open, so results from an earlier opted-in
period remain visible). To enable it on a deployment, set it in the env's
config toml:

```toml
[meta]
peak_assignment = true
```

then rebuild and restart the stack (`mascope prod up --build`). The rebuild
matters: the frontend bakes the flag in at image build time, so a plain
restart flips only the backend and leaves the UI on the old setting.
`MASCOPE_PEAK_ASSIGNMENT=1` in `/etc/environment` flips the backend without
editing the toml (remember host env vars apply at login - start the stack from
a fresh shell), but the frontend still needs the toml value and a rebuild.
Enabling it means every newly processed sample also gets a database-stage
assignment run, which adds processing time and one database row per detected
peak per run, so watch disk after turning it on (see
[Disk space](#disk-space)). Existing samples are not assigned retroactively;
run assignment explicitly from the UI for those.

### Reclaiming assignment runs

Each assignment run writes **one row per observed peak** of its sample -
including peaks it could not assign, because the ledger is deliberately
complete - and re-assigning a sample adds a whole new run beside the old one.
Nothing removes them automatically, so on a server where assignment is re-run
routinely `peak_assignment` grows without bound.

```sh
MASCOPE_PRUNE_DRY_RUN=1 mascope prod db script run prune_peak_assignment_runs
mascope prod db script run prune_peak_assignment_runs
```

The dry run reports what it would delete and changes nothing. A real run keeps
the newest few completed runs per sample (so a result can still be compared
against the one it replaced) and drops the rest, plus failed runs past a short
grace period; deleting a run cascades to its rows. Tune with
`MASCOPE_PRUNE_KEEP_PER_SAMPLE` (default 3), `MASCOPE_PRUNE_KEEP_FAILED_HOURS`
(default 24) and `MASCOPE_PRUNE_KEEP_RUNNING_HOURS` (default 72, floored at 12
so runs that may still be executing cannot be pruned out from under a worker).

Deleting rows returns space to Postgres for reuse but not to the filesystem;
`VACUUM FULL peak_assignment` (or `pg_repack`) afterwards does that, and takes
an exclusive lock while it runs.

There is no timer for this yet - run it when the disk monitor flags growth, or
add it to your own cron alongside the backup job.

### Loading reference chemistry data

Peak assignment can additionally match against a mirror of public chemistry
databases, so a peak gets a named identity and not just a formula. The mirror is
optional: with none loaded, assignment works exactly as described above and
simply reports no identities.

**`mascope reference` is not available on a server.** It is registered only when
the CLI runs from a monorepo checkout, because it pulls the chemistry
dependencies that are deliberately kept out of the operator install - so
reinstalling the CLI does not expose it. A deployed backend image already ships
those dependencies, so load reference data by running the ingest inside the
backend container instead:

```sh
docker compose exec backend python -m mascope_backend.db.scripts.reference_sync custom /data/my_list.csv --name my-list --version 2026-07
```

Mount the dump into the backend service first; the path is resolved inside the
container. `custom` is the adapter for hand-authored CSV/TSV lists - the public
databases have their own adapters, and each load is versioned.

A load replaces the active version of that source only once it has successfully
read records, so a dump the adapter cannot parse leaves the existing mirror
serving rather than emptying it. Re-running the same source is how you update
it; prior versions stay on disk until pruned.

### Upload size cap

A single resumable (tus) upload is capped at 50 GB by default, so one runaway
transfer cannot fill the disk. The cap applies **per upload** - it does not
limit how many files agents or users transfer in a day, only how large each
one may be. Instruments producing larger single files can raise it in the
env's config toml:

```toml
[backend]
tus_max_upload_gb = 200
```

Clients see the cap as the standard `Tus-Max-Size` header; an upload declared
larger than the cap is refused up front with HTTP 413.

| Path | What |
|---|---|
| `/etc/environment` | `MASCOPE_PATH`, `LD_PRELOAD` (read by the systemd units) |
| `/etc/mascope/update.env` | update window / grace / repo, update disk floor (chmod 600) |
| `/etc/mascope/disk-check.env` | disk-monitor thresholds + alert URL (chmod 600) |
| `$MASCOPE_PATH/.runtime/secrets/` | `postgres_password.txt`, `jwt_secret_key.txt`, `server_owner_secret_key.txt`, TLS cert/key, `backup.env` |
| `$MASCOPE_PATH/.runtime/database/backups/prod/` | database dumps (incl. pre-migration) |
| `$MASCOPE_PATH/.runtime/update/` | `state.json` (pending update), `status.log` |

## Troubleshooting

**Stack won't start.** `sudo systemctl status mascope.service`, then
`mascope prod ps` and `mascope prod logs backend`. Confirm Docker is up and the
secrets in `.runtime/secrets/` exist.

**Update timer never fires / always fails.** `systemctl list-timers` to confirm
it is enabled; `journalctl -u mascope-update.service` for the reason. Exit 2 is
usually a stack that is down (the DB revision is read from the running Postgres)
or no network to reach the releases API. Exit 30 is *not* a failure - it means a
migration update is pending.

**A migration update won't apply.** It waits for the maintenance window, the
grace period, or a confirm, and never applies while snoozed. Run
`mascope prod update --check` to see the classification and
`cat "$(mascope path)/.runtime/update/state.json"` for its `first_seen_at` /
`snooze_until` / `confirmed` state. `mascope prod update --confirm` applies it at
the next window.

**Backend unhealthy after an update.** The updater stops and leaves the stack in
place (no automatic rollback). Investigate with `mascope prod logs backend`. To
roll back manually: if a migration ran, first restore the pre-migration dump
(`mascope prod db backup list`, then `mascope prod db restore <dump> --yes`),
then redeploy the previous release with
`mascope prod update --version v<previous>`.
