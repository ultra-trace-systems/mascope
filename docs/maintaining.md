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
| Copy an environment from another host | `mascope env sync user@host:<env> prod <env> prod` |
| **Require a new password from every user** | Manage users in the app, or `mascope prod db script run require_password_change` |
| **Clear a lost second factor (2FA)** | Manage users in the app, or `mascope prod mfa reset <email>` |
| What a raw file measured (its scan streams) | `mascope file scans <file.raw>` |
| Package a dataset for a repository such as Zenodo | `mascope export package <dataset id>` |
| What the method bindings would route, and where a file name disagrees | `mascope prod db script run report_method_binding_disagreements` |
| Audit a TOF instrument's m/z calibration | `TOF_AUDIT_INSTRUMENT=<name> mascope prod db script run audit_tof_calibration` |
| **Disk monitor status / run now** | `systemctl list-timers mascope-disk-check.timer` / `sudo systemctl start mascope-disk-check.service` |
| Disk monitor history | `journalctl -u mascope-disk-check.service` (fill rate and projection on each line) |
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
Version  vX.Y.Z
Disk     state, docker 142 GiB / 61% free · +0.4 GiB/day (7d), +0.3 GiB/day (30d) · full in ~355 days
         filestore 610 GiB / 31% free · +2.1 GiB/day (7d), +1.8 GiB/day (30d) · full in ~290 days
Updates  no pending migration recorded
Backups  5 local dump(s) · newest 8h ago
Images   11 images · 6.2GB (2.1GB reclaimable)
```

It exits `0` when the stack is healthy, the running images match the release
this deployment would deploy, and every filesystem is above the free-space
floor (`MASCOPE_UPDATE_MIN_FREE_GB`); `1` otherwise - so it doubles as a
monitoring probe. It exits `3`, under a `[WARNING]` header, when all of that
holds but a filesystem is filling fast enough to reach the floor within
`MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR` days (default 7, `0` disables): the Kuma
push keeps its monitor up and reports the warning on a second, optional one
(`FILLING_TOKEN`), while the fleet playbooks let a rollout carry on
past it. `--json` emits the same data for scripting.

**Disk** has one row per filesystem, naming every monitored path on it - the
state (`.runtime`), the filestore (which may be a volume of its own) and
docker's image store - with its fill rate over the last 7 and 30 days and, at
the faster of the two, when it would be full; a filesystem projected below
the floor within the horizon is flagged `FILLING`. The doctor keeps no history
of its own: it reads the samples the [disk monitor](#the-monitor-early-warning)
records every 15 minutes, so the rates appear once that timer has a day of
history, and the label in brackets is how much history a rate rests on when
that is less than its window.

**Version** compares the tag the containers are actually running against the
one this deployment would deploy right now (the `MASCOPE_VERSION` pin, else the
release tag checked out, else `latest`). A mismatch is reported as drift:

```
Version  backend latest · frontend latest  DRIFT (this checkout deploys vX.Y.Z)
```

Drift means the next `mascope prod up` - a restart, or a reboot - moves the
stack to a different release than the one it is serving. Resolve it by bringing
the two into line: `git checkout <tag>` for the release the server should run,
then reinstall the CLI from that checkout, then `mascope prod update` - in that
order, see [Rolling out a release across several
servers](#rolling-out-a-release-across-several-servers) for why the CLI has to
move before the stack does.

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

# CLI before stack: the checkout just swapped in that release's
# docker-compose.yaml, and a release that adds a compose secret has it created
# only by the matching CLI.
CFLAGS="-std=c17" uv tool install --force --reinstall --python 3.12 .   --with-executables-from mascope-cli

mascope prod update          # deploys v1.3.0; the UI then shows v1.3.0
```

This pulls the target images and does a rolling restart. Database migrations run
automatically on startup; `db_init` takes a **pre-migration dump** into
`.runtime/database/backups/prod/` first. A failed image pull aborts before the
running stack is touched. (You do **not** need `mascope prod down` first - that
only adds downtime.)

#### Running a release candidate

A **pre-release** (`vX.Y.Z-rc.N`, also `-beta.N` / `-alpha.N`) deploys exactly
like a release - `--version v2.0.0-rc.1`, or check the tag out - and the UI
reports it as `v2.0.0-rc.1`. Nothing that follows releases on its own will
adopt it: `releases/latest` excludes a pre-release, and that is what `--auto`,
the update timer and the in-app **Download File Agent** button all read. So
pair the machines of a piloting site from the pre-release's own versioned
installer asset on its GitHub release page.

Three things to get right, because a candidate is normally short-lived:

- **CLI before stack**, as for any release and more so: the candidate's
  `docker-compose.yaml` can want a secret only the matching CLI creates, and a
  candidate cut on `develop` is further from the installed CLI than a release
  ever is. Check the tag out, reinstall the CLI from that checkout, then
  update - the recipe above. Deploying with `--version` alone moves the images
  while the checkout stays put until the update succeeds, so the stack starts
  under the *previous* checkout's compose file; prefer the tag checkout for a
  candidate.
- **Migrations are forward-only.** A candidate's schema changes are the
  release's schema changes; going back to the previous release means restoring
  the pre-migration dump `db_init` just took, losing everything written since.
  Confirm you can restore *before* updating, not after.
- **Pin it where the boot service reads it.** If you deploy by checking the tag
  out, `mascope prod update` aligns the checkout for you and a reboot redeploys
  the same candidate. If you instead pin by hand, put
  `MASCOPE_VERSION=vX.Y.Z-rc.N` in `/etc/environment` (the unit's environment),
  not just in your shell - a pin only your shell can see leaves the boot
  service deploying something else against a database the candidate has already
  migrated.

You can leave the update timer enabled. `--auto` never moves a deployment
backwards: it compares the newest release against what this deployment would
boot, and when the candidate already supersedes it the run reports "nothing to
do" and changes nothing. Without that check the timer would read the older
release as a pending update and apply it - reverting the pilot silently, or
booting images whose migrations do not know the revision the candidate has
already written to the database. Leaving a candidate is therefore always a
deliberate step: `mascope prod update --version vX.Y.Z`. The timer picks the
site back up on its own once the real `vX.Y.Z` ships, because that supersedes
the candidate.

### Unattended updates (the timer)

`mascope-update.timer` runs `mascope prod update --auto` nightly. It is
installed **disabled**. `--auto` automatically tracks the newest GitHub
**release tag** (`vX.Y.Z`) - there is no version to pin by hand. It only ever
moves forward, so a deployment already running something newer than the newest
release (a release candidate; see above) is left alone. To turn it on:

1. Make sure the stack is running - the applied database revision is read from
   the live Postgres container. **No credentials are needed**: `--auto` reads
   the public GitHub releases API over plain HTTPS.
2. Enable the timer (adjust the window / grace first if you like):

   ```sh
   sudoedit /etc/mascope/update.env      # optional: MASCOPE_UPDATE_WINDOW, grace
   sudo systemctl enable --now mascope-update.timer
   ```

Each run:

- **Already ahead** (this deployment supersedes the newest release) -> nothing.
- **Up to date** -> nothing.
- **Fast update** -> applied inside the maintenance window
  (`MASCOPE_UPDATE_WINDOW`, e.g. `2-5`), then health-checked. A failed health
  check **alerts and stops - it never rolls back automatically** (see
  Troubleshooting).
- **Migration update** -> recorded and reported (exit 30), then applied at the
  next window once its grace period elapses (`MASCOPE_UPDATE_GRACE_DAYS`,
  default 7 days) **or** you confirm it - unless it has been snoozed.
  Deploying that release (or a later one) yourself with `mascope prod update`
  settles the record as well, so `prod doctor` stops reporting it as pending;
  rolling back to an older release keeps it.
- **Failed earlier** (an apply of this release did not come up healthy: its
  migration failed, or the backend never turned healthy) ->
  held: not applied again, and the run exits 2 every time until you act, so
  the timer does not undo your rollback. `mascope prod update --confirm`
  retries it once at the next window; `--snooze <days>` keeps it held but
  quiet for those days. A newer release is tried as usual. Deploying the
  failed release by hand (`mascope prod update --version <tag>`) lifts the
  hold too. If you fixed it in place instead (a config fix, then
  `mascope prod up`) and it now runs healthily, `--confirm` acknowledges
  that: the next run finds the stack up to date and only clears the record.

An applied update also moves the deployment checkout to the release it
deployed, so a reboot redeploys that same release and `mascope prod doctor`
stays clean.

!!! warning "Reinstall the CLI after an unattended update"

    The updater deliberately never reinstalls itself - a self-reinstall
    mid-run is risky, more so unattended - but it *does* move the checkout,
    which installs the new release's `docker-compose.yaml` beside the **old**
    CLI. `prod doctor` will not catch this: it compares the image tag the
    checkout would deploy against the running containers, and both are already
    on the new release, so it reports clean.

    The next `mascope prod up` - a restart, or a reboot - then drives the new
    compose file with the old CLI. If the release added a compose secret, as
    1.8.0 does with `mfa_encryption_key`, compose refuses to create the backend
    *after* stopping the running one, and the server comes up with no backend.

    So when the timer reports a release applied, reinstall the CLI from the
    checkout before the next restart:

    ```sh
    cd "$(mascope path)"
    CFLAGS="-std=c17" uv tool install --force --reinstall --python 3.12 .       --with-executables-from mascope-cli
    ```

    For a release with migrations this is worth doing deliberately rather than
    leaving to the timer - see [Rolling out a release across several
    servers](#rolling-out-a-release-across-several-servers), which puts the CLI
    first by design.

The checkout move is deliberately cautious - it never discards local
changes - so on a checkout with modifications (or one whose `origin` cannot
be reached to fetch the tag) the update still succeeds with a warning, and
doctor reports the gap as DRIFT until the checkout is aligned by hand:

```sh
cd "$(mascope path)"
git fetch --tags && git checkout vX.Y.Z   # the release the update applied

# Reinstall in the same breath. The unattended updater deliberately never
# reinstalls itself, so aligning the checkout alone leaves the new release's
# docker-compose.yaml paired with the old CLI - and the next reboot's
# `mascope prod up` runs that pairing.
CFLAGS="-std=c17" uv tool install --force --reinstall --python 3.12 .   --with-executables-from mascope-cli
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

# CLI first, stack second. The checkout above already swapped in the release's
# docker-compose.yaml, and a release that adds a compose secret (1.8.0 adds
# mfa_encryption_key) has that file created only by the matching CLI - the
# older one stops the backend and then cannot recreate it.
CFLAGS="-std=c17" uv tool install --force --reinstall --python 3.12 .   --with-executables-from mascope-cli

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

- **The CLI is not refreshed by `prod update` - and it has to be refreshed
  first.** `prod update` only pulls images; the `mascope` binary is a `uv` tool
  installed by `tooling/ubuntu.sh`. Reinstall it between the checkout and the
  update, as in the sequence above: the checkout brings in the release's
  `docker-compose.yaml`, and a release that adds a compose secret (1.8.0 adds
  `mfa_encryption_key`) has it provisioned only by the matching CLI - compose
  then refuses to recreate the backend it has just stopped. Prefer the
  `uv tool install` line over `ubuntu.sh reinstall`: reinstall goes through the
  uninstall path, which deletes `.runtime/state.json` and with it the server's
  active environment. `prod update` still warns when the running CLI has
  drifted from the checkout - but by then the update has already run.
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
# and nothing quarantined: failed_files sits beside the streams folder
find "$(mascope path)"/.runtime/env/* -type d -name failed_files
```

Errors that keep arriving after the restart window, or any `failed_files`
directory with contents, are real and worth investigating. Resolve the
restart-window issues in the error tracker so a recurrence stands out.

A file the converter found in the streams folder with no upload registered
for it - one left there across a converter restart - can never be stored,
and its uploader cannot be told. Every active global admin and owner is told
instead, under "Needs attention" in the notifications pane, as "Upload
lost" with the file's name. The backend also looks through `failed_files` at
every start and names each file there that no record was made of, so a file
the converter could not report reaches them too. The startup scan names each
file to a person once; the converter names a file again if it is lost again.
The scan also finds files the converter could not read, whose uploader was
told why at the time, so the pane says under each file how it was found.
Upload a lost file again from the instrument, or from `failed_files`, and
then delete it from the folder; a file that could not be read needs looking
at first.

## Backups

[`tooling/backup-cron.sh`](../tooling/backup-cron.sh) runs a two-layer nightly
backup from cron:

1. **Local dumps** — `mascope prod db backup create` writes a compressed `.dump`
   file (PostgreSQL custom format, `pg_dump -Fc`) of the **active environment's
   database** to `.runtime/database/backups/prod/`, then prunes dumps older than
   `LOCAL_RETENTION_DAYS` (default 7). These give fast same-host restores.
2. **Encrypted off-site copy** — [restic](https://restic.net/) pushes the dump
   directory **and the filestore** (uploaded raw files) to an encrypted remote
   repository, then applies the `KEEP_DAILY`/`KEEP_WEEKLY`/`KEEP_MONTHLY`
   retention policy (defaults 7/4/6). These survive host loss.

Off-site backups are encrypted client-side by restic, so the storage provider
never sees plaintext — this is what lets the privacy notice claim encrypted
backups (GDPR Art. 32).

Neither layer includes `.runtime/secrets/` - deliberately, so no backup medium
holds both the database and the keys that make its secrets usable. The flip
side: restoring onto a fresh host needs the secrets restored separately. Keep
a copy of the secrets files wherever the deployment's other credentials live -
the [two-factor encryption key](#two-factor-authentication) in particular
cannot be regenerated, only lost.

### Prerequisites

```bash
which mascope   # /home/<user>/.local/bin/mascope
which docker    # /usr/bin/docker
which restic    # /usr/bin/restic (installed by tooling/ubuntu.sh)
echo $MASCOPE_PATH   # e.g. /home/<deploy-user>/mascope (from /etc/environment)
```

### Setup

1. **Create the off-site repository.** Any restic-supported backend works:
   - *Hetzner Storage Box* — enable SSH support in the Storage Box settings and
     install the host's SSH key (`ssh-copy-id -p 23 u123456@u123456.your-storagebox.de`).
   - *S3-compatible object storage* (e.g. Contabo Object Storage) — create a
     bucket and an access key pair.
   - *Own backup server* — see below.

   Prefer the **other** hosting provider than the one the server runs on, so a
   provider-level incident cannot take out both the service and its backups.

   #### Using your own backup server

   A machine with large disks (e.g. an office server) can serve as the backup
   target over plain SFTP. Do not expose its SSH port to the internet — connect
   the VPSes and the backup server through a VPN overlay instead
   ([Tailscale](https://tailscale.com/) is the quickest: install on both ends,
   no inbound router ports needed).

   Give **every backed-up server an account of its own** on the backup server,
   one that can do nothing but SFTP into its own directory. The obvious setup,
   one shared account with an ordinary shell, hands every server a key that can
   delete or overwrite every other server's backups, open a shell on the backup
   server and tunnel through it into the rest of the network: one compromised
   server would cost you all of them. The commands below use a group
   `site-backup`, an account `backup-<server>` per backed-up server, a chroot
   per account under `/srv/site-backups` (put it on the big disk) and the keys
   in `/etc/ssh/site-backup-keys`; any names work. `<server>.pub` is the public
   half of the key that server's backup cron uses (made further down), and
   `203.0.113.10` stands for that server's VPN address (`tailscale ip -4`).

   On the backup server, once — the group, two root-owned directories, and an
   sshd drop-in that applies to every member of the group:

   ```bash
   sudo groupadd --system site-backup
   sudo install -d -o root -g root -m 755 /srv/site-backups /etc/ssh/site-backup-keys
   sudoedit /etc/ssh/sshd_config.d/30-site-backups.conf
   ```

   ```
   # Backups: one sftp-only account per backed-up server (group site-backup).
   # Each sees only its own chroot, its repository at /repo; no shell, no
   # terminal, no tunnels. Its keys sit in a root-owned file named after it.
   Match Group site-backup
       ChrootDirectory /srv/site-backups/%u
       ForceCommand internal-sftp
       AuthorizedKeysFile /etc/ssh/site-backup-keys/%u
       DisableForwarding yes
       PermitTTY no
   Match all
   ```

   The closing `Match all` is load-bearing. A `Match` block runs until the
   next `Match` line or the end of the file, so without it a line added to
   this file later — or the block pasted into the middle of `sshd_config` —
   would quietly apply to the backup accounts alone. `Match all` ends the
   block; whatever follows it is global again.

   Then for each backed-up server — an account with no password and no shell,
   its chroot, and its key:

   ```bash
   sudo useradd --system --user-group --groups site-backup --no-create-home \
     --home-dir /nonexistent --shell /usr/sbin/nologin backup-<server>
   # sshd refuses a chroot that is not root's or that anyone else can write;
   # the account cannot create its repository directory there, so make it
   sudo install -d -o root -g root -m 755 /srv/site-backups/backup-<server>
   sudo install -d -o backup-<server> -g backup-<server> -m 700 \
     /srv/site-backups/backup-<server>/repo
   printf 'restrict,command="internal-sftp",from="%s" %s\n' \
     203.0.113.10 "$(cat <server>.pub)" \
     | sudo tee /etc/ssh/site-backup-keys/backup-<server>
   sudo chmod 644 /etc/ssh/site-backup-keys/backup-<server>
   ```

   In the key line, `restrict` turns off forwarding, the terminal and
   `~/.ssh/rc`; `command="internal-sftp"` keeps the key to SFTP even without
   the drop-in; `from=` refuses it from any address but that server's own, so
   a copy of the key is no use anywhere else (it takes a comma-separated list
   if the server may also connect over IPv6). Adding a server later needs no
   reload: sshd reads the account and its key file at login.

   Check the drop-in before reloading sshd, and keep an automatic revert armed
   while you test, so a mistake cannot lock you out of the backup server:

   ```bash
   sudo sshd -t     # syntax; prints nothing when the config is valid
   # a backup account: expect its chroot, internal-sftp, its key file,
   # "disableforwarding yes" and "permittty no"
   sudo sshd -T -C user=backup-<server>,host=<server>,addr=203.0.113.10 \
     | grep -E '^(chrootdirectory|forcecommand|authorizedkeysfile|disableforwarding|permittty) '
   # your own account: chrootdirectory and forcecommand must still be "none"
   sudo sshd -T -C user="$USER",host=<your-machine>,addr=<your-address> \
     | grep -E '^(chrootdirectory|forcecommand) '
   # takes the drop-in out again in 15 minutes unless stopped
   sudo systemd-run --on-active=15min --unit=site-backup-revert \
     /bin/sh -c 'rm -f /etc/ssh/sshd_config.d/30-site-backups.conf && systemctl reload ssh'
   sudo systemctl reload ssh   # the unit is `ssh` on Debian/Ubuntu, often `sshd` elsewhere
   ```

   Keep your current session open, test a fresh login of your own and the
   backed-up server (below), then `sudo systemctl stop site-backup-revert.timer`.
   The revert fails closed: without the drop-in, sshd looks for the backup
   accounts' keys in their (nonexistent) home and refuses them.

   On each backed-up server, as the user that runs the backup cron, make the
   key (no passphrase: cron cannot type one), copy its `.pub` to the backup
   server as `<server>.pub` for the step above, and give the backup server an
   ssh alias that names the account and the key:

   ```bash
   ssh-keygen -t ed25519 -N "" -C "backup@$(hostname)" -f ~/.ssh/id_ed25519_backup
   ```

   ```
   # ~/.ssh/config
   Host <backup-host>
       HostName 203.0.113.20        # the backup server's VPN address
       User backup-<server>
       IdentityFile ~/.ssh/id_ed25519_backup
       IdentitiesOnly yes
   ```

   Connect once by hand to accept the backup server's host key (cron cannot
   answer that prompt; compare the fingerprint with
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` there), and check what
   the account can do:

   ```bash
   sftp <backup-host>       # accept the host key; `ls /` then shows only /repo
   ssh <backup-host> true   # refused: "This service allows sftp connections only."
   ```

   In `backup.env` the repository is then the alias and the path the account
   sees inside its chroot — the same `/repo` on every server:

   ```bash
   RESTIC_REPOSITORY=sftp:<backup-host>:/repo
   ```

   Moving from one shared account: take one server at a time, while its
   backup is not running (its repository's `locks/` directory is empty). Set
   up its account as above, but instead of creating an empty `repo/`,
   `sudo mv` its repository there (on the same filesystem that is a rename)
   and hand it over (`sudo chown -R backup-<server>:backup-<server>`,
   `sudo chmod 700`); move its key line
   out of the shared account's `authorized_keys` into the new key file; then
   change `User` in the server's alias and its `RESTIC_REPOSITORY` to
   `sftp:<backup-host>:/repo`. Once the shared account holds no keys, give it
   `/usr/sbin/nologin` (`sudo usermod -s /usr/sbin/nologin <account>`).

   This contains a compromised server; it does not protect that server's own
   backups from it. restic over SFTP cannot be append-only: every server
   prunes its own repository (`tooling/backup-cron.sh` runs
   `restic forget --prune` after each push), so its account must be able to
   delete, and an attacker who holds the server can delete its history — but
   no longer anyone else's. The alternative is restic's
   [rest-server](https://github.com/restic/rest-server) with `--append-only`
   (and `--private-repos`, so each user reaches only its own repository), with
   the pruning moved to the backup server, which then needs every
   repository's password. `backup-cron.sh` always prunes, so that route needs
   the script changed as well.

   Caveats: the backup server needs disk redundancy (RAID/ZFS) and monitoring
   like any other piece of infrastructure, and its location is a
   data-processing location (Art. 30 record / DPA). Keep the drop-in and
   `/etc/ssh/site-backup-keys` in the backup server's own configuration
   backup — a rebuilt box without them refuses every server.
   Migrating to a hosted target later is just a `RESTIC_REPOSITORY` change
   (`restic init` on the new target, or `restic copy` to carry snapshots over).

2. **Configure the host:**

   ```bash
   cp tooling/backup.env.example "$MASCOPE_PATH/.runtime/secrets/backup.env"
   chmod 600 "$MASCOPE_PATH/.runtime/secrets/backup.env"
   # then edit: repository, encryption password, FILESTORE_PATH, retention

   # the backend image runs as UID 1000, so new dumps land owned by the
   # deploy user — but dumps created by releases older than that (or by the
   # docker daemon creating the mount dir) may still be root-owned. Hand
   # them to the user that runs the cron job once, or pruning fails:
   sudo chown -R "$(whoami)" "$MASCOPE_PATH/.runtime/database/backups"
   ```

   ⚠️ Store the `RESTIC_PASSWORD` in the company password manager **before the
   first run** — without it the backups are unrecoverable, by design.

3. **Test manually** before relying on cron:

   ```bash
   MASCOPE_PATH=$MASCOPE_PATH bash tooling/backup-cron.sh
   restic snapshots   # with backup.env sourced — should list the new snapshot
   ```

4. **Install the cron job** (`crontab -e`). Cron does **not** load
   `/etc/environment` or shell profiles, so the header must set the environment
   explicitly:

   ```
   SHELL=/usr/bin/bash
   PATH=/home/<deploy-user>/.local/bin:/usr/bin:/usr/local/bin
   MASCOPE_PATH=/home/<deploy-user>/mascope

   # Nightly backup at 4 AM: local dump + prune + encrypted off-site push
   0 4 * * * $MASCOPE_PATH/tooling/backup-cron.sh 2>&1 | logger -t mascope-backup
   ```

   Output goes to syslog: `grep mascope-backup /var/log/syslog | tail -20`.

   The backend image runs as UID 1000 (override with the `MASCOPE_UID` /
   `MASCOPE_GID` build args), so filestore files are owned by the deploy user.
   Files written by releases that still ran as root need a one-time
   `sudo chown -R "$(whoami)" <filestore>` — until then a user-level cron
   cannot read them.

5. **Monitoring (recommended):** set `HEALTHCHECK_URL` in `backup.env` to a
   [healthchecks.io](https://healthchecks.io) ping URL. The script pings it on
   success and `<url>/fail` on failure, so a silently broken cron job (the
   classic backup failure mode) still raises an email alert.

### Restoring

```bash
# Same-host restore from a local dump (list, then restore):
mascope prod db backup list
mascope prod db restore

# Disaster recovery from off-site (new host):
set -a; source "$MASCOPE_PATH/.runtime/secrets/backup.env"; set +a
restic snapshots                                  # pick a snapshot
restic restore latest --target /tmp/mascope-restore
# then: mascope prod db restore from the recovered .dump file, and copy the
# recovered filestore back to the MASCOPE_FILESTORE path.
```

Do a **restore drill** (restore latest snapshot to a scratch directory and
`pg_restore --list` the dump) after first setup and then periodically — an
untested backup is not a backup.

### Notes

- All `mascope prod db backup` commands operate on the **active environment's
  database only**; deletion never touches other environments' dumps.
- `pg_dump` for large databases (tens of GB) can take several minutes — the
  dump file grows incrementally while in progress. restic uploads are
  incremental/deduplicated, so nightly pushes are much smaller than the first.
- **Large databases: dump uncompressed.** `pg_dump -Fc` compresses with
  single-core gzip, which is both the dump-time bottleneck and the reason
  restic cannot deduplicate consecutive dumps (each night re-uploads the full
  dump). Set in the env's `prod.mascope.toml`:

  ```toml
  [backend.database]
  dump_compression = "0"
  ```

  This speeds up nightly dumps *and* the pre-migration dump during upgrades
  (shorter downtime window), and lets restic deduplicate and zstd-compress
  dump generations off-site. Uncompressed dumps take 2–4× more local disk —
  lower `LOCAL_RETENTION_DAYS` in `backup.env` to compensate. One-off
  override: `mascope prod db backup create --compress 0`.
- Backup filenames embed a timestamp and the `cron` label, e.g.
  `mascope_test_env_20260313_040001_cron.dump`.
- The script chains dump create and prune sequentially to avoid simultaneous
  writes to `state.json`.
- Pre-migration dumps created by `tooling/db-init.sh` land in the same
  `.runtime/database/backups/prod/` directory, so they are pushed off-site and
  pruned locally by the same run. Use https://crontab.guru to adjust schedules.

### Pre-migration dumps

Separately from the backup cron, `db_init` takes a **pre-migration dump** into
`.runtime/database/backups/prod/` whenever a migration update runs on startup.
To keep these from piling up on a server that has auto-updates but no backup
cron, `db_init` keeps only the most recent `MASCOPE_PREMIGRATION_KEEP` of them
(default 5) and prunes older ones - it only ever touches `*_pre-migration.dump`
files, never the cron/manual dumps. Raise the count (or set up the backup cron
above) if you want a longer local history.

## Copying an environment

`mascope env sync` copies a runtime environment - its filestore and its database - to another env, on the same host or to or from a remote one: the way to stand a test server up on a copy of a site's data, or to move a deployment to a new host. It transfers both the filestore (via [rsync](https://linux.die.net/man/1/rsync)) and the PostgreSQL database (via `pg_dump`/`pg_restore`
staged through .runtime/database/transfer/). Both source and target require an explicit mode (dev or prod) to identify which PostgreSQL server to use.

> [!IMPORTANT]
> Both PostgreSQL containers must be running on source and target machines before executing `env sync`.

Usage examples:

```bash
# local → local
mascope env sync default dev foo dev

# local → remote
mascope env sync foo prod user@192.168.1.100:bar prod

# remote → local, database only
mascope env sync user@192.168.1.100:bar prod baz prod --skip-filestore

# local → local, filestore only
mascope env sync foo dev bar dev --skip-db
```

The `sync` command follows symbolic links on Linux filesystem both ways - syncing into and from a symlinked directory.

If the target environment does not exist, you will be prompted to create it. Pass `--yes` to skip the prompt.

On transfer failure the staged dump is preserved in `.runtime/database/transfer/` for manual recovery. On success it is deleted and 7-day retention pruning runs automatically.

### Syncing only part of the filestore

A full filestore can be far larger than the link between the two machines. `--from` and `--to` (both `YYYY-MM-DD`, both inclusive, either one optional) narrow the transfer to a window of acquisition dates:

```bash
# everything acquired since the start of the year
mascope env sync user@192.168.1.100:bar prod baz prod --from 2026-01-01

# one quarter, filestore only
mascope env sync user@192.168.1.100:bar prod baz prod --from 2026-01-01 --to 2026-03-31 --skip-db
```

The filter is a path filter, not a database query: the filestore stores samples as `filestore/<instrument>/<YYYY.MM.DD>/<sample_name>/`, where the date directory comes from the acquisition timestamp in the sample filename. So the window selects on **acquisition date**, not on when a file was uploaded, reprocessed or last modified. The batch cache under `filestore/sample_batches/` has no date in its path and is always transferred; it is rebuildable, so dropping it would only cost recomputation.

> [!IMPORTANT]
> `--from` / `--to` filter the filestore only - **the database is never filtered**. A date-limited sync that also syncs the database leaves the target holding sample rows whose files were not transferred, and those samples fail to load. The command warns when you do this; add `--skip-db` to sync only the files.

If no filestore data falls inside the window the sync fails rather than silently transferring nothing. When the source is on a **remote** host it also fails if the filestore cannot be listed in full - a mistyped env name, a missing filestore, an unreadable directory - rather than filtering against a partial listing and reporting success on an incomplete transfer.

> [!NOTE]
> Two cases still read as an empty window rather than as a failure. `find -L` skips a dangling symlink silently and exits 0, so a filestore whose data volume is not mounted looks empty; and a local source is listed with a glob that cannot report an error at all, so an unreadable directory there narrows the transfer without a word. Check the target after a sync that reports fewer dates than you expected.

### Ownership and permissions after sync

rsync is run without `-o`/`-g`, so ownership is not carried across: every file lands owned by whoever runs the **receiving** side - the SSH login user for a push, the invoking user for a pull or a local sync. Mascope's containers run as the deployment's own uid (1000 by default; see `MASCOPE_UID` in `server/backend/Dockerfile`), so if those two differ the app cannot read or write what was just synced.

The sync detects this by comparing the receiving uid against the owner of the target env directory, and prints the exact command to fix it:

```
sudo chown -R 1000:1000 /srv/mascope/.runtime/env/default
```

Pass `--chown` to have the sync attempt that itself. It uses `sudo -n` (non-interactive) on the receiving machine and falls back to printing the command when passwordless sudo is not available - the sync is not failed either way.

If the filestore is a symlink to a data volume (see [The filestore on a data volume](#the-filestore-on-a-data-volume)), chown its target too: `chown -R` does not follow symlinks. `--chown` resolves the link and covers both paths.

File modes are forced to `755` for directories and `644` for files - the same bits the backend writes under the default umask, so a synced tree is indistinguishable from a natively written one. Note that this also applies on a **re-sync**: modes hand-tightened on an existing target filestore are reset to `755`/`644`.

## Packaging a dataset for deposit

A user who publishes their data deposits it in a data repository such as
Zenodo, which keeps it and mints its DOI. `mascope export package` writes what
they upload: one zip archive of a dataset, holding its raw files, its batch
spreadsheets, its target collections, a README that says what was measured,
how, under what licence, by whom and with which software, and a manifest with
the SHA-256 of every file. The user guide's
[Export files](user/reference/exports.md#the-deposit-package) lists what it
holds.

```bash
mascope export package <dataset id>                 # into the current directory
mascope export package <dataset id> -o ~/deposits/  # into a directory
mascope export package <dataset id> -o deposit.zip  # under a name of your own
```

The dataset id is in the app's address bar, after `d=`, when the dataset is
open. The archive is named `mascope-package_<dataset id>_<dataset name>.zip`,
and an archive already at the output path is replaced. An output path that
ends in `/` is a directory, made if it is missing; its parent must exist.

The archive is built inside the running backend container, from the database
and filestore of the env the stack runs, in a directory of the run's own under
that env's `temp/packages/`. Then it is copied to the output path and removed
from the container. Two runs at once, of one dataset too, do not meet. Ctrl-C
stops the build in the container as well, and it removes what it had written.
The stack must be up. While the archive is built it needs the archive's size
free on the env's disk, and again where it is copied. A developer's source
checkout builds the package itself instead, from the active env's dev database
and filestore; `--in-container` asks for the container there too.

The archive holds every raw file of the dataset, deflated: plan for most of
the raw files' size, and a few seconds per hundred megabytes. Zenodo takes
up to 50 GB a record.

The command reads whatever dataset it is given, whichever workspace it is in:
whoever runs it has the database. When it is done it says what it wrote, and
warns when the archive is not ready to deposit as it stands:

- **No licence, or no authors.** A repository asks for both. The user adds
  them to the dataset in the app, and the package is built again.
- **Sample files with no raw file.** The filestore keeps only the data
  converted from them, so the package describes them without the file.
- **Raw files changed since upload.** Their SHA-256 is not the one recorded
  when they were uploaded. The manifest gives both. A file that changed on the
  server's disk is worth looking into before it is published.

Hand the archive to the user to check its README and upload it. Once the
repository has minted a DOI, it goes into the dataset's related identifiers,
so that a package built later points at the deposit.

## Disk space

A full disk is the classic way to take the whole stack down: Postgres cannot
write and wedges. Everything that grows lands on the host - the Postgres data,
the filestore (uploaded raw files) and dumps under `.runtime/`, and docker's
image store under `/var/lib/docker` - usually sharing one filesystem. Three
guards keep it from filling silently, and a fourth - the
[upload disk floor](#upload-disk-floor) - refuses new uploads once the spool's
filesystem runs short.

### The monitor (early warning)

`tooling/ubuntu.sh` installs and **enables** `mascope-disk-check.timer`, which
runs [`tooling/disk-check.sh`](../tooling/disk-check.sh) every 15 minutes. It
never deletes anything - it measures free space on the `.runtime` filesystem,
each env's filestore when that is a symlink to a volume of its own, and the
docker filesystem, and reports to the journal. When a filesystem drops below
the floor it pings a healthchecks.io-style URL so you are alerted with lead
time; when one is filling fast enough to reach the floor soon, it pings a
second one.

A floor alone gives a disk that fills fast little warning: at 19 GiB a day, a
10% floor on a 1 TiB disk is crossed about five days before the disk is full.
So each run also appends the free space to
`$MASCOPE_PATH/.runtime/disk-check/history.tsv` (the only file it writes besides
a lock beside it, kept to the last 60 days), fits how many GiB a day the
filesystem has been filling
over the last 7 and 30 days, and projects when it reaches the floor and when
it is full, at the faster of the two rates - the 7-day rate catches an
instrument that has just started a long run, the 30-day rate keeps a quiet
week from hiding a disk that fills in bursts. Every journal line carries it:

```
[disk-check] ok:  state (.runtime) (/) has 647.3 GiB / 37% free; rate +2.1 GiB/day (7d), +1.8 GiB/day (30d); 174.9 GiB floor in ~225 days, full in ~308 days
```

The rate is a least-squares fit of *used* space over every sample in the
window, so growing the filesystem does not read as freeing space and one
reading taken while a dump is being written is not projected forward. Freeing
space does lower the rate for as long as the cleanup stays inside the window,
so after a large cleanup the projection is optimistic for a while - the floor
still applies. Rates appear after a day of history.

Configure it in `/etc/mascope/disk-check.env` (chmod 600, template
[`tooling/disk-check.env.example`](../tooling/disk-check.env.example)):

- `MIN_FREE_GB` (default 10) - absolute floor; the "about to crash" signal.
- `MIN_FREE_PCT` (default 10) - percentage floor; an earlier warning. Set to
  `0` on a very large disk to avoid paging while tens of GiB are still free.
- `MIN_DAYS_TO_FLOOR` (default 30) - warn when the filesystem is projected
  to drop below the floor within this many days. `0` disables the warning; the
  rates are still logged. An env file seeded before this setting existed does
  not list it, and the default applies. A threshold that is not a plain number
  is logged and replaced by its default.
- `HEALTHCHECK_URL` - **set this to actually get alerted.** Every run pings
  the URL while no filesystem is below the floor (so a stalled monitor is
  itself flagged) and `<url>/fail` while one is. Use a check separate from the
  backup one.
- `HEALTHCHECK_FILLING_URL` - a **second** check, for the fill-rate warning:
  `<url>/fail` while a filesystem is projected to reach the floor within
  `MIN_DAYS_TO_FLOOR` days, or is below it, and `<url>` otherwise - so it does
  not report "recovered" at the moment the floor is crossed. It is kept apart
  because healthchecks.io notifies on a change of state: one check already
  down for the warning would say nothing when the floor is actually crossed.
  Without it, the warning is journalled and recorded as a `/log` event on
  `HEALTHCHECK_URL`, which notifies no one.

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

## PostgreSQL tuning

Settings live in `[backend.database]` in `.mascope.toml` layer files.
Postgres tuning parameters are passed as `-c` flags via Docker Compose command override.
`shm_size` is a Docker field - applied via `shm_size:` in compose, never passed to postgres.

**References:**

- [PostgreSQL Parameter Tuning Best Practices - mydbops](https://www.mydbops.com/blog/postgresql-parameter-tuning-best-practices)
- [How to Tune Database Parameters - EnterpriseDB](https://www.enterprisedb.com/postgres-tutorials/comprehensive-guide-how-tune-database-parameters-and-configuration-postgresql)
- [PostgreSQL Resource Consumption Docs](https://www.postgresql.org/docs/current/runtime-config-resource.html)

### Recommended Values

| Setting                        | PG Default | Base (dev, ~16 GB SSD) | Prod (64 GB NVMe) |
| ------------------------------ | ---------- | ---------------------- | ----------------- |
| `max_connections`              | 100        | 100                    | 200               |
| `shared_buffers`               | 128MB      | 512MB                  | 16GB              |
| `effective_cache_size`         | 4GB        | 4GB                    | 48GB              |
| `work_mem`                     | 4MB        | 32MB                   | 64MB              |
| `maintenance_work_mem`         | 64MB       | 512MB                  | 4GB               |
| `autovacuum_work_mem`          | -1         | -1                     | 2GB               |
| `wal_buffers`                  | auto ~16MB | 16MB                   | 16MB              |
| `min_wal_size`                 | 80MB       | 512MB                  | 1GB               |
| `max_wal_size`                 | 1GB        | 2GB                    | 4GB               |
| `checkpoint_completion_target` | 0.9        | 0.9                    | 0.9               |
| `wal_compression`              | off        | on                     | on                |
| `random_page_cost`             | 4.0        | 1.1                    | 1.1               |
| `effective_io_concurrency`     | 1          | 200                    | 200               |
| `default_statistics_target`    | 100        | 100                    | 100               |
| `jit`                          | on         | off                    | off               |
| `autovacuum_max_workers`       | 3          | 3                      | 4                 |
| `shm_size` (Docker)            | 64m        | 1g                     | 20g               |

### Connections

- **`max_connections`** — Server-side cap on concurrent connections. Size it from the app:
  `workers × (pool_size + max_overflow)` plus ~10 headroom (`superuser_reserved_connections`
  defaults to 3, plus `db_init`, backup dumps, and interactive psql). Each idle connection
  costs only a few MB, so a generous cap is cheap — but every *active* connection can
  allocate `work_mem` per operation, so check the `work_mem` formula below when raising it.
  Requires a postgres container restart.

### Memory

- **`shared_buffers`** — PostgreSQL's dedicated data page cache. The single largest performance
  lever - pages cached here cost zero I/O on repeated reads. Rule: **25% of RAM**. Above ~40%
  competes with the OS page cache and yields diminishing returns. PostgreSQL's 128MB default is
  a historical artifact.

- **`effective_cache_size`** — Planner hint only, **no memory is allocated**. Tells the planner
  how much total cache (shared_buffers + OS page cache) is available. Too low causes the planner
  to prefer sequential scans over index scans on large tables. Set to **75% of RAM**.

- **`work_mem`** — Per-operation memory for sorts, hash joins, hash aggregates, window functions.
  **Allocated per operation per connection** - a query with three sort nodes uses 3× work_mem.
  Too low causes disk spills (10–100× slower). Worst-case formula:

  ```
  workers × (pool_size + max_overflow) × ops_per_query × work_mem
  # Prod: 12 × 10 × 3 × 64MB = ~22.5GB theoretical worst case - acceptable on
  # 64GB next to shared_buffers=16GB (assumes every connection running 3 sorts
  # at once, which real workloads never reach)
  ```

  Diagnostic: set `log_temp_files = 0` and watch for spill entries in logs.

- **`maintenance_work_mem`** — Memory for `VACUUM`, `CREATE INDEX`, `ALTER TABLE`, `pg_restore`.
  Only a few maintenance ops run concurrently so this can be set high safely.

- **`autovacuum_work_mem`** — Memory per autovacuum worker. `-1` inherits `maintenance_work_mem`.
  Set explicitly in prod so background autovacuum workers don't consume the full
  `maintenance_work_mem` allocation.

- **`wal_buffers`** — Shared memory buffer for WAL records before flush to disk. Auto-tunes to
  1/32 of `shared_buffers`, capped at 16MB. Set explicitly so it's visible in config.
  **Requires container restart** (shared memory allocation).

### Checkpoints and WAL

- **`min_wal_size` / `max_wal_size`** — WAL files below `min_wal_size` are recycled rather than
  deleted. `max_wal_size` is the ceiling before a checkpoint is forced. Too small causes I/O
  spikes from forced checkpoints. Scale with `shared_buffers` - a larger buffer pool accumulates
  more dirty data between checkpoints.

- **`checkpoint_completion_target`** — Fraction of the checkpoint interval over which dirty page
  writes are spread. At `0.9`, writes are distributed evenly - no I/O spike at checkpoint
  boundary. Default since PG14 but set explicitly for visibility.

- **`wal_compression`** — Compresses WAL records before writing. Reduces WAL volume and disk
  usage at minor CPU cost. `on` uses zlib - available in standard `postgres:16-alpine`.
  `lz4` requires PostgreSQL compiled with `--with-lz4` (**not in the standard image**).

### Planner

- **`random_page_cost`** — Cost of a random page fetch relative to sequential (always 1.0).
  Default 4.0 assumes spinning disk - causes the planner to avoid index scans and prefer
  sequential scans even when an index would be faster. Set to **1.1 for SSD/NVMe** where random
  reads are nearly as fast as sequential. Getting this wrong causes full table scans on large
  tables that have usable indexes.

- **`effective_io_concurrency`** — Concurrent I/O requests for bitmap heap scans. Default 1
  assumes a single spinning disk. Set to **200 for SSD/NVMe**. No effect on sequential scans.

- **`default_statistics_target`** — Histogram depth per column collected during `ANALYZE`. Higher
  values give the planner more accurate row estimates for skewed data. PostgreSQL default 100 is
  adequate as a baseline. For heavily skewed columns (isotope counts, intensity values), set
  per-column:

  ```sql
  ALTER TABLE match_isotopes ALTER COLUMN intensity SET STATISTICS 500;
  ANALYZE match_isotopes;
  ```

- **`jit`** — JIT compilation benefits long analytical queries but adds overhead on short
  transactional ones. Disabled for Mascope's mixed workload - re-evaluate with profiling data
  if specific long queries would benefit.

### Autovacuum

- **`autovacuum_max_workers`** — Parallel autovacuum workers. Each consumes `autovacuum_work_mem`.
  Default 3 is fine for dev; 4 in prod with many large tables prevents dead tuple accumulation.

  The global `autovacuum_vacuum_scale_factor` default of 0.2 means vacuum triggers after 20% of
  the table is modified - on a 450M-row table that's 90M dead tuples before cleanup. For large
  tables, override per-table:

  ```sql
  ALTER TABLE match_isotopes SET (
      autovacuum_vacuum_scale_factor = 0.01,
      autovacuum_analyze_scale_factor = 0.005
  );
  ```

### Docker: shm_size

`/dev/shm` tmpfs inside the postgres container. PostgreSQL uses it for shared_buffers, WAL
buffers, lock tables, and IPC. Docker default 64MB causes crashes with any real shared_buffers:

```
ERROR: could not resize shared memory segment: No space left on device
```

Rule: `shm_size >= shared_buffers + 2GB overhead (WAL buffers, lock tables, IPC)`

> **Uses Docker byte format: `m` / `g` - not PostgreSQL format `MB` / `GB`.**
> The Pydantic validator rejects PostgreSQL format values for this field.

### Verifying Settings

```bash
# Confirm settings applied
mascope prod db cli -p
postgres=# SHOW shared_buffers;
postgres=# SHOW work_mem;
postgres=# SHOW random_page_cost;

# Check /dev/shm allocation and usage
docker exec mascope_prod_postgres df -h /dev/shm

# Check postgres shared memory segments
docker exec mascope_prod_postgres psql -U mascope_user \
  -c "SELECT name, size FROM pg_shmem_allocations ORDER BY size DESC LIMIT 10;"
```

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

An owner can do it from **Manage users** in the web interface or with a script on
the server. Either way, anyone with the app open is moved to the password screen at
once. On the server:

```sh
# report what would change, without changing anything
MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN=1 mascope prod db script run require_password_change

# require the change
mascope prod db script run require_password_change
```

The script reaches open tabs through Redis, as the app's own workers do. If it
cannot reach Redis it says so, and open sessions move to the password screen when
their next request is refused.

Maintenance scripts like this one ship in the backend image, and
`mascope prod db script` lists and runs them inside the backend container -
`mascope prod db script list` shows what the deployed release offers, and both
`list` and `run` need the stack up. The CLI itself needs no source checkout.

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

When a user changes their password, that user's access tokens are revoked.
New uploads are unaffected, since each upload is given a file-converter token of
its own, but **SDK and notebook tokens are not reissued** - their holders must
regenerate them.

A **paired instrument agent is not affected**: its credential belongs to the
machine account the pairing created, not to the person who approved it, so no
password change touches it. The exception is an agent still running on a
pre-registry token issued to a human before it was re-paired - that token sits on
the person's row and is revoked with the rest. Re-pair those machines before a
deployment-wide password requirement, not after. A **TOF agent** is always such
an exception: it has no pairing support, so its token stays a person's for as
long as it runs. Replace it with the File Agent before the requirement, or regenerate its
token from that person's settings and put it in the agent's config once they
have changed their password.

Requiring the change does not revoke anything by itself; tokens are revoked per
user, as each one complies.

### Paired instrument machines

An instrument agent authenticates as a **device**: approving a pairing request
creates a registered device named after the machine, a dedicated machine account
the agent signs in as, and a device-bound token. **Paired machines** under
Settings lists them with their service, the instrument each agent reports
watching, the agent release last seen and when each was last seen, and lets you
rename or revoke one machine on its own. The instrument and the release are
what the agent reported, and both follow what it reports now: a machine
repointed at another instrument, or rolled back to an older agent, says so on
its next upload rather than keeping what it first reported. A machine running
an agent that predates the fields shows neither, which is how far an agent
upgrade has got. Correct a wrong instrument in that machine's `config.toml`
and restart its agent - the server takes the agent's word for what it
watches, so correcting it anywhere else would not stick.

Two consequences worth knowing before an upgrade:

- **Revoking is per machine.** Revoking a device deactivates its machine account
  and clears its tokens, stopping that machine and nothing else. *Regenerate* -
  which replaces the tokens issued to **you** - no longer affects a paired agent,
  because the credential is not yours.
- **The Settings list is scoped to the devices you sponsor.** Reviewing or
  revoking someone else's device is an API call, not a click:
  `GET /api/auth/devices/all` and `DELETE /api/auth/devices/{id}`, within the
  usual user-management role ceiling.

#### Requiring paired credentials

Agent tokens issued before the device registry existed keep working, so a
deployment upgrading into this does not have to re-pair every machine the same
day. Once every agent machine **has** been re-paired, close that door:

```toml
[backend]
require_device_tokens = true
```

then `mascope prod up`. Any `file-agent`, `instrument-agent`, `tof-agent` or
`export-agent` bearer token with no device behind it is then refused with a
message telling the operator to re-pair, instead of being accepted as a
person's token. It ships
**off**, and turning it on before a machine is re-paired stops that machine's
uploads - so treat it as the last step of the rollout, not the first. The TOF
agent and the export agent have no pairing support on the client side, so it
stops those outright: replace every TOF agent with the File Agent before turning
it on. Personal and
SDK tokens are unaffected either way: the rule applies only to agent services.

### Two-factor authentication

Accounts can protect sign-in with a second factor (TOTP), and a deployment can
require one by role - [authorization.md](authorization.md#two-factor-authentication)
describes the feature as users and admins see it, and the
[user guide](user/guides/two-factor.md) walks through enrolment. Three things
concern the operator: one secret, one policy setting, and the last-resort
reset.

**The encryption key.** `.runtime/secrets/mfa_encryption_key.txt` encrypts the
stored TOTP seeds. `mascope prod up` generates it when missing, so it appears
on a deployment's first start once that release's CLI is installed. Two properties
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
mascope prod mfa status          # who holds a second factor (machine accounts: exempt) + unused code counts
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

Code fed to Python on stdin or typed at its prompt never reports, though it
inherits the DSN in the container: a prod job or an operator's
`docker exec -i <container> python - < script.py` keeps its errors on the
terminal instead of filing them as the server's. Run a script by its path when
its errors should reach GlitchTip.

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

## Feature settings

### Peak assignment

**Peak assignment** (assign a chemical composition to every peak - see
[the user docs](user/how-it-works/peak-assignment.md)) ships **off**; a
deployment switches it on as described under [Turning peak assignment on or
off](#turning-peak-assignment-on-or-off). What it costs a server once on is a
database-stage assignment of every newly processed sample, folded into its
batch's ledger: some extra processing time, and **about 200 bytes of database
per detected peak** - one batch-ledger member row - so roughly 0.4-1.6 MB for a
typical sample of 2,000-8,000 peaks. That is permanent, in the database's
volume, and grows with everything you acquire; on most instruments it is a
fraction of the raw data the sample itself brings. Targeted matching is
unaffected - assignment is an addition, not a replacement - and samples
processed before it was switched on are not assigned retroactively; use
*Rebuild batch ledger* on their batch for those.

An **explicit** assignment run - *Assign peaks* on a sample, or an import from
an external engine - writes a per-sample ledger as well: one row per detected
peak, about 0.8 KB each, with the per-peak alternatives, error figures and
provenance the batch ledger does not keep. Those runs are what the retention
pass described under [Reclaiming assignment runs](#reclaiming-assignment-runs)
bounds: it keeps the newest few per sample and engine and drops the rest. The
batch ledger's member rows are replaced when a sample is re-folded, never
accumulated, and the pass does not touch them. Size the database for the
members you keep plus the runs people ask for - see [Disk space](#disk-space).

Three `[meta]` settings shape the ingest-time cost without switching the
feature off - the views, on-demand runs and imports keep working either way:

```toml
[meta]
peak_assignment_on_ingest = false          # assign on demand only (default true)
peak_assignment_ingest_max_peaks = 20000   # skip denser samples at ingest (default 100000; 0 = no ceiling)
peak_assignment_ingest_ledger = "sample"   # write a per-sample run at ingest as well (default "batch")
```

`peak_assignment_on_ingest = false` is for a deployment that wants nothing
assigned unasked: samples are assigned when someone launches a run on them or
computes their batch's peaks. The ceiling guards against a pathological
acquisition: a sample with hundreds of thousands of peaks is skipped at
ingest, logged, and left for an explicit run.

`peak_assignment_ingest_ledger = "sample"` restores the behaviour releases
before the batch-primary ledger had: every ingested sample also gets a
per-sample run, about a kilobyte per detected peak, most of it placeholder
rows for peaks nothing assigned - on a high-throughput instrument tens of
gigabytes a month. It buys the inspector's per-peak alternatives and error
figures and hand curation on every sample without an explicit run. All three
take effect on the next stack restart.

#### Turning peak assignment on or off

Set it in the env's config toml (see [Where a deployment's settings
live](#where-a-deployments-settings-live)):

```toml
[meta]
peak_assignment = true
```

and restart the stack (`mascope prod up`). Samples are assigned at ingest from
then on, the write routes accept work, and the assignment views appear in the
UI. Setting it back to `false` and restarting reverses all three.

There is also a `MASCOPE_PEAK_ASSIGNMENT` environment variable, but it is a
**development knob, not an operator switch**: it is read by the backend only,
and it is not forwarded into the backend container by the production compose
file. Setting it does not move the web app, which reads the `[meta]` value - so
on a stack it would at best leave the assignment views on screen over write
routes answering 403. Use the toml.

!!! note "One switch, both halves - no image rebuild"

    Both the backend and the web app read this value at **start**, from the same
    place: `mascope prod up` hands the frontend container the runtime config it
    resolved, and the container publishes it to the browser
    (`/runtime-config.js`). A restart is therefore the whole procedure, on
    published images as much as on a source build.

    Older releases baked the flag into the frontend bundle at image build time.
    That made switching it off a two-step affair - and worse, it meant a
    deployment whose config layers are its own (`mascope init`, no source
    checkout) could run an image built against *different* config than its
    backend, showing assignment views whose *Assign peaks* button answered 403.
    If you are on such a release, upgrading is what fixes it; there is nothing
    to reconcile by hand afterwards.

A server with it off is unaffected by the feature: samples process as they did
before it landed, the UI shows no assignment views, and the
`/api/peak-assignments` write routes refuse to launch runs (403; the read
routes stay open, so ledgers written while it was on remain visible).

#### What a start seeds

Every backend start creates, before the server listens, the ionization
mechanisms the engine's chemistry needs - the mechanisms the shipped ionization
modes declare and every secondary channel an assignment profile can open,
nineteen in all - and then the eleven shipped modes. Each mechanism is built
with the target ions of every compound already in the library, so a fresh
server searches under the same chemistry as any other from its first run. A
mechanism the deployment already holds under that notation, in either
spelling, is adopted as it is; the shipped modes arrive inert (no token, no
collections, left out of the ionization listing until a deployment adopts one);
a second start changes nothing.

The first start on a large library is the slow one: about 6.5 s per missing
mechanism against a 1,466-compound library, 85 s for thirteen. The backend's
compose healthcheck allows a start 300 s for this (`start_period`); a
healthcheck of your own in front of the backend has to allow the same, or it
marks a slow first start unhealthy. A shipped mechanism cannot be deleted: the
settings show a lock, and the delete route refuses it as it refuses a shipped
mode.

#### Mechanism notation

An ionization mechanism is written in the standard adduct form, the ion's
charge at the end: `[M-H]-`, `[M+Br]-`, `[M+NH4]+`, `[M]+.`, `[M-H]+`, a
labelled moiety kept as it is (`[M+^NO3]-`). Rows stored in the older
`<operation><moiety><charge>` form (`-H+` for deprotonation, `+Br-`) were
rewritten in the standard form by a data migration (alembic `5193d1e942e0`,
whose downgrade is the inverse) when the release that shipped the notation was
applied, so there is no operator step; the mode editor, the exports and the SDK
show the standard form. The old form is still accepted on input and converted.
**The 2.0 release refuses it on input**: a client, a script or a list of your
own that still writes `-H+` has to write `[M-H]-` before the update to 2.0.

### Reclaiming assignment runs

Each assignment run writes **one row per observed peak** of its sample -
including peaks it could not assign, because the ledger is deliberately
complete - and re-assigning a sample adds a whole new run beside the old one,
so on a server where assignment is re-run routinely `peak_assignment` grows
without bound.

That re-run growth is all the pass below bounds. A sample's ingest run is its
only run until someone re-assigns it, and one run per sample is never
superseded, so the baseline ledger described under
[Peak assignment](#peak-assignment) stays whatever was acquired. Batch-peak
occurrences (`batch_peak_occurrence`, one per peak per sample for the latest
run) are bounded the same way - replaced when a sample is re-folded, never
accumulated - and the prune does not touch them either: a pruned run leaves
its occurrences in place with their assignment link set to NULL.

A deployment provisioned by `tooling/ubuntu.sh` handles this automatically:
`mascope-assignment-prune.timer` runs a retention pass nightly at 03:30 and is
**enabled by default**. Each pass keeps the newest few completed runs per
sample *and engine* (so a result can still be compared against the one it
replaced) and drops the rest, plus failed runs past a short grace period;
deleting a run cascades to its rows. It deletes only superseded derived data -
assignments are recomputable by re-running assignment - and it runs whether or
not the `peak_assignment` flag is enabled, since ledgers written before opting
out still age out and an empty table costs one cheap query. Tune the policy in
`/etc/mascope/prune.env` with `MASCOPE_PRUNE_KEEP_PER_SAMPLE` (default 2),
`MASCOPE_PRUNE_KEEP_PER_SAMPLE_TOTAL` (default 12),
`MASCOPE_PRUNE_KEEP_FAILED_HOURS` (default 24),
`MASCOPE_PRUNE_KEEP_RUNNING_HOURS` (default 72, minimum 12 so runs that may
still be executing cannot be pruned out from under a worker) and
`MASCOPE_PRUNE_KEEP_IMPORTING_HOURS` (default 24, minimum 1); disable it
entirely with `sudo systemctl disable --now mascope-assignment-prune.timer`.
A value **below** one of those minimums is rejected rather than raised to it:
the pass exits non-zero and prunes nothing that night, so check
`journalctl -u mascope-assignment-prune.service` after changing one. Leaving a
variable unset, or setting it to something that is not an integer, uses the
default.

The keep-newest budget counts **per sample and engine**, which matters on a
server where assignment runs are also published from an external engine rather
than only computed in the app: on a shared budget a few republished imports
would evict every in-app run for that sample, ledger rows cascading with them.
Each engine ages out of its own quota instead.

That split needs an outer bound, because the engine name comes from the
importing client: one that names itself per build or per release would mint a
fresh quota on every import and grow the table without limit, defeating the
pass. `MASCOPE_PRUNE_KEEP_PER_SAMPLE_TOTAL` caps how many completed runs a
sample keeps across all engines. The in-app engine is exempt from that cap -
otherwise a burst of imports would fill it and start evicting exactly the
history the per-engine quota exists to protect - so a sample keeps at most the
total plus the in-app engine's own quota.
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

**Interrupted runs.** An assignment run that a backend stop interrupts - a
restart, an update, a crash - cannot finish or fail itself. At its next start
the backend marks every run still in a state a server task owns as `failed`,
with the error "Interrupted: the server restarted while this run was in
progress.", so the run no longer holds its sample against new work; re-run the
assignment. An import a client started and did not finish is
not a server task and is left to `MASCOPE_PRUNE_KEEP_IMPORTING_HOURS` above.
On a database not yet migrated to the release's head the step warns and the
server starts anyway.

### The composite cut

**The composite cut** (`composite_scan_streams` in the server's `[backend]`
config) detects the peaks of a file whose acquisition method measures more
than one m/z range in a polarity per range, and stitches the ranges into one
spectrum per polarity: each m/z is read from the range that owns it, averaged
over that range's own scans, so an ion a short range measures no longer reads
low for being divided by every range's scans. The 1.11.0 and 1.12.0 release
notes describe it, and [the design note](dev/ingest_routing_and_splitting.md)
(section 4.5) the design. It ships **off**; two sites run it as canaries.
The backend and the file converter read the setting when they start, so after
changing it restart both:

```sh
mascope prod restart backend
mascope prod restart file_converter
```

Switching it on changes only files converted from then on. Whether a file's
peaks are detected per range is decided at its first conversion and recorded
with the file, and every later rebuild of its peak store goes by that record;
a file converted pooled stays pooled. The one run that decides again is a
**re-process** - *Re-process* in Raw files, or `POST /api/sample/files/reprocess`
from a signed-in web session (the route does not accept API tokens yet) - which
detects the file's peaks under the setting as it is at that moment, then
calibrates and matches as a first processing does. With the setting off, a
re-process leaves a pooled file alone.

#### Re-processing a site's older files under the cut

Done on both canary sites in October 2026, with the site acquiring throughout.
What to expect and how to pace it:

- **Which files.** The files acquired before the switch whose method measures
  several ranges in a polarity. The scan-stream census in each file's `.props`
  says which; by the database, they are the files of the methods whose files
  since the switch carry a composite stream row (`acquisition_stream` with no
  `signature_key`) that have no such row themselves. Files parked without a
  chemistry have no samples to re-process and are left as they are until a
  chemistry binds them; a re-process then stitches them too.
- **Batch size and pace.** 25 files per re-process, one batch at a time, the
  next only once every file of the last has reached a terminal status (`done`,
  `calibration_failed`, `failed` or `needs_chemistry`) with a processing time
  after the submission. Inside a batch each file took 2.5 to 3 s on a 32-core
  server - the detection per range, the calibration and the matching - so a
  batch is about a minute; with a minute's pause between batches, 7,300 files
  took ten hours. Hold while the site's own ingest has more than about twenty
  fresh files still processing; at 80 to 110 files an hour the live ingest
  never queued behind the re-processing, and the load stayed under 1.
- **What to watch.** Every file of a batch ends `done` and carries a composite
  stream row; the fresh files keep ending `done`; error tracking shows only
  the drift warning a drifting instrument raises anyway, at most once a day
  per instrument and threshold, so that count stays flat however many files
  are re-processed (each file's drift is in the logs at INFO). A re-process
  takes no database dump, so the backups directory is untouched. Keep a list of what
  was submitted, so an interrupted run resumes where it stopped rather than
  re-submitting.
- **What it changes.** A pooled file becomes stitched: one reading of each
  m/z from the range that owns it, averaged over that range's scans, so ions a
  short range measures read higher than they did, and a batch mixing
  re-processed and untouched files shows a step at the boundary until every
  file is done. A file already stitched by 1.11.0 gains, when re-processed
  under 1.12.0, a calibration factor per range and the overlap readings in its
  processing detail.

### Method bindings

**A method binding** records which chemistry an acquisition method has been
seen running - keyed on the instrument, the method file's name and the scan
signature of the method's MS1 streams - so that a later file of the same method
can be bound to its ionization mode without a token in its name. Every file
bound by its acquisition record, a person's choice or a filename token teaches
its method's binding; [the design note](dev/ingest_routing_and_splitting.md)
(section 5.3) has the rules. What a deployment does with the bindings is
`method_binding` in the `[backend]` section of the env's config toml:

```toml
[backend]
method_binding = "shadow"   # "shadow" (the default), "route" or "off"
```

- `"shadow"` learns a binding from every file that routes on a stronger rung
  and routes nothing on what it learned.
- `"route"` also binds a file **no token names** to the chemistry its method
  has been seen running, instead of parking it as *Needs a chemistry*. It sits
  below the filename token, so a file a token names is bound exactly as before.
  A method seen running two chemistries is skipped rather than guessed at, and
  so is a method whose name is a constant the instrument reports for every
  acquisition (some TOF configurations), which is keyed on the signature class
  alone and yields to a token. What it gains is concentrated at the sites whose
  file names carry no token: switch it on there first and watch the parked
  count.
- `"off"` records nothing.

It takes effect on restart (`mascope prod up`) and applies to files converted
from then on. With the rung on, re-processing a parked file from Raw files asks
its method too, so the files that waited for a chemistry are picked up once one
file of their method has been given one.

**Prime the table from history.** Bindings are learned as files arrive, so a
server that installs the feature knows nothing of the methods it has routed
for years until the backfill reads them. Two scripts, in this order:

```sh
DRY_RUN=1 mascope prod db script run backfill_scan_stream_census   # what it would do
mascope prod db script run backfill_scan_stream_census             # again, until nothing is left
DRY_RUN=1 mascope prod db script run backfill_method_bindings
mascope prod db script run backfill_method_bindings
```

- `backfill_scan_stream_census` records, in the files converted before the
  census existed, the scan-stream census the signature class comes from,
  reading it off the raw data kept beside each converted sample. Orbitrap files
  only: a TOF acquisition is one stream and has nothing to fill in. Reading a
  raw file per census is slow, so a run is bounded and resumable:
  `CENSUS_LIMIT` caps how many files one run reads (run it again until it
  reports nothing left to do), a file whose census could not be read is
  remembered and passed over on the next run, and `CENSUS_RETRY=1` tries those
  again once the cause is dealt with. A file without a census is skipped by
  the binding backfill, not guessed at, so on a server with Orbitrap history
  this one runs first or the next learns nothing from that history.
- `backfill_method_bindings` folds every acquisition sample auto-processing
  made into the table. It merges with what live learning has written, so
  running it twice changes nothing, and the mode a binding points at is the
  newest one its method's files bound to.

Both take the pre-script database dump the runner makes for any script that
writes.

**Read what the bindings would do** before and after a change to the switch:

```sh
mascope prod db script run report_method_binding_disagreements
BINDING_REPORT_FILES=100000 mascope prod db script run report_method_binding_disagreements
```

For every binding it says what the newest files of that method bind to by
their token today and what they would bind to by their method - asked of every
file read, including the ones a token names, which production never asks -
plus the files `route` would bind and `shadow` parks, the keys a guard holds
back and why, the keys following a change of mode that has not landed, and the
files whose name matches two chemistries, which park whatever the bindings say.
Both answers come from the code the pipeline runs. It writes nothing, so no
dump is taken before it. `BINDING_REPORT_FILES` caps the files one run walks
(20,000 by default, newest acquisition first) and `BINDING_REPORT_PER_KEY` how
many of them each binding is compared on (200).

**Where the composite cut is on**, `report_stitch_overlaps` reads what the
scan ranges of stitched files record where two of them overlap: per layout and
per overlap, which range owns it today, how many ions the two share and how the
second reads them against the first (the ratio and the m/z offset), how many
peaks each range holds in the overlap that the other does not, and the ions the
two ranges do not measure alike. It decides nothing - a ratio far from one can
be the layout's window factor or chemistry inside the instrument - and writes
nothing. `STITCH_REPORT_FILES` caps the raw Orbitrap files one run walks
(5,000, newest first).

```sh
mascope prod db script run report_stitch_overlaps
```

### Auditing a TOF instrument's mass calibration

`audit_tof_calibration` refits the stored peaks of one TOF instrument's files
exactly as the calibration node does - the same matching, the same 15 ppm
residual rule, the same subset search for five matches or fewer, the same fit -
under the mode's calibration collection and any calibrant sets you name, and
reports how good each fit is per ion and per mass band. It is how a change to a
calibrant collection is measured before it is made and after, in ppm per mass
band rather than one RMS.

```sh
TOF_AUDIT_INSTRUMENT=<name> mascope prod db script run audit_tof_calibration
TOF_AUDIT_INSTRUMENT=<name> TOF_AUDIT_FROM=2026-10-12 TOF_AUDIT_TO=2026-10-18 \
    TOF_AUDIT_SETS='{"proposed": ["C3H9N", "C6H18N2", "C13H25NO2"]}' \
    mascope prod db script run audit_tof_calibration
```

It writes nothing, so no dump is taken before it, and it never computes a peak
timeseries the store lacks, so it can run on a site inside the backend
container or on a copy of a site's database and filestore. For each file in the
range, and each ionization mode the files were acquired in, it reports: each
ion line's median and IQR error where the fit used it and where it did not
(held out); each calibrant's presence rate and how often the fit kept it; per
mass band, the fitted and the held-out error, and how far strong peaks sit from
the nearest CHO formula under each of the mode's mechanisms, which needs no
identity to be right; and the nitrate, sulfate and bromide cluster series
member by member. It checks that its refit under the mode's own collection
lands on the stored fit to 0.01 ppm on every file the node fitted, and says why
where it does not.
A file whose record is the converter's own calibration, as on a mode with no
calibrants or after a failed fit, has no `stored` fit: those coefficients count
samples from the start of the raw file's axis, not the stored TOF index the
audit reads at. The report counts such files as `unfitted` instead. The audit
tells them from an applied fit by the file's database record, since `.props`
holds both bare.
A file it cannot audit is counted as unreadable by its cause, and listed with
it in the JSON. "Stored as a blank" is a file the converter took for a blank,
or found too weak to fit instrument functions on: it has an empty peak store
and no instrument functions, so there is nothing to refit. The other causes
are no peak store, no instrument functions on a file that has peaks, and
instrument functions that could not be read. Anything else is counted by the
error's type, and the debug log has its traceback.

Everything is set through the environment:

| Variable | Meaning |
|---|---|
| `TOF_AUDIT_INSTRUMENT` | The instrument, as files name it. Required |
| `TOF_AUDIT_FROM` / `TOF_AUDIT_TO` | Acquisition dates, inclusive, `YYYY-MM-DD` (UTC). Default: the seven days up to today |
| `TOF_AUDIT_PER_DAY` | Files per day, spread over the day; `0` (the default) for every file |
| `TOF_AUDIT_MODE` | Only the files of the ionization mode of this name |
| `TOF_AUDIT_SETS` | Named calibrant sets as JSON: a name to a list of compound formulas, which the mode's mechanisms make ions of as a collection would, or to `{"collection": "<id or name>"}`. The mode's own collection is always audited, as `collection` |
| `TOF_AUDIT_MODELS` | Tofwerk calibration modes to fit, comma-separated; default `0` |
| `TOF_AUDIT_BANDS` | Mass band edges in Th; default `40,100,150,200,300,500` |
| `TOF_AUDIT_FAMILIES` | Mechanisms whose CHO family is measured, comma-separated (`[M+NO3]-,[M-H]-`); default the mode's |
| `TOF_AUDIT_SERIES` | Cluster series as JSON, a name to `{"core": "HSO4", "unit": "H2SO4", "n": 4, "charge": "-"}`, or `none`; default the three negative-ion series |
| `TOF_AUDIT_FAMILY_SNR`, `TOF_AUDIT_FAMILY_WINDOW_PPM`, `TOF_AUDIT_FAMILY_DBE_MAX` | The family yardstick: strong peaks above this S/N (20), within this window (30 ppm), against formulas of up to this many double bonds and rings (3) |
| `TOF_AUDIT_FAMILY_WINDOW_SPACING` | The family window is at most this share of the distance from a formula to its nearest neighbour (0.5). Above about 600 Th, and under `[M+Br]-` from about 300 Th, where the two bromine lattices interleave 17.7 mDa apart, that is narrower than 30 ppm |
| `TOF_AUDIT_FAMILY_PEAKED_IQR` | A band's family offset is a reading only where its IQR is at most this share of the window (0.5). Where the offsets fill the window, the report says "no reading: flat" instead of a median, and the JSON has `"peaked": false` |
| `TOF_AUDIT_FAMILY_MIN_PEAKS` | ...and only where it rests on at least this many peaks (10). Two or three offsets have a small IQR by chance; below the minimum the report says "no reading: too few peaks" instead of a median, and the JSON has `"peaked": false` beside its `n` and `min_peaks` |
| `TOF_AUDIT_JSON=1` | Also print the whole result as one JSON document, per-file fits and each unreadable file's cause included, as the last line of the output |

The TOF mass calibration plan ([tof_calibration.md](dev/tof_calibration.md))
is where the audit's readings go and what they decide.

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

Each load also records the window its formulas may be matched in, whether its
radicals may be, and its polarity. The flags that set them, the CSV schema a
hand-authored list follows, and staging, activating and pruning loads are in
the user guide [Reference compound lists](user/guides/reference-lists.md).

A load replaces the active version of that source only once it has successfully
read records, so a dump the adapter cannot parse leaves the existing mirror
serving rather than emptying it. Re-running the same source is how you update
it; prior versions stay on disk until pruned.

Mascope also ships a small curated seed of atmospheric lists: a monoterpene HOM
list, mass spectrometry background contaminants, and families of species no
formula grid reaches. Nothing loads them on its own. Every active reference
formula is one that Stage A of peak assignment matches peaks against, so loading
them is a choice. To load the lists that load by default:

```sh
docker compose exec backend python -m mascope_backend.db.scripts.reference_seed
```

- `--list` shows what ships and which lists are opt-in.
- Name a list to load it, e.g. `... reference_seed monoterpene-ro2-kang2021`.
- Each list becomes its own source.
- Running it again loads only the lists whose version changed. After an upgrade,
  that is how a revised list reaches the database.
- Running it again also brings the row of every list already loaded up to date
  with the window, radical allowance and polarity the list names. Run it once
  after upgrading to a release that added those fields.

To take a source out without deleting it:

```sh
docker compose exec backend python -m mascope_backend.db.scripts.reference_deactivate cyclic-siloxanes
```

Annotation and peak assignment stop reading it at once. `reference_seed` loads a
shipped list again, and a new `reference_sync` loads any other source.

### Reference licence gating

Every mirrored record carries a licence **tag** - a short exact string, not a
licence document. The nine registered adapters carry six distinct tags between
them, and a hand-authored list can also set the tag per row, in which case the
row's own tag wins over the adapter's:

| Licence tag | Default for | Note |
|---|---|---|
| `CC-BY-4.0` | `chebi`, `lipidmaps` | attribution required |
| `CC0` | `coconut` | |
| `custom` | `custom`, `peaklist` | every hand-authored row with no `license` column of its own, and every row of a schema 1 list file |
| `hmdb-attribution` | `hmdb` | free with attribution; verify commercial terms first |
| `open` | `norman` | |
| `public-domain` | `comptox`, `pubchem` | |

`mascope reference sources` names each source's tag and `mascope reference
status` prints this whole table with the gate's verdict on each row (monorepo
checkouts only, like the rest of `mascope reference`). Those two read the
adapter registry, so they cannot fall out of date when a source is added - the
table above is written by hand and can, which is why the allowlist is worth
re-checking after an upgrade that adds a source.

**By default peak assignment matches against every active source, whatever its
licence.** That is the behaviour every deployment has always had, and it is
unchanged.

A deployment that must not match against some of them restricts assignment to
an allowlist of tags in the env's config toml. The gate is an **exact string
match** on the tag, so the allowlist has to name every tag you want kept -
anything left out is dropped, and dropped silently:

```toml
[backend]
# Declines HMDB, keeps the other five tags. An example, not a default.
reference_licenses = ["public-domain", "CC-BY-4.0", "CC0", "open", "custom"]
```

Write it that way round - start from the full list and delete the tags you
decline - rather than allowlisting the two or three you had in mind. An
allowlist of `["public-domain", "CC0", "CC-BY-4.0"]` reads as harmless and
also drops every `norman` record, plus every hand-authored row that carries no
licence of its own - which on most deployments is the target list loaded with
`reference_sync custom` above.

Restart the backend to apply it (`mascope prod up`); no rebuild is needed -
this is backend-only and never reaches the browser. The gate gets applied at
the database stage of assignment: records whose licence is not listed are not
matched, so they never produce an identity, an alternative, or a score.
Annotation of a formula you look up by hand is *not* gated - that reads the
mirror directly - so removing a source from the gate is not the same as not
loading it. If you must not hold the data at all, do not ingest it.

Only add the line when you mean to restrict something. Narrowing the gate makes
assignment quietly find less, and nothing in the UI says why a peak went
unidentified. Two places do say so:

- `mascope reference status` (monorepo checkouts only, like the rest of
  `mascope reference`) prints the effective set and, for each active source,
  whether Stage A matches all, some, or none of its records. It reports the
  **per-record** licences, which the `custom` adapter lets a hand-authored list
  set per row, so a list can be partly matched. It then repeats the tag table
  above with each tag marked matched or not, so the tags an allowlist leaves
  out are named on screen - including tags belonging to a source this
  deployment has not loaded yet.
- Every assignment run records the set in force when it ran, as
  `reference_licenses` in the run's `config` - served by
  `GET /api/peak-assignments/sample/{id}/runs`, and by the SDK's
  `mascope.peak_assignments.list_runs(sample_id)`. `null` means the run was
  ungated. This is how you tell, months later, what a result was allowed to
  match - and it is the way to check the gate on a server, where
  `mascope reference` is not installed. The backend log names the set on each
  expansion too.

An empty list is refused at startup rather than honoured: `reference_licenses =
[]` reads like "no restriction" but would block every record. Delete the line
to allow every licence. The list is also not something an API client can set -
it is deployment configuration, so a caller cannot widen the gate its own run
is matched under.

### Upload size cap

A single resumable (tus) upload is capped at 5 GB by default, so one runaway
transfer cannot fill the disk. The cap applies **per upload** - it does not
limit how many files agents or users transfer in a day, only how large each
one may be. Instruments producing larger single files can raise it in the
env's config toml:

```toml
[meta]
tus_max_upload_gb = 20
```

Clients see the cap as the standard `Tus-Max-Size` header; an upload declared
larger than the cap is refused up front with HTTP 413.

The web uploader sizes its own client-side limit from the same setting, so
raising the cap lifts it for browser uploads too, on a stack restart and
nothing more - the frontend container publishes the runtime config it was
started with, on a pulled release image as much as on a local build (like
`peak_assignment`). The SDK and instrument-agent paths pick the new cap up on
the same restart.

The setting used to live under `[backend]`. A config toml that still has it
there keeps working - it is promoted to `[meta]` at startup with a warning -
but move it, because only the `[meta]` copy reaches the web app.

### Upload disk floor

The per-upload cap bounds one transfer; it does not bound how much disk several
concurrent uploads take together. A resumable upload is therefore also refused
at creation, with HTTP 507, when admitting it would leave less than
`tus_min_free_disk_gb` (10 GB by default, matching the disk monitor's own
threshold) free on the filesystem holding the upload spool:

```toml
[backend]
tus_min_free_disk_gb = 20
```

Set it to `0` to disable the check. The refusal happens before any bytes move,
and clients retry it, so a squeeze that clears on its own costs nothing but a
delay. If uploads are being refused, free space on `$MASCOPE_PATH` - see
[Disk space](#disk-space) for what grows there.

Upload spool entries left behind by clients that started an upload and never
finished it are deleted once they have gone 24 hours without progress, checked
whenever a new upload is admitted. Only untouched entries are swept, so a slow
multi-hour transfer is never reaped.

### Legal and support links

The sign-in screen and the About tab of the home menu link a privacy
notice, terms of service and a support contact. The documents are published
outside the product, so the links are settings in the env's config toml. The
defaults are:

```toml
[meta]
privacy_notice_url = "https://ultratrace.eu/mascope/privacy"
terms_url = ""                                 # empty: no link is shown
support_url = "mailto:support@ultratrace.eu"
```

A deployment operated by someone other than Ultra Trace should point these at
its own privacy notice and support desk. An empty string hides a link. The
privacy notice and terms take an `http(s)://` URL and the support contact an
`http(s)://` or `mailto:` one; anything else is refused when the config loads,
naming the setting, rather than ending up in a link. Like the other `[meta]`
settings, a change takes a stack restart - the frontend container publishes the
runtime config it was started with.

The About tab also shows the licence and the notices - NOTICE together with
the third-party attributions. Those need no setting: each image generates its
own at build time (the npm packages in the web app's bundle; for the bundled
user documentation, the packages that build it and the assets vendored under
`docs/user/assets`, by the licence files kept beside them; the Python
distributions installed in the server image). A new vendored asset needs its
licence file next to it, or the docs build has nothing to carry for it.

### Deployment identity

Each deployment has an id that names it in the provenance of everything it
exports: the *Provenance* sheet of a batch's *Batch data* spreadsheet, and the
`df.attrs["provenance"]` of the frames the SDK's loaders return
(`GET /api/provenance`). Records are keyed by ids that are unique within one
deployment only, so the deployment id is what tells two deployments' exports
apart.

Nothing needs setting. On its first start the backend generates an id and
keeps it in `deployment.json` at the root of the env's filestore, and every
later start reads it back. The filestore is where the id belongs: it is
persistent in every deployment, and the [off-site backup](#backups) copies it
with the database dumps, so a restored deployment keeps its name. To choose
the name yourself, set it in the env's config toml:

```toml
[backend]
deployment_id = "example-lab"   # 1-64 letters, digits, ".", "_" or "-"
```

A configured id wins over the generated one and is read at start. It is part
of the env's config, which `mascope env sync` copies with the rest of the env,
so an env synced from one with a configured id reports that same id: leave
`deployment_id` unset on envs that are synced from one another.

Copying an env's data does not copy its generated id. `mascope env sync` and
the demo bundle leave `deployment.json` behind, so a synced env or a demo stack
keeps the id it has, or generates its own on its next start. A restore from
backup does bring it back - the point when the original is gone. A backup
restored beside an original that keeps running carries the original's id as
well: move the restored `deployment.json` aside before the copy's first start,
and it generates its own.

The file is never written over. If the backend finds it but cannot read an id
from it, it logs a warning at start and exports name no deployment until the
file is repaired, moved aside (the next start generates a new id), or
`deployment_id` is set.

The deployment id is also the namespace of the global ids an export gives its
records. Beside the dataset and batch ids it names - `inputs.dataset_id`,
`inputs.sample_batch_id` - the provenance carries `inputs.dataset_global_id`,
`inputs.sample_batch_global_id`, in the form

```text
<deployment_id>:<entity>:<local_id>     e.g. example-lab:sample_batch:V1StGXR8_Z5jdHi6
```

where `<entity>` is the record's table (`dataset`, `sample_batch`,
`sample_item`, `sample_file`, `target_collection`, `target_compound`,
`target_ion`) and `<local_id>` its id in this deployment. Neither a deployment
id nor an entity can hold a `:`, so a global id splits back into its three
parts. The format is stable: it is part of the provenance contract, and a
change to it would change the provenance version. A deployment with no id
gives no global ids rather than inventing a namespace. Only a key that holds
one id gains a global id: a list of ids, such as
`inputs.target_collection_ids`, has none.

A global id is only as unique as the deployment id in it. Two deployments
configured with the same `deployment_id` mint global ids that can name
different records with the same string: give every deployment whose exports
may meet a name of its own, and leave it unset where you have no reason to
choose one.

### Where a deployment's settings live

| Path | What |
|---|---|
| `/etc/environment` | `MASCOPE_PATH`, `LD_PRELOAD` (read by the systemd units) |
| `/etc/mascope/update.env` | update window / grace / repo, update disk floor (chmod 600) |
| `/etc/mascope/disk-check.env` | disk-monitor thresholds + alert URL (chmod 600) |
| `$MASCOPE_PATH/.runtime/secrets/` | `postgres_password.txt`, `jwt_secret_key.txt`, `server_owner_secret_key.txt`, `mfa_encryption_key.txt`, TLS cert/key, `backup.env` |
| `$MASCOPE_PATH/.runtime/database/backups/prod/` | database dumps (incl. pre-migration) |
| `$MASCOPE_PATH/.runtime/update/` | `state.json` (pending update), `status.log` |
| the env's filestore, `deployment.json` | the generated [deployment id](#deployment-identity), unless `[backend] deployment_id` is set |

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
the next window. To take it now instead, deploy it by hand (check out the tag,
reinstall the CLI, `mascope prod update`), which also clears the record.

**`mascope prod db script` cannot find a mascope Python / lists no scripts.**
The scripts are discovered and run inside the backend container, so the stack
must be up: `mascope prod ps`, then `mascope prod up --detach`. The nightly
`mascope-assignment-prune.service` is affected the same way - a firing while
the stack is down fails, and the next one runs the pass again.

**A file was processed in an unexpected way** - samples missing, or peaks that
look like two measurements mixed together. `mascope file scans <file.raw>` lists
the file's scan streams, one per experiment of its acquisition method: what
each measured (polarity, scan range, scan mode, resolution), when, under which
scan event, and its strongest peaks, which usually include the reagent ions of
its chemistry. Two experiments that measure the same thing on paper - the same
range at another microscan count, or one scan definition repeated later in the
method - are listed apart, with the scan event closing each one's name. A
polarity with more than one MS1 stream is pooled into one peak list by
processing, and the command says so. It
reads a copy of the file on this machine, inside the backend container, so the
stack must be up.

**Acquisition times are hours off for files that were uploaded without a
timezone**, and their ingest appears to precede their acquisition. A raw file
records its acquisition time as the instrument PC's wall clock, with no
offset, and the server can only place it in UTC if the upload says which
zone that clock was in. An upload that reports none - the web app before it
reported the browser's zone, an SDK script, a File Agent older than zone
reporting - has the wall clock read in the converter's own zone, which is UTC
in the backend image. Such a file is stored with `utc_offset_source =
'guess'`, no `acquisition_timezone`, and a `datetime_utc` that is the wall
clock labelled UTC - two hours late in central European summer time. Such
rows keep arriving until every client of the site reports a zone; the rows
already stored are repaired by reading each wall clock in the zone the
instrument PC's clock is *set to* (its Windows time zone), which is not
always the zone the site is in - a PC whose clock is set to UTC needs no
repair, and naming the site's zone for it would shift every one of its rows:

```sh
# report what would change, without changing anything
MASCOPE_FIX_GUESSED_UTC_ZONE=Europe/Vienna mascope prod db script run fix_guessed_datetime_utc --skip-backup

# repair, with the pre-script backup
MASCOPE_FIX_GUESSED_UTC_ZONE=Europe/Vienna MASCOPE_FIX_GUESSED_UTC_APPLY=1 mascope prod db script run fix_guessed_datetime_utc
```

Read the dry run's evidence line before applying: it counts the rows that end
after their own registration time, as stored and as they would be after the
repair. A wall clock stored as UTC east of UTC shows as rows that end after
they were registered, which the repair clears; rows that are already right
show none, and a repair that creates such rows is in the wrong zone.
`MASCOPE_FIX_GUESSED_UTC_INSTRUMENT` narrows the run to one instrument, for a
server whose instruments' clocks are set to different zones, and
`MASCOPE_FIX_GUESSED_UTC_CREATED_MIN` / `_CREATED_MAX` (ISO instants) to a
window of registration time - rows that have no registration time (registered
before the column existed) fall outside any window and are counted as such.
The script repairs Orbitrap rows only: a guessed TOF row's `datetime_utc` is
already right, since its local time is the acquisition's UTC instant plus the
guessed offset, and it is that local column that is off. It touches only rows
guessed with a zero offset; a guessed row stored with another offset was
guessed in a real zone, or repaired by an earlier run, and is counted and left
alone, as is a row that carries a reported zone. A row already right in the
named zone, as a British winter file is, is not written. The instrument
function fitted from a repaired file is moved with it. Every apply writes a
ledger of the rows it changed, old and new value, to
`.runtime/env/<env>/repairs/` under the Mascope home, so a repair made in the
wrong zone can be found and undone row by row without restoring a dump. The
repair follows the converter's rule for the hour the clocks repeat or skip,
and names the rows that fall in one.

**Backend unhealthy after an update.** The updater stops and leaves the stack in
place (no automatic rollback). That covers a failed migration too, which stops
`docker compose up` before the backend starts. Investigate with
`mascope prod logs backend` (and `mascope prod logs db_init` for a migration). To
roll back manually: if a migration ran, first restore the pre-migration dump
(`mascope prod db backup list`, then `mascope prod db restore <dump> --yes`),
then redeploy the previous release with
`mascope prod update --version v<previous>`. The timer will not apply the
failed release again: `--auto` records the failure and holds that release, and
`mascope prod doctor` reports it as failed. Each timer run exits 2 until you
act: `mascope prod update --snooze <days>` quiets it while you wait for a
fixed release, which the timer then applies as usual, and
`mascope prod update --confirm` retries the failed release at the next window.

**Two-factor codes stopped working for everyone** (typically after a rebuild or
a restore onto a fresh host). The seeds in the database no longer decrypt:
`mfa_encryption_key.txt` is missing, or is not the file the seeds were
encrypted under. Recovery codes still work. Put the original key file back and
restart the backend; if it is gone for good, each enrolled account signs in
with a recovery code (or is reset - see
[Two-factor authentication](#two-factor-authentication)) and enrols again.
