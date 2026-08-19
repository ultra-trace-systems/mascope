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
| **Require a new password from every user** | Manage users in the app, or `mascope prod db script run require_password_change` |
| **Clear a lost second factor (2FA)** | Manage users in the app, or `mascope prod mfa reset <email>` |
| **Disk monitor status / run now** | `systemctl list-timers mascope-disk-check.timer` / `sudo systemctl start mascope-disk-check.service` |
| Disk monitor history | `journalctl -u mascope-disk-check.service` |
| Assignment-run retention status / run now | `systemctl list-timers mascope-assignment-prune.timer` / `sudo systemctl start mascope-assignment-prune.service` |
| Assignment-run retention history | `journalctl -u mascope-assignment-prune.service` |

## Health at a glance

`mascope prod doctor` gathers the signals you would otherwise check across
several commands into one read-only, network-free report - safe to run anytime
or to poll:

```
$ mascope prod doctor
[OK]
Stack    backend healthy · frontend healthy · postgres healthy · redis healthy · file_converter running
Version  v1.6.1
Disk     state 142 GiB / 61% free   ·   docker 38 GiB / 40% free
Updates  no pending migration recorded
Backups  5 local dump(s) · newest 8h ago
Images   11 images · 6.2GB (2.1GB reclaimable)
```

It exits `0` when the stack is healthy, the running images match the release
this deployment would deploy, and every filesystem is above the free-space
floor (`MASCOPE_UPDATE_MIN_FREE_GB`); `1` otherwise - so it doubles as a
monitoring probe. `--json` emits the same data for scripting.

**Version** compares the tag the containers are actually running against the
one this deployment would deploy right now (the `MASCOPE_VERSION` pin, else the
release tag checked out, else `latest`). A mismatch is reported as drift:

```
Version  backend latest · frontend latest  DRIFT (this checkout deploys v1.6.1)
```

Drift means the next `mascope prod up` - a restart, or a reboot - moves the
stack to a different release than the one it is serving. Resolve it by bringing
the two into line: `git checkout <tag>` for the release the server should run,
then `mascope prod update`.

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
> `mascope-update.service` run as that user, from the deployment checkout
> (`WorkingDirectory`), and read `MASCOPE_PATH` / `LD_PRELOAD` from
> `/etc/environment`.

> Servers provisioned before `WorkingDirectory` was added got units that
> systemd ran from `/`: with no repository there, the checked-out release tag
> could not be read and every boot deployed `latest` instead. Re-run
> `./tooling/ubuntu.sh install` from the checkout on such a server (it rewrites
> the units and reloads systemd) - a release update alone does not refresh the
> installed units. `mascope prod doctor` reports the drift while it lasts.

Verify a fresh deployment end to end with `mascope prod doctor` plus the
release smoke test, then reboot once and check both again - boot-time
problems (a unit change, a kernel setting that did not persist) only show
on a reboot. Against a production stack the smoke test needs the origin
URL, its owner credentials, and - with the self-signed certificate -
`SMOKE_INSECURE`:

```sh
SMOKE_BASE_URL=https://localhost SMOKE_INSECURE=1 \
SMOKE_EMAIL=<owner email> SMOKE_PASSWORD=<owner password> \
bash tooling/smoke-test.sh
```

### The filestore on a data volume

Uploaded raw files and their processed arrays land in the filestore at
`.runtime/env/<env>/filestore` - usually the fastest-growing part of the
deployment. To keep it on a separate data volume, replace that directory with
a symlink while the stack is down; the tooling resolves the link when it
mounts the filestore into the containers:

```sh
mascope prod down
sudo mkdir -p /mnt/data/filestore
sudo chown 1000:1000 /mnt/data/filestore    # the containers run as uid 1000
rsync -a .runtime/env/default/filestore/ /mnt/data/filestore/
rm -rf .runtime/env/default/filestore
ln -s /mnt/data/filestore .runtime/env/default/filestore
mascope prod up --detach
```

Do not point `[meta] filestore` in the env config at an absolute path
instead: that file is read both on the host (where it picks the volume to
mount) and inside the containers (where the host path does not exist), so
the stack fails to start.

## The stack (boot service)

`mascope.service` brings the stack up on boot and down on shutdown:

```sh
sudo systemctl status mascope.service
sudo systemctl restart mascope.service     # = mascope prod down && up
```

It runs from the deployment checkout, so a boot deploys whatever the checkout
selects - the release tag checked out, or `latest` on a `master` checkout,
exactly like running `mascope prod up` by hand. To hold a server on one release
regardless of the checkout, pin it in `/etc/environment`
(`MASCOPE_VERSION=vX.Y.Z`, read by the unit) - at the cost of editing it by
hand at every update. A `.env` file in the checkout has no effect: the CLI
resolves `MASCOPE_VERSION` itself and passes it to compose.

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
release, pass `--version vX.Y.Z` - a successful update then also moves the
deployment checkout to that tag, so the server keeps tracking (and
reporting) that release across restarts and future updates. Checking the
tag out first is equivalent:

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

An applied update also moves the deployment checkout to the release it
deployed, so a reboot redeploys that same release and `mascope prod doctor`
stays clean. The move is deliberately cautious - it never discards local
changes - so on a checkout with modifications (or one whose `origin` cannot
be reached to fetch the tag) the update still succeeds with a warning, and
doctor reports the gap as DRIFT until the checkout is aligned by hand:

```sh
cd "$(mascope path)"
git fetch --tags && git checkout vX.Y.Z   # the release the update applied
```

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

Neither layer includes `.runtime/secrets/` - deliberately, so no backup medium
holds both the database and the keys that make its secrets usable. The flip
side: restoring onto a fresh host needs the secrets restored separately. Keep
a copy of the secrets files wherever the deployment's other credentials live -
the [two-factor encryption key](#two-factor-authentication) in particular
cannot be regenerated, only lost.

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

## User accounts

### Requiring a password change

Requiring a change puts every account through the password policy in
[authorization.md](authorization.md#passwords). Reach for it whenever you want
every password re-set - after tightening a rule, on a periodic refresh, or if you
suspect credentials are exposed. Accounts keep whatever password they had until
then: the policy is only enforced at the moment one is set.

It is a **soft** requirement, not a lockout. Everyone signs in with their existing
password as usual, and is then held at a password screen until they set a new one
that passes the policy and differs from the old. Nobody is excluded - the owner
who triggers it and deactivated accounts included, so a reactivated account cannot
come back on a pre-policy password.

An owner can do it from **Manage users** in the web interface, which also notifies
anyone with the app open. On the server:

```sh
# report what would change, without changing anything
MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN=1 mascope prod db script run require_password_change

# require the change
mascope prod db script run require_password_change
```

The script sends no live notification - there is no socket server in that process
- so sessions already open transition when their next request is refused.

**Time it deliberately.** Changing a password revokes that user's API access
tokens; see below.

### Undoing it

There is no way to withdraw the requirement through the web interface. On the
server:

```sh
mascope prod db script run clear_password_change_requirement
```

Set `MASCOPE_CLEAR_PASSWORD_CHANGE_EMAILS` to a comma-separated list to release
only some accounts. Note that the pre-script database dump is a whole-database
restore, not a per-account undo - use the script, not the dump.

Accounts whose password an administrator reset keep that administrator-issued
password, so releasing them leaves it in place. Reset those accounts again
instead.

### Effect on API access tokens

When a user changes their password, that user's access tokens are revoked. The
file-converter token is reissued automatically, but **SDK and notebook tokens and
instrument-agent pairings are not** - their holders must regenerate or re-pair
them. Across a whole deployment that adds up, so schedule a deployment-wide
requirement outside acquisition hours.

Requiring the change does not revoke anything by itself; tokens are revoked per
user, as each one complies.

### Two-factor authentication

Accounts can protect sign-in with a second factor (TOTP), and a deployment can
require one by role - [authorization.md](authorization.md#two-factor-authentication)
describes the feature as users and admins see it, and the
[user guide](user/guides/two-factor.md) walks through enrolment. Three things
concern the operator: one secret, one policy setting, and the last-resort
reset.

**The encryption key.** `.runtime/secrets/mfa_encryption_key.txt` encrypts the
stored TOTP seeds. `mascope prod up` generates it when missing, so it appears
on a deployment's first start under a release that knows it. Two properties
matter:

- **It is not in the nightly backups** - deliberately, so a database dump (or
  a stolen off-site copy) cannot be used to mint codes. That makes the file on
  the host the only copy: keep one off the server, wherever the deployment's
  other credentials live. On a server rebuilt from backups without it, every
  enrolled account's TOTP stops verifying; recovery codes still work (their
  hashes live in the database), so each user can sign in and enrol again - but
  every one of them has to.
- **Never rotate it casually.** Replacing it has exactly the same effect as
  losing it. Unlike `jwt_secret_key.txt`, there is no routine reason to change
  it.

**Requiring it.** Set `mfa_required_min_role` under `[backend]` in the env's
config toml (`admin` covers admins and owners, `guest` covers everyone), then
`mascope prod up` to recreate the backend. No image rebuild is needed - the
frontend reads the policy from the API. Two guards catch misconfiguration at
startup rather than at someone's expense: a value that is not a role name
stops the backend, and so does an active policy with no usable encryption key
(which would otherwise hold every covered account at an enrolment screen that
cannot complete).

**When someone is locked out.** Recovery codes and in-app resets (Manage
users) cover most cases. The host-level escape hatch exists for the case
nothing in the app can reach - the only account that could reset the factor
has lost its own authenticator and its codes:

```sh
mascope prod mfa status          # who holds a second factor + unused code counts
mascope prod mfa reset <email>   # clear it so its holder can enrol afresh
```

The reset changes no password and reveals nothing; it only stops the second
step being demanded, so the account's holder can sign in and set up a new
authenticator. Open sessions are not ended - restart the backend if you need
them closed.

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
complete - and re-assigning a sample adds a whole new run beside the old one,
so on a server where assignment is re-run routinely `peak_assignment` grows
without bound.

A deployment provisioned by `tooling/ubuntu.sh` handles this automatically:
`mascope-assignment-prune.timer` runs a retention pass nightly at 03:30 and is
**enabled by default**. Each pass keeps the newest few completed runs per
sample *and engine* (so a result can still be compared against the one it
replaced) and drops the rest, plus failed runs past a short grace period;
deleting a run cascades to its rows. It deletes only superseded derived data -
assignments are recomputable by re-running assignment - and it runs whether or
not the `peak_assignment` flag is enabled, since ledgers written before opting
out still age out and an empty table costs one cheap query. Tune the policy in
`/etc/mascope/prune.env` with `MASCOPE_PRUNE_KEEP_PER_SAMPLE` (default 3),
`MASCOPE_PRUNE_KEEP_FAILED_HOURS` (default 24),
`MASCOPE_PRUNE_KEEP_RUNNING_HOURS` (default 72, floored at 12 so runs that may
still be executing cannot be pruned out from under a worker) and
`MASCOPE_PRUNE_KEEP_IMPORTING_HOURS` (default 24, floored at 1); disable it
entirely with `sudo systemctl disable --now mascope-assignment-prune.timer`.

The keep-newest budget counts **per sample and engine**, which matters on a
server where assignment runs are also published from an external engine rather
than only computed in the app: on a shared budget a few republished imports
would evict every in-app run for that sample, ledger rows cascading with them.
Each engine ages out of its own quota instead.
`MASCOPE_PRUNE_KEEP_IMPORTING_HOURS` covers a different case - an import that
was started and never finished. Such a run holds staged rows *and* blocks new
assignment work on its sample, so it is reclaimed on its own, shorter grace; a
client that still knows the run id can also delete it outright instead of
waiting for the nightly pass.

The same pass can always be run by hand, e.g. ahead of schedule when the disk
monitor flags growth:

```sh
MASCOPE_PRUNE_DRY_RUN=1 mascope prod db script run prune_peak_assignment_runs
mascope prod db script run prune_peak_assignment_runs
```

The dry run reports what it would delete and changes nothing.

Deleting rows returns space to Postgres for reuse but not to the filesystem;
`VACUUM FULL peak_assignment` (or `pg_repack`) afterwards does that, and takes
an exclusive lock while it runs.

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

A single resumable (tus) upload is capped at 5 GB by default, so one runaway
transfer cannot fill the disk. The cap applies **per upload** - it does not
limit how many files agents or users transfer in a day, only how large each
one may be. Instruments producing larger single files can raise it in the
env's config toml:

```toml
[backend]
tus_max_upload_gb = 20
```

Clients see the cap as the standard `Tus-Max-Size` header; an upload declared
larger than the cap is refused up front with HTTP 413.

| Path | What |
|---|---|
| `/etc/environment` | `MASCOPE_PATH`, `LD_PRELOAD` (read by the systemd units) |
| `/etc/mascope/update.env` | update window / grace / repo, update disk floor (chmod 600) |
| `/etc/mascope/disk-check.env` | disk-monitor thresholds + alert URL (chmod 600) |
| `$MASCOPE_PATH/.runtime/secrets/` | `postgres_password.txt`, `jwt_secret_key.txt`, `server_owner_secret_key.txt`, `mfa_encryption_key.txt`, TLS cert/key, `backup.env` |
| `$MASCOPE_PATH/.runtime/database/backups/prod/` | database dumps (incl. pre-migration) |
| `$MASCOPE_PATH/.runtime/update/` | `state.json` (pending update), `status.log` |

## Troubleshooting

**Stack won't start.** `sudo systemctl status mascope.service`, then
`mascope prod ps` and `mascope prod logs backend`. Confirm Docker is up and the
secrets in `.runtime/secrets/` exist.

**The stack came back on a different release (e.g. after a reboot).**
`mascope prod doctor` shows this as `DRIFT`. The version a deploy selects comes
from the deployment checkout, so check `git -C "$(mascope path)" describe --tags`
and `systemctl cat mascope.service | grep WorkingDirectory` - a unit without a
`WorkingDirectory` runs from `/`, finds no repository, and falls back to
`latest` (re-run `./tooling/ubuntu.sh install` to fix it). The CLI logs a
warning whenever it cannot resolve a version and falls back:
`journalctl -u mascope.service | grep -i "rolling 'latest'"`.

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

**Two-factor codes stopped working for everyone** (typically after a rebuild or
a restore onto a fresh host). The seeds in the database no longer decrypt:
`mfa_encryption_key.txt` is missing, or is not the file the seeds were
encrypted under. Recovery codes still work. Put the original key file back and
restart the backend; if it is gone for good, each enrolled account signs in
with a recovery code (or is reset - see
[Two-factor authentication](#two-factor-authentication)) and enrols again.
