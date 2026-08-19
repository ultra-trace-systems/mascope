# Monitoring stack — GlitchTip + Uptime Kuma

Self-hosted monitoring for a Mascope fleet, meant to run on an internal
monitoring box (**the monitoring box** below), reachable from the LAN and the
tailnet only, plain HTTP. Concrete names and addresses are deliberately kept
out of this public repo — where you see `<monitoring-host>`,
`<monitoring-tailnet-ip>` or `<lan-subnet>`, substitute the real values (on
the box: `tailscale ip -4`; private fleet docs have the rest):

- **GlitchTip** — error tracking. Mascope's backend forwards `WARNING`/`ERROR`
  log records (with tracebacks and request context) so you stop grepping log
  files. Sentry-API compatible; Mascope uses the stock `sentry-sdk`.
- **Uptime Kuma** — external uptime + **TLS-certificate-expiry** monitoring, one
  monitor per Mascope server. Complements the healthchecks.io dead-man's-switch
  checks (backups, disk) with "is the site actually reachable / is the cert about
  to expire".

These files are a **template**: copy them to the box and run them there. The real
`glitchtip.env` and `data/` never live in git.

> **Why the tailnet matters:** the Mascope servers are cloud VMs — the
> box's LAN address does not route from them. Error reporting reaches GlitchTip
> over Tailscale, so the published ports are bound to `0.0.0.0` and access is
> restricted to LAN + tailnet in the `DOCKER-USER` chain (§2). The DSN and
> `GLITCHTIP_DOMAIN` use the box's tailnet IP.

## 1. Prerequisites — Docker

```sh
# Docker Engine + compose plugin (skip if already installed). Review before
# running on a shared box.
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"      # log out/in for the group to take effect
docker --version && docker compose version
```

**Storage, if this box also receives the fleet's backups (§11).** Everything
below assumes a redundant data volume mounted before any service starts —
GlitchTip's Postgres, Kuma's SQLite, the restic repos and the assessment
records all live on it. Use RAID or ZFS: a backup host whose own disk has no
redundancy fails at exactly the moment it is needed, and the failure is
discovered during a restore. Mount it via `/etc/fstab` (or a mount unit) so it
is present at boot, and confirm before continuing — a Docker volume created
while the mount is missing lands silently on the root disk and looks fine until
the disk fills.

## 2. Firewall (LAN + tailnet only)

`ufw` alone does **not** filter Docker-published ports (Docker's DNAT runs before
`ufw`'s INPUT chain), so filtering for the published services lives in the
`DOCKER-USER` chain via [ufw-docker](https://github.com/chaifeng/ufw-docker).
Host-level rules cover SSH.

> **Do not lock the tailnet out.** The box is administered over Tailscale — the
> `tailscale0` rules below are what keep that working (and what let the Mascope
> backends deliver events).

```sh
# host-level (INPUT chain): SSH from the LAN and the tailnet
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <lan-subnet> to any port 22 proto tcp       # SSH (LAN)
sudo ufw allow in on tailscale0 to any port 22 proto tcp        # SSH (tailnet)
sudo ufw enable

# DOCKER-USER chain: the published monitoring ports, LAN + tailnet only
sudo wget -O /usr/local/bin/ufw-docker \
  https://github.com/chaifeng/ufw-docker/raw/master/ufw-docker
sudo chmod +x /usr/local/bin/ufw-docker
sudo ufw-docker install
sudo systemctl restart ufw
sudo ufw route allow proto tcp from <lan-subnet>  to any port 8000   # GlitchTip (LAN)
sudo ufw route allow proto tcp from <lan-subnet>  to any port 3001   # Uptime Kuma (LAN)
sudo ufw route allow proto tcp from 100.64.0.0/10 to any port 8000   # GlitchTip (tailnet)
sudo ufw route allow proto tcp from 100.64.0.0/10 to any port 3001   # Uptime Kuma (tailnet)

# REQUIRED last step: restarting ufw flushed the FORWARD chain, wiping
# Docker's own forwarding rules - containers lose OUTBOUND internet (webhook
# notifications, update checks) while inbound to the published ports still
# works. Restarting Docker re-inserts its chains. Repeat this after ANY
# future `systemctl restart ufw` on this box.
sudo systemctl restart docker
```

(`100.64.0.0/10` is the Tailscale CGNAT range every tailnet node gets its
address from.)

## 3. GlitchTip

```sh
sudo mkdir -p /opt/glitchtip
sudo cp glitchtip/compose.yaml glitchtip/glitchtip.env.example /opt/glitchtip/
cd /opt/glitchtip
cp glitchtip.env.example glitchtip.env
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" glitchtip.env
docker compose up -d
docker compose logs -f web      # wait until it serves on :8000 (migrations run on boot); Ctrl-C when up
```

Then create the first account and a project (see [§6](#6-first-run-glitchtip)).

## 4. Uptime Kuma

```sh
sudo mkdir -p /opt/uptime-kuma/data
sudo cp uptime-kuma/compose.yaml /opt/uptime-kuma/
cd /opt/uptime-kuma
docker compose up -d
```

Open `http://<monitoring-host>:3001` (MagicDNS, from any tailnet machine; use the box's LAN
IP from a non-tailnet LAN machine) and create the admin account on first load
(see [§8](#8-uptime-kuma-monitors)).

## 5. Backups

Copy `backup-monitoring.sh` to the box (e.g. `/opt/monitoring/`), point it at your
restic repo, and schedule it nightly **in root's crontab** (`sudo crontab -e` —
the script needs docker, `/var/lib/docker/volumes`, and `/root/.restic-pass`).

> **restic >= 0.16 required** (`--stdin-from-command` for the Postgres dump).
> Ubuntu's apt ships an older restic — install the current binary from
> [restic releases](https://github.com/restic/restic/releases) into
> `/usr/local/bin`, and give root's crontab a `PATH=/usr/local/bin:/usr/bin:/bin`
> line so cron finds it (cron's default PATH does not include /usr/local/bin).
It logically dumps GlitchTip's Postgres, backs up GlitchTip uploads, and takes a
quiesced copy of Uptime Kuma's SQLite.

```sh
sudo mkdir -p /opt/monitoring && sudo cp backup-monitoring.sh /opt/monitoring/
export RESTIC_REPOSITORY=/srv/restic-repo          # or your existing repo
sudo install -m 600 /dev/stdin /root/.restic-pass <<<"$(openssl rand -hex 24)"
sudo RESTIC_PASSWORD_FILE=/root/.restic-pass restic init --repo "$RESTIC_REPOSITORY"
```

Cron (niced so it never starves the backup workload):

```cron
RESTIC_REPOSITORY=/srv/restic-repo
RESTIC_PASSWORD_FILE=/root/.restic-pass
30 4 * * * nice -n 19 ionice -c3 /opt/monitoring/backup-monitoring.sh 2>&1 | logger -t monitoring-backup
```

## 6. First-run: GlitchTip

1. Browse to `http://<monitoring-host>:8000` and **register the first account** at
   `/register` (allowed even with `ENABLE_USER_REGISTRATION=False`; there is no
   default admin). You are prompted to **create an organization**, then a
   **project** — pick platform **FastAPI**/**Python**.
2. **Copy the DSN.** Project → *Settings → Client Keys (DSN)*. It looks like
   `http://<public_key>@<monitoring-tailnet-ip>:8000/<project_id>` — it mirrors
   `GLITCHTIP_DOMAIN`, which deliberately uses the box's **tailnet IP**: the
   backend containers must reach it, and container DNS does not resolve
   MagicDNS names (Docker's embedded resolver bypasses the tailnet resolver).
   The MagicDNS name is for humans in browsers only.
3. **Notifications:** in the project/organization settings, add an alert (email
   via your SMTP relay, or a Slack/webhook integration) so new issues page you.

## 7. Turn on error reporting in Mascope

The backend has an **optional, off-by-default** GlitchTip sink (see
`docs/maintaining.md` → Monitoring). On each Mascope server:

1. **One-time network prerequisite** — the backend runs in Docker with
   `iptables: false`, and the standing `MASCOPE NAT` block in
   `/etc/ufw/before.rules` only masquerades container traffic leaving the WAN
   interface. Events travel over the **tailnet**, so add a `tailscale0`
   masquerade line next to the existing ones and reload:
   ```
   -A POSTROUTING -s 172.18.0.0/16 -o tailscale0 -j MASQUERADE
   ```
   ```sh
   sudo ufw reload
   # verify from inside the container before relying on it (expect HTTP 200):
   docker exec mascope_prod_backend python3 -c \
     "import urllib.request; print(urllib.request.urlopen('http://<monitoring-tailnet-ip>:8000/', timeout=5).status)"
   ```
2. Make sure the server runs a backend image that ships `sentry-sdk` (builds
   from v1.4.3 onward include the runtime's `[sentry]` extra). Check against
   the app's venv — the image's bare `python3` is a DIFFERENT interpreter and
   gives a false negative:
   ```sh
   docker exec mascope_prod_backend /opt/uv/tools/mascope/bin/python -c "import sentry_sdk"
   ```
   Update the stack if that fails.
3. Set the DSN on the **host** — compose passes it into the backend and
   file-converter containers:
   ```sh
   # append to /etc/environment, then restart the stack (mascope prod up)
   MASCOPE_SENTRY_DSN=http://<public_key>@<monitoring-tailnet-ip>:8000/<project_id>
   ```
   `/etc/environment` is applied at **login**: run `mascope prod up` from a
   fresh SSH session after editing it, or the DSN interpolates as empty and
   reporting silently stays off.
4. Smoke-test: `runtime.logger.error("glitchtip smoke test")` on the server (or
   trigger any backend warning) and confirm the event appears in GlitchTip.
   Unset the var and restart to turn reporting back off — it's a complete no-op
   when absent.
5. **Optional — performance tracing.** Next to the DSN, set
   `MASCOPE_SENTRY_TRACES_RATE=0.1` (fraction of requests to trace, `0`–`1`)
   and restart the stack; sampled requests show up under the GlitchTip
   project's **Performance** tab as per-endpoint latency. Start at `0.05`–`0.1`
   and watch GlitchTip's Postgres volume before raising it — transactions
   vastly outnumber errors. Unset/`0` (default) means errors only.

## 8. Uptime Kuma monitors

There is no supported config API, so add monitors in the UI. **For each Mascope
server:**

1. **Add New Monitor** → Type **HTTP(s)**.
2. **URL** = the server's public app URL, e.g. `https://example.mascope.app`
   (through the CDN/proxy in front, if any — exactly the path users take).
   TLS-expiry checks require an `https://` target and *"Ignore TLS/SSL error"*
   **off**.
3. Set a friendly name, heartbeat interval, retries.
4. Enable **Certificate Expiry Notification** (global thresholds default to
   **21/14/7 days** before expiry).
5. Tick the notification channel (Settings → Notifications: email/Slack/webhook),
   then **Save**.

**Security tripwires (inverted monitors).** If the fleet's origin servers are
not meant to answer strangers directly (e.g. SSH restricted to the tailnet and
443 restricted to the CDN's ranges — the posture `tooling/fleet/` codifies),
encode that as standing alarms — for each server add two **TCP Port** monitors
against its **public IP**, ports **22** and **443**, with **Upside Down Mode**
enabled (healthy = connection *fails*). If a firewall regresses or Docker
starts bypassing ufw, the "port reachable" alert fires within minutes instead
of being discovered months later.

## 9. Stack-health push monitors (`mascope prod doctor`)

The HTTP monitors above prove "users can load the app"; they cannot see a
filling disk, a stale backup, a pending migration, an unhealthy container
hiding behind a still-green frontend, or a stack running a different release
than its checkout deploys. `mascope prod doctor` sees all of that
(exit 0 healthy / 1 unhealthy), and [`doctor-push.sh`](doctor-push.sh) feeds it
into a Kuma **Push** monitor per server (dead-man's switch: a missed heartbeat
also alerts, catching servers too broken to even run cron).

Per Mascope server:

1. In Kuma: **Add New Monitor** → type **Push**, name `doctor-<server>`,
   heartbeat interval `3600`, retries `1`, notification ticked. Copy the token
   from the generated push URL.
2. Copy `doctor-push.sh` to the server (e.g. `~/doctor-push.sh`), replace
   `__KUMA_URL__` with the monitoring box's URL as reachable from that server
   (e.g. `http://<monitoring-tailnet-ip>:3001`) and `__TOKEN__` with that
   monitor's token, and `chmod 700` it (the token is a write credential to the
   monitor).
3. Install the deploy user's cron (no sudo needed — doctor only needs docker
   access): `*/30 * * * * /bin/bash $HOME/doctor-push.sh`

Pinging every 30 min against a 60-min window tolerates a single blip without a
false alarm. The script prefers `doctor`; on a release that predates it, it
falls back to a direct container-health check. Two extras ride along in each
heartbeat: the message carries current disk usage (visible in the heartbeat
tooltip), and the numeric `ping` field carries disk-used %, so the monitor's
"response time" chart doubles as a disk-usage trend graph — a deliberate,
lightweight stand-in for a real metrics stack until trend questions justify
one.

> The `doctor` command requires an up-to-date `mascope` CLI. `mascope prod
> update` refreshes images but **not** the CLI binary — if `doctor` is missing,
> rerun the `uv tool install` step from `tooling/ubuntu.sh` (or
> `ubuntu.sh reinstall`).

**Deliberate overlap with the disk-check timer.** `mascope-disk-check.timer`
(see `tooling/systemd/`) also watches the disk and pings healthchecks.io. That
is layering, not redundancy: doctor->Kuma is the rich aggregate but depends on
Python, Docker, the tailnet, and the monitoring box; the disk check is bash +
`df` + one HTTPS call to an external service — the dumbest reporter survives
the disk-full scenario that wedges everything else. Keep both, and stagger the
thresholds so the simple tier warns early and a doctor "down" means it is
serious.

## 10. Scheduled security assessment (`run-assessment.sh`)

[`run-assessment.sh`](run-assessment.sh) runs the pen-test suite
(`security/pentest/`) against a deployment on a schedule and keeps the reports
here. Uptime Kuma tells you the site answers; nothing else tells you TLS is
still enforced, the security headers are still set, or tenant isolation still
holds. Because deployed images are pinned, what a periodic run actually detects
between releases is **environment drift** — an edge rule, a firewall change, a
certificate approaching expiry.

**It runs from the monitoring box, driving the suite over SSH on the target.**
Two reasons, and neither is convenience: a report stored on the system under
test is a report a successful attacker can edit, and the target is rebuilt from
scratch as a matter of routine. The suite still has to *execute* on the target
when the origin is firewalled to the CDN's ranges — so execute there, keep the
record here.

Setup:

1. On the target, create an unprivileged account (`pentest` by default) that is
   **not** in `sudo`, and deploy `security/pentest/` plus its venv into
   `SUITE_DIR`. A release does not update this copy — it is a copy, not a
   checkout, so sync it and verify by hashing against the tag.
2. Give the monitoring box a dedicated key for that account. Use `restrict` in
   the target's `authorized_keys`, server-side, rather than relying on the
   client's `ForwardAgent no`.
3. Write the config file (`/opt/pentest-runner/config.env`, `chmod 600`) with
   `TARGET_HOST`, the `MASCOPE_PENTEST_*` credentials and an optional `HC_URL`.
   The script header lists every variable.
4. Cron it clear of the backup windows, e.g. `30 5 * * 0`.

**Exit codes.** Normally the suite's, so the heartbeat reflects the assessment
rather than the plumbing — with two overrides for cases where pytest succeeded
and the run still is not evidence:

| Exit | Meaning |
| --- | --- |
| `1` | the report was unusable (empty archive, unreadable JSON) |
| `90` | the target is **behind the current release** — honest, but about an artifact deployed nowhere |

The lag comparison happens **here, never on the target**: the suite executes on
the box under test, so a release lookup performed there would be the deployment
whose currency is in question answering the question about itself. A lookup that
cannot resolve the current release records `lag=unknown` and changes nothing —
an unreachable third party must never be able to redden an assessment.

Each run leaves three files in `RECORDS`: `<ts>-origin.log`, `<ts>-origin.tar.gz`
and `<ts>-origin.summary`, pruned at 400 days.

**Do not "simplify" the report retrieval.** It is written defensively against
the box it talks to: the report is pulled as one stream rather than a remote
glob (a hostile server chooses the filenames it sends, which would be a
file-write primitive on the host holding your backups), the stream is
size-bounded before it lands, the archive is never extracted, and only counts
are sent to the heartbeat service — finding detail describes weaknesses in your
own deployment and the heartbeat is a third party.

## 11. Receiving the fleet's backups

If this box is also the off-site restic target for your Mascope servers (the
arrangement `tooling/backup-cron.sh` on each server expects), that side needs
setting up **here** — the server-side docs only describe what gets pushed.

```sh
sudo adduser --disabled-password --gecos "" mascope-backup
sudo -u mascope-backup mkdir -p ~mascope-backup/.ssh
# one line per server: the public half of the key its backup cron uses
sudo -u mascope-backup tee -a ~mascope-backup/.ssh/authorized_keys < server-keys.pub
sudo install -d -o mascope-backup -g mascope-backup /mnt/<data-volume>/mascope-backups/<server>
```

Points that are easy to get wrong, each of which has bitten someone:

- **restic over `sftp:` is SSH**, so any network policy between the servers and
  this host must keep **port 22** open. A tailnet ACL draft that allowed only
  the monitoring ports would have silently stopped every off-site backup — the
  backups would have failed nightly and the failure is only visible if the
  dead-man's-switch pings (below) are actually wired up.
- **One repo per server, each with its own password.** A shared repo means one
  compromised server can read — or delete — every other customer's backups.
- **The repo password is the whole restore.** Repo plus password is sufficient;
  neither alone is worth anything. Keep the passwords in the password manager,
  never on this box.
- These repos are **not** included in this host's own backup (§5). They are
  large, and they are themselves backups — but it does mean they exist in
  exactly one place, so decide deliberately whether that is acceptable.

## 12. Restoring this host

Worth reading before you need it, and worth drilling once. The backup from §5
contains the monitoring data *and* the configuration that makes it meaningful —
the scripts and their env, both stacks' compose files, and any extra paths
named in `EXTRA_CONFIG_PATHS`.

1. Install the OS, Docker (§1) and restic; restore the firewall rules (§2).
2. **Mount the data volume first**, at the same path as before.
3. Restore from the repo — you need the repo and its password, nothing else:
   ```sh
   export RESTIC_REPOSITORY=... RESTIC_PASSWORD_FILE=...
   restic snapshots                       # pick the target snapshot
   sudo restic restore <id> --target /
   ```
4. Bring up the stacks with the restored compose files (§3, §4).
5. Restore GlitchTip's database from the `glitchtip-db.sql` snapshot:
   ```sh
   restic dump <id> glitchtip-db.sql \
     | docker compose -f /opt/glitchtip/compose.yaml exec -T postgres psql -U postgres
   ```
6. Re-add the scheduled jobs (§5, §9, §10) — cron entries are not part of the
   backup unless you put `/var/spool/cron` in `EXTRA_CONFIG_PATHS`.
7. Verify rather than assume: monitors listed and green, a test event reaching
   GlitchTip, and a `restic check` on the restored repo.

**The gap to be honest about:** if this host's own repo lives on the same
machine — even on a redundant volume — it survives a disk failure but not loss
of the machine. An off-site copy of at least this repo is cheap (it is small,
unlike the fleet repos) and is what makes this section usable in the scenario
that actually destroys a box.

## Notes & caveats

- **GlitchTip 6** runs one all-in-one `web` container (no separate worker/beat).
  The in-container port var is `GRANIAN_PORT`, not `PORT`; to move the port, remap
  the host side in `compose.yaml`.
- **Postgres 18** stores data at `/var/lib/postgresql` (not `.../data`) — the
  volume mount reflects that. `POSTGRES_HOST_AUTH_METHOD=trust` is acceptable only
  because Postgres has no published port; set a password to harden.
- **`DOCKER-USER` interface**: if you hand-write firewall rules instead of
  ufw-docker, confirm the real interface with `ip -br addr` (often not `eth0`).
- **Resource footprint** on the shared box: budget ~1 GB RAM for GlitchTip
  (web + Postgres + Valkey) and ~256 MB for Uptime Kuma. Trim GlitchTip's worker
  with `VTASKS_CONCURRENCY` if constrained.
- Pin concrete image tags (`glitchtip/glitchtip:6.x`, `louislam/uptime-kuma:2.x.y`,
  `postgres:18.x`) before you consider this production-frozen.
