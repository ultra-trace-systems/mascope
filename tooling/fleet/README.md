# Fleet configuration (Ansible)

Codifies the host-level configuration a Mascope production fleet depends on,
so drift becomes a weekly diff instead of a months-later incident:

| Role | Owns |
|---|---|
| `sshd_hardening` | `/etc/ssh/sshd_config.d/00-tailnet-hardening.conf` (key-only SSH, no root passwords) |
| `firewall` | ufw policies, tailnet SSH rule, 443 from Cloudflare's ranges (ufw only, see below), the canonical `MASCOPE NAT` masquerade block |
| `docker_daemon` | `/etc/docker/daemon.json` with `iptables: false` (load-bearing: with stock Docker, published ports bypass ufw) |
| `unattended_upgrades` | unattended security updates enabled |
| `deploy_env` | `MASCOPE_PATH` in `/etc/environment` (load-bearing: it is how the CLI finds the checkout, and so which release a unit starts) |
| `postgres_readonly` | the `mascope_readonly` Postgres role in `mascope_prod_postgres`: SELECT only, read-only by default, its password in `.runtime/secrets/` (see [Read-only database access](#read-only-database-access)) |

The monitoring box is deliberately **not** in the fleet group — it
runs a different network model (stock Docker + `DOCKER-USER` rules).

**The tailnet bypasses ufw.** Tailscale installs its own netfilter chain,
`ts-input`, jumped from the top of `INPUT`, and it accepts everything arriving
on `tailscale0` ahead of ufw's rules. So ufw's "443 from Cloudflare only" holds
for the internet, but every tailnet peer the Tailscale ACL lets through reaches
443, and every other port, whatever ufw says: a request from a tailnet peer
to a fleet server's 443 is answered. This matters to the frontend, which trusts
`CF-Connecting-IP` relayed through the Docker gateway (the `cloudflare-realip`
block in `server/frontend/nginx.conf`): a tailnet peer can send that header and
choose its own rate-limit bucket. The Tailscale ACL, not ufw, decides who can.

## One-time setup (WSL on the admin workstation)

```sh
# in WSL (Ubuntu):
sudo apt update && sudo apt install -y pipx && pipx install --include-deps ansible
# the SSH key must live inside WSL with sane permissions:
mkdir -p ~/.ssh && cp /mnt/c/Users/<you>/.ssh/id_ed25519_mascope ~/.ssh/ && chmod 600 ~/.ssh/id_ed25519_mascope
```

Create your inventory (deliberately not committed — this repo is public and
the tailnet addresses stay out of it):

```sh
cp inventory.example.yml inventory.local.yml
# fill in each server's tailnet IP (see the private fleet docs, or
# `tailscale status` on any tailnet machine)
```

## Log access from the workstation: `mascope fleet`

The inventory doubles as the server list for the CLI's fleet commands — no
Ansible required, so they run natively on Windows (Ansible itself stays in
WSL):

```sh
mascope fleet list                                        # servers from the inventory
mascope fleet logs <host> -l error --interval '1 day'     # remote `mascope logs query`
mascope fleet logs <host> -m 50 --json                    # NDJSON for scripts/agents
```

`fleet logs` SSHes to the server over the tailnet (using the inventory's
user/key) and runs `mascope logs query --prod` there, passing every extra flag
through — so agents can pull filtered production logs with one command and no
private fleet knowledge in their prompts.

The CLI looks for `tooling/fleet/inventory.local.yml` in its own checkout
first, then under `$MASCOPE_PATH` (so agent worktrees resolve the main
checkout's copy), or wherever `MASCOPE_FLEET_INVENTORY` points. If your
inventory lives only inside WSL, drop a copy into the main checkout — it is
gitignored there too.

## Sudo passwords: the vault (recommended)

The fleet model assumes **no NOPASSWD sudo** and a per-server sudo
password, so a single `-K` prompt cannot drive a whole-fleet run. Ansible Vault
solves this: store the per-host passwords once in an encrypted file, then unlock
them all with one prompt.

```sh
# Create the encrypted vault (you set a vault password; then paste each
# server's sudo password (the deploy user) from your password manager).
# Structure is in vault.local.example.yml.
ansible-vault create vault.local.yml
# Later edits:
ansible-vault edit vault.local.yml
```

The real `vault.local.yml` is **gitignored** — never commit it, even encrypted
(this repo is public). Keep a copy of the vault password in your password
manager; losing it means recreating the vault, not a lockout (the servers are
unchanged).

The vault lives **next to the playbooks, not under `group_vars/`**, and is
loaded explicitly by `site.yml`. This is deliberate: `group_vars` files are
auto-decrypted for *every* play, which would force a vault prompt even on
sudo-less playbooks like `update.yml`.

## Workflow: check first, apply deliberately

**Drift check** (read-only, safe anytime). With the vault, one vault-password
prompt covers the whole fleet:

```sh
ansible-playbook site.yml --check --diff --ask-vault-pass
```

**Apply** — always canary-first, then the rest:

```sh
ansible-playbook site.yml --ask-vault-pass --limit <canary-host>   # one server first
ansible-playbook site.yml --ask-vault-pass                         # fleet
```

*No vault?* Drop `--ask-vault-pass`, add `-K`, and always `--limit <host>` so
the single sudo prompt matches exactly one server:

```sh
ansible-playbook site.yml --check --diff -K --limit <host>
```

## Apply-time cautions

- The `docker_daemon` role's restart handler **restarts Docker = restarts the
  Mascope stack** (~30 s outage on that server). It only fires when
  `daemon.json` actually changed, which should be never once converged — but
  treat a non-empty diff there with respect and apply per-server.
- The first-ever run on a long-lived server is a **migration**, not a no-op:
  a server provisioned before this role existed may carry a hand-written
  `MASCOPE NAT` block (removed and replaced by the ansible-managed block, same
  semantics) or persist equivalent NAT rules via `iptables-persistent` —
  harmless duplication that can be retired separately.
- Cloudflare ranges are fetched live at run time; rules for ranges Cloudflare
  has *withdrawn* are not auto-pruned (same behavior as
  `tooling/ufw-allow-cf.sh`) — prune manually on the rare CF delisting.

## Read-only database access

`postgres_readonly` gives every server a Postgres role, `mascope_readonly`,
for work that reads production without the app's own credentials: fleet
audits, and prod jobs (a reviewed read-only script whose output is checked
before anyone else sees it).

**What the role may do.** It logs in, with a connection limit of 4, and is
`NOSUPERUSER NOINHERIT NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS`,
a member of no other role. `default_transaction_read_only = on` is set on it.
In every app database (`mascope_<env>`) it has USAGE on the schemas and
SELECT on every table, view and materialized view, and default privileges on
the app's role (`mascope_user`, which runs the migrations) make the tables and
schemas created later readable too. Nothing else is granted to it, and a run
takes away anything else it finds granted: write privileges on tables, USAGE
on sequences, CREATE on a schema or database, other default privileges. What
PUBLIC holds by default (CONNECT and TEMPORARY on a database, EXECUTE on
functions) it keeps. A write privilege it would hold through PUBLIC, such as
CREATE on the `public` schema in a cluster first initialised before
PostgreSQL 15, makes the role fail on that server rather than leave it
writable: revoking that from PUBLIC is an operator's call.

`default_transaction_read_only` is a default a session can turn off; the
privileges are what make the role read-only.

**The password** is `.runtime/secrets/postgres_readonly_password.txt` in the
deployment checkout, mode 0600 and the deploy user's, made on the first run.
Postgres is given only its SCRAM verifier, and each run checks the role's
verifier against the file, so a run with no drift changes nothing. It is not a
compose secret: no container mounts it and the app never reads it. To rotate
it, delete the file and run the role again.

**How a job connects.** A job runs in the backend container, which reaches the
database as host `postgres` on the compose network (the port is not
published). Whoever runs the job reads the password on the server and hands it
to `docker exec` by name, never on a command line, with libpq's environment:

```sh
cd "$MASCOPE_PATH"
PGPASSWORD=$(head -n 1 .runtime/secrets/postgres_readonly_password.txt) \
  docker exec -i -e PGHOST=postgres -e PGUSER=mascope_readonly -e PGPASSWORD \
    -e PGOPTIONS='-c default_transaction_read_only=on' \
    mascope_prod_backend /opt/uv/tools/mascope/bin/python - < job.py
```

and the job connects with `psycopg2.connect(dbname=os.environ["MASCOPE_DB_NAME"])`,
which takes the rest from that environment. The backend container already has
`MASCOPE_DB_NAME`. Peer or trust authentication cannot do this instead: the
backend and the database are separate containers, the image's `pg_hba.conf`
trusts only its own socket and loopback, and a trust rule for the compose
network would let every container on it log in as the role with no password.
A job must not read `/run/secrets/postgres_password` or use the app's own
engine, which connect as the app's role.

**Running it.** It is part of `site.yml`, so the usual check and apply cover
it:

```sh
ansible-playbook site.yml --check --diff --ask-vault-pass --limit <canary-host>
ansible-playbook site.yml --ask-vault-pass --limit <canary-host>
```

In check mode the role lists what it would change. Once there is nothing to
change, check and apply runs both end by connecting as the role from
`mascope_prod_backend`, as a job does, and checking the session is read-only;
a stopped backend skips that check with a note.

**After a restore.** `mascope prod db restore` restores without privileges
(`pg_restore --no-acl`), and a new env starts a new database: either leaves the
role without its grants and default privileges there. Run the role again
afterwards. The role itself is cluster-wide and survives both.

## Rolling out a release

`site.yml` owns host *configuration*; `update.yml` performs the recurring
*operation* of deploying a release — one server at a time, verifying each with
`mascope prod doctor` and stopping the rollout on the first failure. It also
reinstalls the `mascope` CLI, before the stack update, so it cannot drift
behind the checkout - and so a release that adds a compose secret is
provisioned by the CLI that knows about it. The doctor's exit 3 - a healthy
stack whose disk is projected to reach its free-space floor within days - is
a warning, not a failure: the play prints a note for that server and carries
on, since a filling disk needs an operator rather than a fleet stuck on the old
release. No sudo (and therefore no vault password) is needed:

```sh
ansible-playbook update.yml -e mascope_version=vX.Y.Z --limit <canary-host>
ansible-playbook update.yml -e mascope_version=vX.Y.Z    # rest of the fleet
```

Servers already on the release — checkout *and* running stack — are skipped
with a one-line verdict, so a rollout that failed partway through the fleet
can simply be re-run without restarting the servers that finished. Pass
`-e force=true` to redeploy such a server anyway.

The manual per-server equivalent (and its verification checklist) is in
`docs/maintaining.md` → "Rolling out a release across several servers".

## Rebooting the fleet

Unattended security upgrades install kernel and library updates, but they do
not take effect until a reboot — and nothing alerts on that, so servers drift
quietly onto patched-but-not-running code. `reboot.yml` performs the reboot as
a deliberate, verified operation rather than a scheduled one: an unattended
reboot that fails to bring the stack back would leave an instance down until
somebody noticed.

```sh
ansible-playbook reboot.yml --limit <canary-host> --ask-vault-pass
ansible-playbook reboot.yml --limit <host>,<host> --ask-vault-pass
ansible-playbook reboot.yml --ask-vault-pass    # every server with one pending
```

Per server it skips anything without `/var/run/reboot-required`, refuses to
proceed while a backup is in flight, reboots, then gates on four checks: the
backend container reports healthy, the running release is the same one the
server went down with, the origin API answers `422`, and `mascope prod doctor`
passes (its disk-filling warning, exit 3, passes with a note, as in
`update.yml`). Any failure stops the batch before the next server is touched. The
transcript states a one-line verdict per server - naming the packages that
requested the reboot - and, after the reboot, the kernel change it activated,
so a justified reboot is distinguishable from a skip at a glance.

Two things worth knowing before scheduling a window:

- **Avoid each server's nightly backup slot.** The playbook refuses to reboot
  while a backup runs, but starting shortly before one still collides; a killed
  restic push wastes that night's dump. Slots are staggered overnight — see the
  private fleet docs for the per-server times.
- **Probe the origin, not the public URL.** Behind the Cloudflare IP gate, a
  request from one server to another's public URL returns 403 regardless of
  health. The playbook resolves the app host to `127.0.0.1` for this reason.

Sudo is needed for the reboot itself, so unlike `update.yml` this one does
require the vault password.

## Suggested cadence

Weekly `--check --diff` (eyeball the diff, expect empty), plus a check run
before and after any manual server surgery. A cron wrapper that alerts on
non-empty diff can come later once the fleet has converged.

Monthly, `reboot.yml` to activate the kernel updates unattended-upgrades has
already installed — canary first, outside the backup slots.
