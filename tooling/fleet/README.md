# Fleet configuration (Ansible)

Codifies the host-level configuration a Mascope production fleet depends on,
so drift becomes a weekly diff instead of a months-later incident:

| Role | Owns |
|---|---|
| `sshd_hardening` | `/etc/ssh/sshd_config.d/00-tailnet-hardening.conf` (key-only SSH, no root passwords) |
| `firewall` | ufw policies, tailnet SSH rule, Cloudflare-only 443, the canonical `MASCOPE NAT` masquerade block |
| `docker_daemon` | `/etc/docker/daemon.json` with `iptables: false` (load-bearing: with stock Docker, published ports bypass ufw) |
| `unattended_upgrades` | unattended security updates enabled |

The monitoring box is deliberately **not** in the fleet group — it
runs a different network model (stock Docker + `DOCKER-USER` rules).

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

## Rolling out a release

`site.yml` owns host *configuration*; `update.yml` performs the recurring
*operation* of deploying a release — one server at a time, verifying each with
`mascope prod doctor` and stopping the rollout on the first failure. It also
reinstalls the `mascope` CLI so it cannot drift behind the checkout. No sudo
(and therefore no vault password) is needed:

```sh
ansible-playbook update.yml -e mascope_version=vX.Y.Z --limit <canary-host>
ansible-playbook update.yml -e mascope_version=vX.Y.Z    # rest of the fleet
```

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
passes. Any failure stops the batch before the next server is touched. The
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
