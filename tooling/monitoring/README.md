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

**Storage, if this box also receives the fleet's backups (§12).** Everything
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
(exit 0 healthy / 1 unhealthy / 3 healthy but a disk is filling toward its
floor), and [`doctor-push.sh`](doctor-push.sh) feeds it into a Kuma **Push**
monitor per server, down on 1 (dead-man's switch: a missed heartbeat also
alerts, catching servers too broken to even run cron). The disk warning can
have a second Push monitor of its own, down while a filesystem is filling
toward the doctor's floor or below it. It is kept apart because Kuma notifies
on a change of state: on one shared monitor, a server already down for the
warning would report a later failure - a stopped container, version drift - to
no one.

Per Mascope server:

1. In Kuma: **Add New Monitor** → type **Push**, name `doctor-<server>`,
   heartbeat interval `3600`, retries `1`, notification ticked. Copy the token
   from the generated push URL.
2. Copy `doctor-push.sh` to the server (e.g. `~/doctor-push.sh`), replace
   `__KUMA_URL__` with the monitoring box's URL as reachable from that server
   (e.g. `http://<monitoring-tailnet-ip>:3001`) and `__TOKEN__` with that
   monitor's token, and `chmod 700` it (the token is a write credential to the
   monitor).
3. Optionally, a second Push monitor `doctor-disk-<server>` with the same
   settings, its token in the script's `FILLING_TOKEN`. Without it the
   doctor's 7-day disk warning only shows in the first monitor's messages, and
   the disk check's 30-day `HEALTHCHECK_FILLING_URL` is what pages.
4. Install the deploy user's cron (no sudo needed — doctor only needs docker
   access): `*/30 * * * * /bin/bash $HOME/doctor-push.sh`

Pinging every 30 min against a 60-min window tolerates a single blip without a
false alarm. The script prefers `doctor`; on a release that predates it, it
falls back to a direct container-health check. Two extras ride along in each
heartbeat: the message carries current disk usage and, once the disk-check
timer has a day of history, the filesystem nearest to full with its fill rate
and days until full (`OK_disk61pct_docker61pct_filestore_fill19.0GiBperday_full21d`,
or `doctor_disk_filling_...` while the doctor exits 3, the first monitor
staying up; visible in the heartbeat tooltip), and the numeric `ping` field
carries disk-used %, so the monitor's
"response time" chart doubles as a disk-usage trend graph — a deliberate,
lightweight stand-in for a real metrics stack until trend questions justify
one. The fill rate is read out of `doctor --json` by
[`doctor_trend.py`](doctor_trend.py), run from the server's checkout with the
host's `python3`; without either, the message simply carries no rate.

> The `doctor` command requires an up-to-date `mascope` CLI. `mascope prod
> update` refreshes images but **not** the CLI binary — if `doctor` is missing,
> rerun the `uv tool install` step from `tooling/ubuntu.sh`. Not
> `ubuntu.sh reinstall`: that goes through the uninstall path, which deletes
> `.runtime/state.json` and with it the server's active environment.

**Deliberate overlap with the disk-check timer.** `mascope-disk-check.timer`
(see `tooling/systemd/`) also watches the disk and pings healthchecks.io. That
is layering, not redundancy: doctor->Kuma is the rich aggregate but depends on
Python, Docker, the tailnet, and the monitoring box; the disk check is bash +
`df` + one HTTPS call to an external service — the dumbest reporter survives
the disk-full scenario that wedges everything else. Keep both, and stagger the
thresholds so the simple tier warns early and a doctor "down" means it is
serious. The defaults are staggered that way for both the floor and the fill
rate: the disk check alerts below 10 GiB / 10% free and, on its second check
(`HEALTHCHECK_FILLING_URL`), when that floor is projected within 30 days
(`MIN_DAYS_TO_FLOOR`); the doctor goes down below 5 GiB free
(`MASCOPE_UPDATE_MIN_FREE_GB`), and its disk monitor (`FILLING_TOKEN`) when
that floor is projected within 7 days (`MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR`) or
crossed. Both fit the same rate, from the history
the disk check records, so they agree on the pace. Set
`MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR` in `/etc/environment`, which
`doctor-push.sh` sources. The fleet playbooks pass the doctor's exit 3, so a
filling disk does not stop a rollout (`tooling/fleet/README.md`).

## 10. Scheduled security assessment (`run-assessment.sh`)

[`run-assessment.sh`](run-assessment.sh) runs the pen-test suite
(`security/pentest/`) against a staging deployment on a schedule and whenever
a new release reaches it, runs the suite's non-mutating subset against each
production deployment on a schedule of its own, and keeps the reports here. Uptime Kuma tells you the site
answers; nothing else tells you TLS is still enforced, the security headers
are still set, or tenant isolation still holds. Because deployed images are
pinned, what a periodic run actually detects between releases is
**environment drift** — an edge rule, a firewall change, a certificate
approaching expiry. What a release changes, the deploy run (below) assesses.

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
   checkout, so sync it and verify by hashing against the tag. Sync it to the
   tag the target runs, not ahead of it: `baseline-origin.json`, the origin
   pass's baseline (step 5 of "Production"), accepts findings a given release
   produces, and an acceptance whose control passes fails the run as stale.
   The `IDENT-02` acceptance, for one, matches only a target that trusts the
   Docker gateway. On a release from before that trust, or on a target
   running `MASCOPE_TRUST_DOCKER_GATEWAY=off`, `IDENT-02` passes: a copy
   synced ahead of the target goes red until the target catches up, and an
   `off` target's copy needs the entry removed. Update this runner with the
   suite: one from v1.16.0 or earlier names no baseline, so a later suite's
   origin pass reads `baseline.json`, which no longer holds `IDENT-02`, and
   fails on it.
2. Give the monitoring box a dedicated key for that account. Use `restrict` in
   the target's `authorized_keys`, server-side, rather than relying on the
   client's `ForwardAgent no`.
3. Write the config file (`/opt/pentest-runner/config.env`, `chmod 600`) with
   `TARGET_HOST`, the `MASCOPE_PENTEST_*` credentials and an optional `HC_URL`.
   The script header lists every variable. On a target with authenticated
   origin pulls on (`MASCOPE_ORIGIN_PULL=on`, `docs/maintaining.md`, "The
   edge"), set `MASCOPE_PENTEST_TARGET=https://127.0.0.1:8443`: its :443 then
   answers 403 to everything without Cloudflare's client certificate. That
   loopback listener differs from :443 only in asking for no client
   certificate, so the refusal itself is outside this vantage. The report
   says so: it records the listener it measured (`listener` in
   `pentest-report.json`, a **Listener** line in the Markdown) and, against
   the loopback listener, a notice that the origin-pull refusal was not
   assessed. The summary then reads `listener=loopback:8443
   origin_pull=not_assessed`. Check the refusal itself from the host with
   the `:443` request in `docs/maintaining.md`, "Turning origin pulls on".
4. Cron it clear of the backup windows, e.g. `30 5 * * 0`, and add the
   deploy watch beside it (see "Two triggers"):

   ```cron
   30 5 * * 0  /opt/pentest-runner/run-assessment.sh 2>&1 | logger -t pentest
   */5 * * * * /opt/pentest-runner/run-assessment.sh --on-deploy 2>&1 | logger -t pentest-watch
   ```

**Two triggers.** They answer different questions, so both run.

- **The periodic run** (no arguments) asks whether the ground has shifted:
  certificates, edge rules, base images, drift, none of which has a commit.
  Its heartbeat (`HC_URL`) is the dead-man switch, so it always runs, on
  every tick of its schedule, whatever ran before it.
- **The deploy run** (`--on-deploy`) asks whether this build broke a control.
  Each tick it reads the release the target reports on its anonymous
  `/api/version/public` route, on the target as the suite runs there, and
  assesses a release no run has started on yet. Otherwise it does nothing,
  and pings nothing. Its heartbeat is `DEPLOY_HC_URL`, or `HC_URL` when that
  is unset, so a deploy run that finds a broken control still pages.

The watch reads the deployment instead of being told by it. Whatever
promotes a release — the auto-update timer, an operator's `mascope prod
update`, a fleet playbook, a rollback — changes what the target reports, so
one watch covers every path. The target is treated as hostile and holds no
credential for this host: all it can do is name a version, which is bounded
and held to the shape of one before it is used. Each release is assessed
once, whatever its run concluded. A red run pages, and repeating it each tick
would only spend the target's rate limits. The watch needs a target that
reports a pinned release (v1.14.0 or later, `MASCOPE_VERSION` set to a
release, not `latest`). A target that answers nothing usable, `unknown` or
`latest` included, leaves each tick logging `cannot read the
deployed release`, and the periodic run's summary ends in
`deployed=unreadable` until it is fixed.

**Runs never overlap, and are spaced.** The suite verifies login rate limits
whose windows are a minute (per address) and five minutes (per account), so
two runs back to back throttle each other's controls, and two an hour apart do
not. A lock in `RECORDS` is held for the whole of a run. The next run starts
no sooner than `MIN_INTERVAL` seconds (default 600) after the last one ended.
A deploy run that meets either is deferred, logging why, and the next tick
looks again. A periodic run waits for both, up to `LOCK_WAIT` seconds
(default 7200) for the lock, and starts its heartbeat only once it runs.

A run whose controls were throttled anyway says so: the summary's
`throttled=N` counts the controls a rate limit answered first, and the
report opens with a notice (`security/pentest/README.md`, "Reading the
results"). It does not change the exit code. A throttled control is not
assessed, and not-assessed never fails a run.

**Assess a deployment with Turnstile off.** The suite signs in with a
password and cannot solve Cloudflare Turnstile's human check, so on a site
with Turnstile on its sign-in form (`docs/maintaining.md`, "Turning Turnstile
on") it signs in nowhere. It reads that from the site's login options at
session start and reports every control that needs a sign-in, or the login's
answer to a password, as not assessed: the site has Turnstile on. The run
stays green, the summary's `not_assessed=N` grows by those controls, and the
report counts them (`turnstile` in its summary) and opens with a notice. Its
`AUTHN-06` then checks that the login refuses a sign-in with no token. Point
the staging run at a deployment with Turnstile off, so the signed-in controls
are assessed somewhere, and do not give it Cloudflare's test keys to get past
the check: that turns the check off where it is assessed. `AUTHN-06` is then
not assessed on staging. The production run assesses it instead: it is
non-mutating and signs in nowhere, and on a site with Turnstile on it sends
`AUTHN-06`'s one login, for an account that does not exist, which costs one
request of the per-address login budget. Turnstile changes nothing else
there but the report's **Turnstile** line (`security/pentest/README.md`, "A
site with Turnstile on").

**The CDN vantage.** The origin run never crosses the edge, so it cannot see
anything the edge answers itself: the HTTPS redirect (`TLS-02`), WAF
behaviour, edge rate limits, headers the edge adds or strips. With
`CDN_TARGET` set, the same job runs the suite a second time, **here**, against
the deployment's public URL, after the origin run and a pause of
`VANTAGE_GAP` seconds (default 300, so the second run does not start inside
the login windows the first one spent for the same account). The two
vantages legitimately disagree, and that is the information:
`<ts>-vantage.md` says per control where they disagree and which layer
answered (`security/pentest/README.md`, "The CDN vantage").

1. Deploy `security/pentest/` with its venv on this box too, in `CDN_SUITE_DIR`
   (default `/opt/pentest-runner/suite/pentest`), synced to the same tag as the
   target's copy. Hash it against the tag, as for the target.
2. In the CDN console, add a **WAF skip rule keyed on a secret header**: a
   custom rule whose expression matches one request header against a long
   random value and whose action skips the rules that would refuse a scanner.
   Scope it to the assessed hostname. Not an address allowlist: it drifts when
   this box's address changes and exempts everything else behind that address.
   The uptime monitors use the same shape. Record the rule, without the value,
   with the rest of the fleet's console settings, outside this repository.
3. Optionally, a request-header transform rule that removes the header before
   the request goes to the origin, so the origin never sees or logs it.
4. In `config.env`, add `CDN_TARGET` (the public `https://` URL),
   `MASCOPE_PENTEST_HEADER_NAME` and `MASCOPE_PENTEST_HEADER_VALUE`. The header
   goes only to the edge, over TLS with the certificate verified. The target
   host never receives it, and the suite keeps the value out of the report
   and its console output.
5. Check it: a run with the header passes the WAF and gets real answers. The
   same run without it, or with a value the rule does not match, aborts
   before the first control: the suite stops when a public route the
   application always answers comes back 403, since every control behind it
   would be assessing the WAF. The job then fails rather than reporting
   refusals as controls that held.

**Which controls may run where.** Every control's marker says whether it is
mutating (`security/pentest/README.md`, "Mutating and non-mutating
controls"). Non-mutating means it signs in to no account, spends no budget,
writes nothing, and stays harmless on the day the control it checks is
broken. `AUTHN-06` is the one exception, written down beside it: on a site
with Turnstile on it spends one request of the per-address login budget, for
an account that does not exist. Only those run against production. A control with no classification
fails the run at collection, so a new one cannot reach production by default.

| Class | Runs against | Controls |
| --- | --- | --- |
| Non-mutating (`--non-mutating`) | staging and production | `RECON-01`, `RECON-02`, `RECON-03`, `HDR-01`, `HDR-02`, `HDR-03`, `HDR-04`, `HDR-05`, `HDR-06`, `AUTHN-01`, `AUTHN-06`, `TOKEN-01`, `INFO-02`, `TLS-01`, `TLS-02`, `CORS-01`, `CORS-02` |
| Mutating | staging only | every other control: they sign in, provision accounts, workspaces, devices or uploads, burst a rate limit, spend a login or pairing budget, or send a write that a missing gate would perform |

`selftest/test_classification.py` pins the first row against the markers, so
a control moved into it changes this table in the same pull request.

**Production.** `run-assessment.sh --production` runs the non-mutating subset
(`pytest --non-mutating`) against every deployment in `PRODUCTION_TARGETS`,
here, through the CDN, as the CDN vantage runs: the operator header sent, TLS
verified. No account goes along. Each deployment's report has the format and
the retention of a staging run's.

1. Check that every deployment runs v1.14.0 or later. The subset signs in
   nowhere, so it learns the build only from the anonymous
   `/api/version/public`, which v1.14.0 added. On an older deployment the
   suite cannot say which build it assessed, so it aborts that deployment's
   run with exit `4` and no report. Its summary reads `report unreadable
   trigger=production`, and the heartbeat fails on every run until it is
   upgraded or taken out of `PRODUCTION_TARGETS`.
2. Use the CDN vantage's suite copy (`CDN_SUITE_DIR`), or set
   `PRODUCTION_SUITE_DIR` to another, synced to the tag production runs.
3. Give each deployment's hostname the same WAF skip rule as the staging one
   (the CDN vantage, step 2), matching the same header and value.
4. In `config.env`, list the deployments as `NAME=URL` pairs, the public
   `https://` addresses. `NAME` (lower-case letters, digits, `-`) names the
   records, so the records on this box never need the hostname to say which
   deployment they are:

   ```sh
   PRODUCTION_TARGETS="site-a=https://a.example.com site-b=https://b.example.com"
   PRODUCTION_HC_URL=https://hc-ping.com/...   # optional; no heartbeat without
   ```

   An entry that is not `NAME=https://...`, or a `NAME` given twice, refuses
   the whole run (exit 2) before anything is sent. Give `PRODUCTION_HC_URL` a
   check of its own: without it the production run pings nothing. It never
   falls back to `HC_URL`, whose weekly check its daily pings would keep green
   after the periodic run stopped.
5. Accept a deployment's findings in a baseline of its own, never in
   staging's. Each run reads the one the runner names in
   `MASCOPE_PENTEST_BASELINE`:

   | Run | Baseline |
   | --- | --- |
   | staging, origin vantage | `baseline-origin.json` in `SUITE_DIR`, on the target |
   | staging, CDN vantage | `baseline.json` in `CDN_SUITE_DIR` |
   | production deployment `NAME` | `PRODUCTION_BASELINE_DIR/prod-NAME.json` |

   `PRODUCTION_BASELINE_DIR` defaults to `/opt/pentest-runner/baselines` and
   must be an absolute path, outside the suite copy each sync replaces; a
   relative one refuses the run (exit 2). A missing file accepts nothing, so
   a new deployment starts with every finding undecided. The files take the
   format of `baseline.json` (`security/pentest/README.md`, "Accepted
   findings"). Staging's acceptances are about staging: shared, an entry would
   accept the same gap on every deployment, and turn stale on each one where
   the control passes.

   Only a suite released after v1.16.0 reads `MASCOPE_PENTEST_BASELINE`. A
   copy synced to v1.16.0 or earlier (step 2) ignores it and reads its own
   `baseline.json`, so every deployment keeps staging's acceptances, and
   nothing in the run says so. Sync the production copy to a later release
   before relying on these files.
6. Cron it beside the others. It takes the same lock, so it never runs inside
   a staging run, waiting up to `LOCK_WAIT` like the periodic run. It neither
   waits out `MIN_INTERVAL` nor moves it: it spends none of the staging
   target's login windows.

   ```cron
   15 6 * * *  /opt/pentest-runner/run-assessment.sh --production 2>&1 | logger -t pentest-prod
   ```

Every deployment is assessed even when one fails. The run's exit code is the
first failing deployment's. A deployment behind the current release is
recorded (`lag=behind`) and not turned into exit `90`: what production runs is
the build users have, so the report is evidence about it. Its heartbeat is
`PRODUCTION_HC_URL`, carrying each deployment's summary as
`site-a: build=... | site-b: ...`.

**Telling the assessment from an attack.** Every request the suite sends names
it in its user agent, `mascope-security-assessment/1
(+https://github.com/ultra-trace-systems/mascope/tree/develop/security/pentest)`.
It is in each deployment's nginx access log. GlitchTip keeps it too, with the
request of any event raised while handling one: error reporting runs with
`send_default_pii=False`, which strips cookies, auth headers and client
addresses but keeps the user agent. So an event the assessment provoked is
traceable to it rather than read as an attack. Search for the string in
GlitchTip and in the logs to tell an assessment's traffic from anything else.
The same string at an hour no run is scheduled is someone else using it.

**Exit codes.** Normally the suite's, so the heartbeat reflects the assessment
rather than the plumbing. With both vantages, either one failing fails the
job, and the origin's code wins when both fail. A disagreement between them
does not fail it. Two overrides apply to each vantage, for cases where pytest
succeeded and the run still is not evidence:

| Exit | Meaning |
| --- | --- |
| `1` | the report was unusable (empty archive, unreadable JSON) |
| `75` | a periodic or `--production` run waited `LOCK_WAIT` for another run and assessed nothing |
| `90` | the target is **behind the current release** — honest, but about an artifact deployed nowhere. Never for `--production`, which records the lag only |
| `2` | `--production` without `PRODUCTION_TARGETS`, with an entry that is not `NAME=https://URL`, with a `NAME` given twice, or with a relative `PRODUCTION_BASELINE_DIR`; nothing was sent |

The lag comparison happens **here, never on the target**: the suite executes on
the box under test, so a release lookup performed there would be the deployment
whose currency is in question answering the question about itself. A lookup that
cannot resolve the current release records `lag=unknown` and changes nothing —
an unreachable third party must never be able to redden an assessment.

The release is looked up once, before either vantage runs, and passed to both
suites as `MASCOPE_PENTEST_CURRENT_RELEASE`, so each report records the lag
too. Only a release tag is passed: when the lookup fails or names anything
else, the reports say `unknown`. The report's verdict is a record; the exit
code is still this script's.

`tooling/cli/tests/test_run_assessment.py` runs this script as it is, with
`ssh` and `curl` stubbed, through the lag matrix, the exit-code overrides and
the two triggers; CI runs it with the CLI tests. Deploy the script from a checkout, so what runs
here is what was reviewed (`sha256sum` it against the tag), and change it by a
pull request rather than on the box.

Each vantage leaves three files in `RECORDS`: `<ts>-<vantage>.log`,
`<ts>-<vantage>.tar.gz` and `<ts>-<vantage>.summary`, where the vantage is
`origin` or `cdn`. The archive holds that run's report alone,
`report/pentest-report.json` and `report/pentest-report.md`. The evidence
trail is the series of these archives here, one per run. The suite's
`report/history/` is not archived: for the origin that copy is on the target,
which is no place for the record (section 10). With both, `<ts>-vantage.md` and `<ts>-vantage.json` hold
the comparison. A production run leaves the same three files per deployment,
`<ts>-prod-<NAME>.{log,tar.gz,summary}`, with `trigger=production` in the
summary. Everything is pruned at 400 days, deploy runs, periodic and
production ones alike. A summary reads
`build=... findings=N accepted=N stale=N verified=N not_assessed=N throttled=N listener=PORT lag=... trigger=schedule`,
or `trigger=deploy` for a deploy run. `listener` is the port the suite
measured, `loopback:PORT` on the host's own loopback, followed by
`origin_pull=not_assessed` on the :8443 listener (setup, step 3), and `?`
for a report from a suite that predates it. The heartbeat carries it, and with both
vantages carries both summaries and the comparison's counts
(`origin: ... | cdn: ... | vantages: agree=N disagree=N edge=N origin_only=N`).
Three dotfiles beside them hold the triggers' state: `.run.lock`,
`.last-run-end` and `.last-run-version`, the release the last run started on.
Remove `.last-run-version` to have the next watch tick assess the deployed
release again.

**Do not "simplify" the report retrieval.** It is written defensively against
the box it talks to: the report is pulled as one stream rather than a remote
glob (a hostile server chooses the filenames it sends, which would be a
file-write primitive on the host holding your backups), the stream is
size-bounded before it lands, the archive is never extracted, and only counts
are sent to the heartbeat service — finding detail describes weaknesses in your
own deployment and the heartbeat is a third party.

### The weakened-target guard (`run-weakened-guard.sh`)

A green assessment is only evidence if its controls can fail.
[`run-weakened-guard.sh`](run-weakened-guard.sh) checks that some of them can,
weekly and unattended. It brings up a throwaway demo stack on this host and
weakens it by configuration, one edit at a time. It strips a security header or
the auth cookie's flags, turns the nginx version banner on, adds a wildcard CORS
header, serves the API docs or schema, lets a client name its own address,
raises the upload cap, or switches the backend's rate limiter or write-origin
guard off with their test-only settings, which only a demo stack honours. Each control the edit should trip must then report a
finding
(`security/pentest/mutations/README.md`, "The weakened target"). It touches no
deployment, and spends nothing of the staging target's rate limits.

It needs Docker, which this host has (section 1). The stack's frontend is
published on loopback only, under its own compose project, and taken down
again however the run ends. Its volumes stay, so the next run reuses the
downloaded demo bundle. Its containers use the demo's fixed names
(`mascope_demo_*`), so no other demo stack may run on this host.

1. Use the CDN vantage's suite copy (`CDN_SUITE_DIR`), or set
   `WEAKENED_SUITE_DIR` to another copy of `security/pentest/` with its venv.
2. Put `docker-compose.demo.yaml` from the same tag beside it, by default at
   `/opt/pentest-runner/docker-compose.demo.yaml` (`WEAKENED_DEMO_COMPOSE`).
   Set `MASCOPE_VERSION` in `config.env` to that tag; the default is
   `latest`. The weakenings edit the nginx config the image ships, so the
   images and the suite copy must come from one release.
3. Optionally give it a heartbeat of its own, `WEAKENED_HC_URL`. It never
   falls back to `HC_URL`, whose weekly check its pings would keep green.
4. Cron it clear of the assessment, for example:

   ```cron
   30 3 * * 0  /opt/pentest-runner/run-weakened-guard.sh 2>&1 | logger -t pentest-weakened
   ```

The script header lists every variable. It sources the same `config.env`,
but the staging credentials and the edge header in it do not reach the guard:
the controls it runs sign in nowhere.

| Exit | Meaning |
| --- | --- |
| `0` | every expected control reported its weakening |
| `1` | a control stayed green on a target known to be weakened: a control that cannot fail. Read the log before believing it; the mutation guard's README says how to triage |
| `2` | nothing was established: an edit did not take, a witness did not see it, the suite did not run, or the stack never answered |
| `75` | another run of this guard was in progress; nothing ran |

Any non-zero pings `/fail`. An edit that cannot apply, say on an image whose
nginx config no longer has the line, stops the frontend instead of serving a
healthy stack, and the run says `2` rather than blaming a control. Each run
leaves `<ts>-weakened.log` and `<ts>-weakened.summary` in `RECORDS`, pruned at
400 days. The summary reads `detected N  missed N  unproven N version=TAG`.

The guard covers 13 controls. The log's coverage line counts the others by
reason, and each control's marker gives its own (`weakening=` in
`security/pentest/test_*.py`, which `mutations/weakened.py` reads):
application code no configuration reaches, a missing test-only switch, a
topology the demo does not have, or an edit not yet written. The guard
itself takes about three minutes once the stack is up; the upload-cap edit is
the slowest, since it restarts the backend twice.
`tooling/cli/tests/test_run_weakened_guard.py` runs this script with `docker`,
`curl` and the guard stubbed; CI runs it with the CLI tests.

## 11. Security fixes not yet deployed (`security-fix-distance.py`)

A fix on `develop` is not a fix in production.
[`security-fix-distance.py`](security-fix-distance.py) asks one question, on a
schedule: is there a merged security fix that a production deployment does
not run? It names each deployment that is behind and the fix it is missing.
When every deployment runs every fix, it says nothing.

- **A security fix** is a commit that adds a changelog fragment in the
  security section, `changelog.d/<branch>.security.md` (`CLAUDE.md`,
  Conventions). The fragment lands with the fix, at merge, so the check sees
  the fix from that moment. Cutting a release deletes the fragment, but the
  commit that added it still marks the fix. A security fix filed under
  `fixed` is one the check never sees. Fixes released before the fragments
  existed are in `CHANGELOG.md` alone and are not checked.
- **A security update of a dependency a deployment runs** is a fix too.
  Renovate's vulnerability updates add no fragment. Their subject ends in
  `[security]`, and the check counts such a commit when it changes the
  version of a package that ships:
  - in `uv.lock`, a package the root project reaches through its
    `dependencies`, and the extras those ask for. That is what the backend
    image installs (`uv tool install .`). The root's `docs` group ships too:
    the frontend image builds the user docs with it and serves them at
    `/docs/`, the theme's JavaScript and CSS included. A package only the
    `dev` or `lint` group reaches does not ship.
  - in `server/frontend/package-lock.json`, a package the lock does not mark
    `"dev": true`. npm marks the ones only `devDependencies` reach, and the
    frontend image bundles the rest.

  The check reads both locks as the commit and its first parent have them.
  A package counts when it ships after the commit, or when the commit took
  it out and it shipped before. Markers are not weighed: a package only
  Windows installs still counts. A lock it cannot read counts as a fix, so
  an unknown format never hides one. Other lockfiles, such as the File
  Agent's `agents/file/uv.lock`, ship to no deployment and are not read. The
  fix is named after the package, `deps-pillow`, or `deps-axios-and-2-more`
  when the bump moved packages it pulls in too.
- **What a deployment runs** is the release its backend reports on the
  anonymous `/api/version/public` route, read from this box through the
  deployment's public URL. It is the running backend answering, never a
  record of what was deployed.
- **A deployment has a fix** when the release tag it runs contains the fix's
  commit (`git tag --contains`). A release candidate has the fixes its own
  tag holds, and no later ones.
- **A version the check cannot place is a finding**, never a skip: no answer,
  an HTTP error, `unknown`, `latest`, a dated build tag, or a release tag the
  repository does not have. None of them shows the fix is there.

The check keeps a bare clone of its own and fetches `develop`, `master` and
the tags into it on every run. It needs no token: the repository is public.
The first run fetches the whole history, about 100 MB; later runs fetch only
what is new, in a second or two. A run that cannot fetch fails its
heartbeat, rather than reading a stale clone.

Setup:

1. Copy the script from a checkout to `/opt/pentest-runner/`, and
   `sha256sum` it against the tag, as for `run-assessment.sh`. It needs `git`
   and the host's `python3`, 3.10 or later, and nothing else.
2. Add a heartbeat check (healthchecks.io, or any service with the same
   `/start` and `/fail` URLs) with a period of one hour and a grace of one
   hour.
3. Write `/opt/pentest-runner/fix-distance.ini` (`chmod 600`), with one line
   per production deployment. The aliases are yours. They appear in the log,
   never in the heartbeat.

   ```ini
   [check]
   heartbeat = https://hc-ping.com/<uuid>
   # Optional, with their defaults:
   # repository = /opt/pentest-runner/fix-distance.git
   # remote = https://github.com/ultra-trace-systems/mascope.git
   # branches = develop master
   # timeout = 10

   [deployments]
   server-a = https://a.example.com
   server-b = https://b.example.com
   ```

4. Cron it, hourly:

   ```cron
   17 * * * * python3 -I /opt/pentest-runner/security-fix-distance.py 2>&1 | logger -t fix-distance
   ```

   `--config <path>` reads another file.

Each run pings `/start`, then the heartbeat with
`fixes=N unreleased=N deployments=N behind=N unreadable=N`: plain on success,
on `/fail` when anything is behind or unreadable. A check that stops running
stops pinging, and the heartbeat pages: that is the dead-man switch.

Which deployment misses which fix stays on this box, in the log
(`journalctl -t fix-distance`): it describes an open weakness of the fleet,
and the heartbeat service is a third party. One line per finding:

```text
behind: server-a runs v1.15.0, missing 1a2b3c4d5 mas-123-session-expiry (merged 2026-10-28, not released)
behind: server-b runs v1.14.0, missing 5e6f7a8b9 mas-120-secret-file-modes (merged 2026-10-20, released in v1.15.0)
behind: server-b runs v1.14.0, missing 0c1d2e3f4 deps-pillow (merged 2026-10-25, released in v1.15.0)
unreadable: server-c runs 'latest', which names no release
```

`not released` means no release holds the fix yet, so every deployment is
behind until one is cut and rolled out. `released in vX.Y.Z` names the first
release that holds it. There is no grace period: a fix reports from the run
after it merges, and the heartbeat stays failed until it runs everywhere.

The route is anonymous, but it is read through the edge. An edge rule that
challenges scripts has to let this box's requests to `/api/version/public`
through, or every deployment reads as unreadable. The check sends the
User-Agent `mascope-fix-distance`.

| Exit | Meaning |
| --- | --- |
| `0` | every deployment runs every marked fix; nothing on stdout |
| `1` | a deployment is behind, or its release cannot be read |
| `2` | the configuration is unusable (missing, no heartbeat, no deployments) |
| `3` | the repository could not be fetched: the check is blind |

`tooling/cli/tests/test_security_fix_distance.py` runs the script as it is,
against a real git repository and a local server standing in for the
deployments and the heartbeat. It shows the check failing both ways: a marked
fix no release holds reports, and a fleet that runs every fix is quiet. CI
runs it with the CLI tests.

## 12. Receiving the fleet's backups

If this box is also the off-site restic target for your Mascope servers (the
arrangement `tooling/backup-cron.sh` on each server expects), that side needs
setting up **here**: give each server an account of its own, SFTP-only and
chrooted to its own directory, its key usable only from that server's
address. The recipe, sshd drop-in and checks included, is
[Using your own backup server](../../docs/maintaining.md#using-your-own-backup-server)
in the maintainer runbook. Not one shared account with a shell: every server
would hold a key that can delete every other server's backups and tunnel
through this box.

Points that are easy to get wrong, each of which has bitten someone:

- **restic over `sftp:` is SSH**, so any network policy between the servers and
  this host must keep **port 22** open. A tailnet ACL draft that allowed only
  the monitoring ports would have silently stopped every off-site backup — the
  backups would have failed nightly and the failure is only visible if the
  dead-man's-switch pings (below) are actually wired up.
- **One repo per server, each with its own password and its own account.** A
  shared repo means one compromised server can read — or delete — every other
  customer's backups; a shared account still lets it delete them.
- **The repo password is the whole restore.** Repo plus password is sufficient;
  neither alone is worth anything. Keep the passwords in the password manager,
  never on this box.
- These repos are **not** included in this host's own backup (§5). They are
  large, and they are themselves backups — but it does mean they exist in
  exactly one place, so decide deliberately whether that is acceptable. The
  sshd drop-in and the key directory are configuration, though: name them in
  `EXTRA_CONFIG_PATHS`, or a rebuilt box refuses every server.

## 13. Restoring this host

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
6. Re-add the scheduled jobs (§5, §9, §10, §11) — cron entries are not part of the
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
