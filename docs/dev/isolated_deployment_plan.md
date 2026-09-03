# Isolated deployments: reaching and updating a server with no internet route

Goal: run a Mascope server at a customer site where the server itself has no
route to the internet, the instrument PC next to it has one only when the
customer says so, and maintaining that server is not harder than maintaining a
cloud one. Two things have to keep working across the gap: *reach* (a shell on
the server when a human is needed) and *transfer* (releases in, diagnostics
out) - and the second should need no human at all.

This document records (1) the setting and the constraint that shapes the
design, (2) every place the deployment reaches out to the internet today and
what breaks without it, (3) the cheapest option, which is not isolation at all,
(4) the access design, (5) the update design, (6) the diagnostics design, (7)
the host-level hygiene an isolated server needs, and (8) the build phases. It
is written against the deployment tooling as it exists in this checkout, with
the code paths cited, so the proposal can be checked against what runs.

> **Status (2026-09-03): proposal, nothing built.** Written for sign-off
> before any code. Section 4 is configuration only and can be piloted at one
> site without touching the repository; sections 5-7 are CLI and docs work.

## 1. The setting

The topology at these sites:

- **Instrument PC** (Windows): runs the instrument control software and the
  Mascope File Agent (`agents/file`). Has internet access either permanently or
  in windows the customer opens deliberately.
- **Mascope server** (Ubuntu, provisioned with `tooling/ubuntu.sh`): on the
  same LAN as the instrument PC, or on a direct link to it. By customer policy
  it has **no route to the internet**, permanently.
- **Users** reach the web UI over the LAN.

Maintenance today is remote desktop into the instrument PC, then SSH from there
to the server. That is two hops, one of them a GUI; it rides a third-party relay
that gives full desktop control of a machine that also drives the instrument;
it needs a licence per site; and nothing about it can be automated - every
update is a session with a human at both ends.

The constraint that shapes everything below: **the customer owns the switch.**
They chose an isolated server for a reason, so any design that quietly gives the
server a default route, or a standing inbound path, defeats the point and will
be refused by the same IT department that asked for isolation. What they will
accept is outbound-only, off by default, turned on by them, logged on both
sides - and, for the routine work, no session at all.

## 2. What a deployment reaches out to today

Everything a self-hosted server contacts, and what happens when it cannot.
Only the first five rows are Mascope's own update path; the rest is host
hygiene that any isolated Linux box has to solve.

| Need | Where it is | Reaches | Without a route |
|---|---|---|---|
| Release discovery | `auto_update.latest_release_tag` (`tooling/cli/src/mascope_cli/cmd/prod/auto_update.py`) | `api.github.com`, unauthenticated | Returns `None`; `_auto` logs "Could not determine the latest release" and exits 2. `mascope-update.service` accepts only exit 0 and 30, so an isolated server with the timer enabled has a **failed unit every night**, and one without it has nothing at all. |
| Release manifest | `auto_update.download_manifest` | the release asset `mascope-manifest.json` | Falls back to reading the Alembic head out of the target image - which also needs the image. |
| Images | `docker compose pull` in `prod update`; `preflight.pull_image` during `--check`/`--auto` classification | `ghcr.io` and its blob host | The pull fails. The manual path aborts before touching the running stack, which is the right failure but still no update. |
| Checkout alignment | `_align_checkout` (`cmd/prod/main.py`) | `git fetch origin tag vX.Y.Z` from `github.com` | Warns; the checkout stays on the old tag, so a reboot would redeploy the old release and `prod doctor` reports DRIFT. |
| CLI reinstall | `uv tool install ... .` (`tooling/ubuntu.sh`, `docs/hosting.md`, `docs/maintaining.md`) | PyPI, for the CLI's dependencies | Fails. The CLI then trails the checkout - the drift `cli_drift.py` exists to detect, and which took a server down once when a release added a compose secret. |
| Provisioning | `tooling/ubuntu.sh install` | apt mirrors, the Docker apt repository, the NodeSource script, `packages.microsoft.com`, the snap store for uv, restic from apt | A first install needs internet. Nothing after it does, except the rows above. |
| OS patching | `tooling/fleet/roles/unattended_upgrades` | apt security mirrors | Never upgrades, silently. |
| Error reporting | `MASCOPE_SENTRY_DSN` (`docs/maintaining.md`, Monitoring) | the monitoring box over the tailnet | Events are dropped. `sentry_sdk` ships only HTTP transports - there is no on-disk queue to flush later. |
| Health pings | `tooling/monitoring/doctor-push.sh`; `HEALTHCHECK_URL` in the backup and disk-check envs | Uptime Kuma; a healthchecks.io-style URL | The dead-man's switches go red or were never armed. |
| Off-site backups | `tooling/backup-cron.sh`, layer 2 | a restic repository over SFTP or S3 | The local dump layer still runs; the off-site copy fails every night. |
| TLS renewal | the DNS-01 option in `docs/hosting.md` | the CA and the DNS provider's API | The certificate expires. Self-signed and internal-CA certificates are unaffected. |
| Time | NTP | pool or the customer's server | The clock drifts. TOTP verifies with a 30 s step and one step of tolerance either way (`mfa/config.py`), so beyond roughly a minute of skew every second factor fails; token expiry is time-based too. |
| User docs | none | - | Bundled into the frontend image; `mkdocs.yml` already vendors fonts, KaTeX and mermaid *specifically* so the docs work air-gapped. The product already assumes this deployment at the docs layer. |
| File Agent uploads | `agents/file` | the Mascope server, over the LAN | Unaffected. The agent retries uploads and token renewal on its own when the server is unreachable, and the instrument PC's internet state never enters into it. |

One non-shortcut worth ruling out explicitly: pointing `MASCOPE_UPDATE_REPO`
at a customer-side mirror does not help. Discovery is the unauthenticated
GitHub releases API (`update.env.example` says so), and the images come from
GHCR regardless of the repository setting.

## 3. Option 0: an egress allow-list, not isolation

Ask this first in every deal, because it costs nothing. "No internet" often
means "no unrestricted internet", and an IT department that refuses a default
route will frequently accept an outbound firewall rule to a fixed list of
names. If they do, the whole existing update path works unchanged, the fleet
tooling works unchanged, and only the access problem (section 4) remains.

The list, from the table above:

- `api.github.com` and `github.com` - release discovery, the manifest, the tag
  fetch;
- `ghcr.io` and `pkg-containers.githubusercontent.com` - image manifests and
  blobs;
- `pypi.org` and `files.pythonhosted.org` - the CLI reinstall;
- the Ubuntu security and archive mirrors, or the customer's own mirror - OS
  patching;
- an NTP source;
- optionally the monitoring endpoints (a healthchecks.io-style URL, and the
  monitoring box if a tunnel to it exists).

Nothing in the repository blocks egress today: the fleet firewall role sets
`default allow outgoing` (`tooling/fleet/roles/firewall/tasks/main.yml`). An
allow-listed site is simply a fleet server whose *customer* firewall does the
restricting, and it can be put in the Ansible inventory like any other.

The rest of this document is for the sites that say no to this.

## 4. Reach: a customer-switched tunnel through the instrument PC

The instrument PC is the only machine at the site with an outbound path, and it
already sits on the server's network. Use it as a gateway *for administrators*,
never as a gateway for the server.

### 4.1 The mechanism: a subnet router on the instrument PC

Install the Tailscale client on the instrument PC and have it advertise the
server's LAN address (a single host route is enough; the Windows client
supports subnet routing). Approve the route on the tailnet. Every admin device
on the tailnet can then SSH to the server's LAN address directly, with no
desktop session and no second hop.

This is the same fabric the fleet already runs on: the servers are
administered over Tailscale SSH (`tooling/fleet/roles/sshd_hardening`), the
Ansible inventory carries tailnet addresses, `mascope fleet logs` runs over
it, and the monitoring box is reached over it. An isolated site becomes one
more inventory entry whose address happens to be reached through a route:
`ansible-playbook update.yml --limit <site>` and `mascope fleet logs <site>`
work as they are.

Two things in the repository's host configuration assume the server has its
own tailnet interface, and need a variable for the routed case:

- The `firewall` role allows SSH **on the `tailscale0` interface only**
  (`sshd_hardening` is what makes it key-only). An isolated server has no such
  interface; its SSH must be allowed from the instrument PC's LAN address
  instead (still key-only, still nothing else inbound). Add a per-host variable
  for the allowed SSH source.
- The Cloudflare-only 443 rule is irrelevant here; the UI is reached over the
  LAN. The role should skip it for isolated hosts.

A subnet route does **not** give the server internet access - routes carry
traffic from the tailnet *to* the advertised subnet, not the other way. The
server stays as isolated as it was; the tunnel adds an inbound path from named
admin devices while it is up, and nothing else.

### 4.2 The switch stays with the customer

- The Tailscale service on the instrument PC is **stopped by default**. A
  desktop shortcut (or a scheduled task the customer owns) starts it for a
  maintenance window and a second one stops it; a timed stop after a fixed
  number of hours guards against forgetting.
- On the tailnet side, tag the node per site and write an ACL that lets only
  the admin devices reach port 22 on that route - nothing else on the site's
  network is reachable, and nothing at the site can reach the tailnet. Require
  device approval for new nodes and leave key expiry on.
- The connection log exists on both sides: the customer sees the service
  start and stop, the tailnet admin console shows every connection.

Compared with remote desktop, the pitch to the customer's IT is: outbound-only
from their side, no desktop control, no third party with a view of the
instrument screen, off unless they turn it on, and a route that reaches one
port on one machine.

### 4.3 Alternatives when a third-party control plane is refused

- **Cloudflare Tunnel.** `cloudflared` on the instrument PC publishing the
  server's address as a private network, with Cloudflare Access in front and
  the WARP client on the admin device. Functionally the same shape with a
  vendor the fleet already depends on for its public edge.
- **A plain reverse SSH tunnel.** Windows ships an OpenSSH client; a scheduled
  task on the instrument PC opens `ssh -R` to a bastion Ultra Trace runs, and
  admins jump through the bastion. No third-party control plane at all, at the
  cost of running and hardening the bastion and managing its keys.

Both keep the customer's switch: the tunnel process is what they start and stop.

### 4.4 Explicitly not

- No exit node, Internet Connection Sharing, or NAT that gives the server a
  default route via the instrument PC. Fragile, and it is exactly what the
  customer refused.
- No Tailscale (or any agent) *on the server*: it needs egress to its
  coordination server, which the server does not have.
- Remote desktop stays as a fallback for the instrument PC itself - supporting
  the instrument control software is a separate matter - but it stops being
  the path to the server.

## 5. Transfer in: a signed release bundle and an inbox

Most maintenance sessions are updates, and an update needs no human on the box
if the bits can arrive on their own. The design keeps every decision the
existing updater already makes - classification, maintenance window, grace
period, health check, checkout alignment - and replaces only where the bits
come from.

### 5.1 What a bundle contains

One file per release, `mascope-bundle-vX.Y.Z.tar`, holding:

- `images.tar` - `docker save` of every image that release's compose file
  names, with the exact tags the compose file references: the backend image
  (which also serves `db_init` and `file_converter` through the compose
  anchor), the frontend image, and the pinned third-party images
  (`postgres:16-alpine`, `redis:7-alpine` today). The third-party images must
  be in the bundle: the server cannot pull *anything*, and a release that bumps
  the Postgres tag would otherwise fail to start.
- `mascope-manifest.json` - the existing release manifest
  (`release_manifest.py`, schema version 1, carrying the Alembic head).
- `source.bundle` - a `git bundle` containing the release tag, incremental
  from the previous release when the exporter is told which one, full
  otherwise. The deployment is a git checkout whose tag selects the release
  and which `prod doctor` reads, so the tag has to arrive as a git object;
  `git fetch <file>` accepts a bundle as a remote, offline.
- `wheels/` - the CLI's locked dependencies as wheels for the deployment's
  platform (Linux x86-64, CPython 3.12), so the CLI can be reinstalled with
  uv's offline index options. This is the row of the table in section 2 that
  is easy to forget: without it the checkout moves and the CLI does not.
- `bundle.json` - the bundle manifest: release tag, schema version, the list
  of files with their SHA-256, the previous tag the git bundle is incremental
  from, creation time.
- `bundle.json.sig` - a detached signature over `bundle.json`.

### 5.2 Producing it

A job in `.github/workflows/build-release-images.yaml`, after the images are
published and alongside the manifest upload, builds the bundle and uploads it
as a release asset. The same code is exposed as `mascope prod bundle export
vX.Y.Z` for an ad hoc build on any machine with Docker and internet.

Two constraints to design around rather than discover:

- **Size.** Four images plus a wheelhouse is on the order of a gigabyte or
  two. GitHub caps a single release asset at 2 GiB, so the exporter must be
  able to split `images.tar` into parts and the importer to reassemble them;
  `bundle.json` lists the parts and their hashes.
- **Disk on the server.** The update disk guard (`MASCOPE_UPDATE_MIN_FREE_GB`)
  is sized for pulling one release's images. A bundle apply holds the tar *and*
  the loaded images at once, and section 5.6 keeps the previous release's
  images around; the guard needs a higher floor in inbox mode.

### 5.3 Signing

The customer's IT will ask what stops a tampered bundle. The signature is what,
and it has to be verifiable on the server with nothing installed for the
purpose:

- Sign `bundle.json` in CI with an SSH signing key (`ssh-keygen -Y sign`) and
  verify on the server with `ssh-keygen -Y verify` against an
  `allowed_signers` list shipped inside the CLI. The server has an OpenSSH
  client by construction, so this adds no dependency; minisign is the
  equivalent if a smaller key format is preferred.
- Every file's hash is in the signed `bundle.json`, so the signature covers
  the images, the git bundle and the wheels transitively.
- The CLI refuses an unsigned or mis-signed bundle. A development escape hatch
  (`--allow-unsigned`) exists for testing and is loud about it in the status
  log. Rotation: `allowed_signers` can hold two keys during a rotation, and
  the signature names the key.
- This is separate from the Authenticode signing of the File Agent installer
  (the `release-signing` environment and `AZURE_SIGNING_ACCOUNT` gate in the
  same workflow); that service signs Windows binaries, not arbitrary files.

### 5.4 Applying it, and the inbox the timer watches

`mascope prod bundle apply <file>` does, in order: verify the signature and
every hash; check the schema version and that the tag matches `vX.Y.Z`;
`docker load images.tar` (checking that the loaded tags are the ones the
bundled compose file references); `git fetch` the tag from `source.bundle`;
classify with the existing preflight - `preflight.build_plan(pull=False,
target_head=<manifest head>)`, which is exactly what `prod update --check
--no-pull --manifest` does today; then hand over to the existing apply path
(`_apply_update`), which recreates the containers and health-checks the
backend. `docker compose up` uses the loaded images because they are present
locally; the `pull` step is skipped in this mode. `_align_checkout` needs one
change: fetch the tag from the git bundle when there is no reachable origin.

The unattended path gets an **inbox**: `.runtime/update/inbox/`, next to the
existing `state.json`, `status.log` and `manifests/`. A new setting in
`/etc/mascope/update.env`, `MASCOPE_UPDATE_SOURCE=inbox` (default `github`,
today's behaviour), makes `prod update --auto` discover the target as *the
newest validly signed bundle in the inbox* instead of asking the GitHub API.
From that point the flow is the one in `_auto` unchanged: up-to-date does
nothing; a fast update applies inside `MASCOPE_UPDATE_WINDOW`; a migration
update is recorded, reported with exit 30, and applied after
`MASCOPE_UPDATE_GRACE_DAYS` or an explicit `--confirm`, unless snoozed. The
customer gets the same window and grace semantics the fleet has, and the
nightly unit stops failing for lack of network.

The one rule the updater keeps is that it **never reinstalls the CLI from
inside its own run** (`cli_drift.py` explains why: the update may be running
from the stale tool). On a fleet server the runbook makes the operator do it
afterwards; an isolated server has no operator on it. So the reinstall
becomes a separate process that runs *after* the update service exits: an
`ExecStartPost=` step (or a second oneshot unit ordered after it) that, when
`status.log` records a release applied, runs the `uv tool install` from the
runbook against the bundle's wheelhouse with uv's offline index options. The
next `mascope prod up` then drives the new compose file with the matching CLI,
which is precisely the case the runbook's warning box is about.

After a successful apply the bundle is moved to `.runtime/update/applied/`
with its `images.tar` deleted (the images now live in Docker's store) and its
`bundle.json` kept, so the history of what arrived and when survives.

### 5.5 How a bundle reaches the inbox

Three carriers, in the order they should be built:

1. **The customer copies it.** A USB stick, or an SMB share the server exports
   to the instrument PC. Zero code beyond the inbox itself, and it is what an
   air-gapped site's IT expects from an appliance. The inbox must tolerate a
   half-copied file: validate only files whose hash matches `bundle.json`, and
   ignore anything else.
2. **An owner uploads it in the web UI.** A route that accepts a bundle from
   the server-owner role and writes it into the inbox. The backend container
   has no view of `.runtime/update/` today (the compose file mounts the env
   directory, the filestore and the backup directories, not the update
   directory), so this needs one more volume mount, and the upload should go
   through the same resumable path the File Agent uses rather than a single
   multipart request of a gigabyte. The backend only stores the file; the CLI
   still verifies the signature, so a compromised session cannot install
   anything unsigned.
3. **The File Agent carries it.** The agent is the one component that sits on
   both networks. In a "carrier" mode it would, whenever the instrument PC is
   online, fetch the newest bundle from a configured URL (the release asset,
   or a mirror Ultra Trace runs) and upload it to the server via carrier 2's
   route. This closes the loop with no human step at either end: a release is
   published, the next time the customer opens the window the bundle arrives,
   the next maintenance window applies it. It is also scope creep on an agent
   whose job is uploading acquisition files, and it needs a device-token scope
   of its own (the device identity work in `docs/dev/device_identity_plan.md`
   is where that lives). Build it last, and only if carriers 1 and 2 turn out
   to be the friction.

When the section 4 tunnel is open, none of these is needed: the Ansible
rollout can `scp` the bundle to the server and run `prod bundle apply` in the
same play, so `update.yml` gains an isolated-host branch and a fleet-wide
rollout covers isolated sites in the same command.

### 5.6 Rollback

Today the manual update path prunes the superseded release's images
(`_prune_images`) because they can be pulled again. On an isolated server they
cannot, so in inbox mode the apply **keeps the previous release's images** and
the previous bundle's metadata. Rolling back is then `prod bundle apply` of the
previous release (or `prod update --version <previous>` against the images
still present), plus restoring the pre-migration dump that `db_init` takes
(`docs/maintaining.md`, Pre-migration dumps) if a migration had run - the same
procedure the hosting guide documents, minus the pull.

## 6. Transfer out: a support bundle and an outbox

The other half of a maintenance session is looking at the server. Package that
so it can travel without one.

`mascope prod support-bundle [--out <file>]` writes one archive containing:
`prod doctor --json`; `.runtime/update/state.json` and `status.log`;
`docker compose ps`; the last few thousand lines of each container's logs;
`journalctl` for the `mascope*` services and timers; `df` and
`docker system df`; the Alembic revision applied in the database; OS, kernel,
Docker and CLI versions and uptime; and the contents of `/etc/mascope/*.env`.
Everything passes through a redaction step before it is written: secret file
contents never enter the bundle at all, and any env value whose key looks like
a token, key, password or URL with credentials is replaced by its hash so two
bundles can still be compared.

The bundle lands in `.runtime/update/outbox/` and travels by the mirror image
of section 5.5: the customer copies it out, an owner downloads it from the web
UI, or the File Agent relays it to a configured endpoint when it is online. The
last of those also gives the fleet a heartbeat from isolated sites: the agent
can forward a small status document to the Uptime Kuma push URL the way
`doctor-push.sh` does directly from connected servers, so an isolated site
that has fallen over shows up as a missed push rather than as silence.

Error reporting stays off at isolated sites (`MASCOPE_SENTRY_DSN` unset): with
no route to the monitoring box every send fails, and the SDK has nothing to
queue them in. The support bundle is the substitute, and the runbook should
say so rather than let someone spend an afternoon on why events do not arrive.

## 7. Isolated mode as a setting the tools understand

Rather than a collection of per-command flags, one setting -
`MASCOPE_UPDATE_SOURCE=inbox` in `/etc/mascope/update.env` - should drive
every behaviour that differs:

- `prod update --auto` discovers from the inbox (section 5.4) and never
  contacts the GitHub API, so "no network" stops being an error.
- The apply path keeps the previous release's images (section 5.6) and uses
  the higher disk floor (section 5.2).
- `prod doctor`'s **updates** section reports the source, the last applied
  bundle and when, and the inbox state ("empty", "vX.Y.Z pending, migration,
  grace ends <date>"), instead of implying a network check. The JSON report
  grows matching fields, so `doctor-push.sh` and the support bundle carry them.
- `--auto`'s status log records bundle arrivals and verification failures, so
  a bundle that was copied but rejected is visible without a shell.

## 8. Host hygiene on an isolated server

None of this is Mascope code, but every item has bitten some isolated Linux
box, and the runbook should carry a checklist:

| Concern | Do this |
|---|---|
| Time | Point the host's time sync (`systemd-timesyncd` on Ubuntu) at the customer's NTP server, or at the instrument PC, whose Windows Time service can be configured to serve NTP. Say why in the runbook: a minute of skew locks every two-factor user out. |
| TLS | Use the internal-CA or self-signed option from `docs/hosting.md`. The DNS-01 option needs egress at every renewal and is out. |
| OS patching | Unattended upgrades cannot run. Either apply security updates during a section 4 window (the fleet's `reboot.yml` already verifies the stack after a reboot), or agree that the customer's own patching regime covers the host. Write down which, per site. |
| Backups | Keep `backup-cron.sh`'s local layer; point restic at a local path or a customer NAS over SFTP instead of an internet target. If off-site copies are required, the instrument PC can push the local restic repository out when it is online. |
| Monitoring | Leave `HEALTHCHECK_URL` and the DSN unset so nothing fails nightly for want of a route. The heartbeat in section 6 is the replacement; until it exists, the doctor report in the support bundle is. |
| Disk | The disk-check timer is local and keeps working; enable it as usual. |

## 9. Provisioning

An isolated server is provisioned **before it is isolated**: run
`tooling/ubuntu.sh install`, pull or load the release's images, seed the CLI's
wheelhouse, generate the secrets and certificate, and set
`MASCOPE_UPDATE_SOURCE=inbox` - all with internet, at Ultra Trace or in the
customer's staging network - and then ship or move the box. This is the
appliance model these customers picture. On site, the first owner registers
over the LAN, the File Agent pairs over the LAN, and the box never needs egress
again except through the inbox.

The alternative, a temporary tether on site for the install, works but puts
the one internet-dependent step in the one place that has no internet;
avoid it as the default.

## 10. Build phases

**Phase A - access, configuration only.** Pilot the section 4 subnet router at
one site: install and tag the node, write the ACL, give the customer the
start/stop shortcuts, add the site to the Ansible inventory, and add the
SSH-source variable to the `sshd_hardening`/`firewall` roles so the drift
check is clean for a host without `tailscale0`. Add "Option 0" (section 3) to
`docs/hosting.md` as the egress list to put in front of every prospect. No
release needed.

**Phase B - the bundle.** `prod bundle export` and the CI job; the signing
key and `allowed_signers`; `prod bundle apply`; the inbox and
`MASCOPE_UPDATE_SOURCE` in `--auto`; the post-run CLI reinstall; the keep-N-1
and disk-floor changes; `doctor`'s inbox reporting; an "Isolated deployment"
section in `docs/hosting.md` and a matching runbook chapter in
`docs/maintaining.md` with the section 8 checklist. Tests: the bundle
round-trip against the CLI's hermetic suite (`tooling/cli/tests/`), and one
end-to-end apply on the LAN test host with the network namespace closed.

**Phase C - diagnostics.** `prod support-bundle`, the redaction step, the
outbox.

**Phase D - carriers.** The owner upload/download routes and the volume mount;
then, if warranted, the File Agent carrier mode with its device-token scope
and the heartbeat relay.

**Phase E - fleet integration.** The isolated-host branch in `update.yml`
(scp + apply over the tunnel), inventory conventions for sites reached through
a route, and Uptime Kuma monitors keyed on the heartbeat instead of a probe.

Phase A removes the remote-desktop hop this month. Phase B removes the need for
a session for every routine update, which is most of them. C and D make the
remaining sessions rarer; E folds the sites back into the normal rollout.

## 11. Deliberately not in scope

- Giving the server any route to the internet, however narrow, as a side
  effect of the tooling. If a customer wants that, it is Option 0 and their
  firewall does it.
- Automatic OS patching for isolated hosts. The tooling cannot honestly
  promise it; the runbook says who does it instead.
- Replacing remote desktop for the *instrument PC*. Supporting the instrument
  control software is not Mascope's problem to solve here.
- Peer distribution, delta images, or a private registry at the site. A signed
  tarball is what an isolated site's IT can inspect and approve; a registry is
  another service to run and secure at the customer.
- Any telemetry beyond the opt-in heartbeat of section 6.

## 12. Open questions

- **Bundle size versus the release-asset cap.** Measure a real bundle before
  deciding whether splitting is a corner case or the normal case. If the
  third-party images dominate, a "images unchanged since vX" optimisation is
  tempting; resist it until a measurement says it matters, because it makes
  the bundle's validity depend on what the server already has.
- **Key custody.** Where the SSH signing key lives (a CI secret gated like
  `release-signing`, or a hardware key used by a release manager) and who can
  rotate `allowed_signers` in the CLI.
- **Whether the File Agent should carry updates at all** (section 5.5, carrier
  3), or whether that belongs in a separate small "Mascope relay" service on
  the instrument PC with a narrower remit and its own installer.
- **Retention on the server.** Keeping N-1 images is a floor; whether to keep
  more, and whether `applied/` bundle metadata is pruned, follows from the
  disk measurements.
- **Customer acceptance of a third-party control plane** for section 4. The
  pilot will tell whether the Tailscale form is enough or the reverse-SSH form
  is needed as a standard offer.
- **The `mascope-update.service` documentation drift.** The unit's comment
  mentions a release token that `update.env.example` correctly says no longer
  exists; tidy it when the unit changes for the post-run reinstall.
