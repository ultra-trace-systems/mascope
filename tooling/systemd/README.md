# Mascope systemd units

The systemd units that run a Mascope deployment. `tooling/ubuntu.sh install`
templates and installs all of them (filling in the deploy user and the resolved
`mascope` binary); you normally don't touch them by hand. For the full
operations story see [docs/maintaining.md](../../docs/maintaining.md).

| File | Installed as | Enabled by `ubuntu.sh`? | Purpose |
|---|---|---|---|
| `mascope.service` | `mascope.service` | yes | Bring the stack up on boot (`prod up --detach`) / down on stop. |
| `mascope-update.service` | `mascope-update.service` | no (oneshot, run by the timer) | One unattended update pass (`prod update --auto`). |
| `mascope-update.timer` | `mascope-update.timer` | **no - opt-in** | Fire the update service nightly. |
| `update.env.example` | `/etc/mascope/update.env` (chmod 600) | seeded once | Update window / grace / release token. |
| `mascope-disk-check.service` | `mascope-disk-check.service` | no (oneshot, run by the timer) | One disk-space check (`tooling/disk-check.sh`). |
| `mascope-disk-check.timer` | `mascope-disk-check.timer` | **yes - on by default** | Fire the disk check every 15 minutes. |
| (see `tooling/disk-check.env.example`) | `/etc/mascope/disk-check.env` (chmod 600) | seeded once | Disk thresholds + optional alert URL. |

Both `.service` files template `@@USER@@`, `@@MASCOPE_BIN@@` and
`@@MASCOPE_PATH@@`; `MASCOPE_PATH` and `LD_PRELOAD` come from
`/etc/environment`, matching how `ubuntu.sh` provisions the box.

`@@MASCOPE_PATH@@` fills the `WorkingDirectory`, which is not cosmetic: systemd
otherwise starts a unit in `/`, and a deploy launched outside the checkout
cannot read which release tag is checked out - it would deploy the rolling
`latest` images instead. After changing any unit here, re-run
`./tooling/ubuntu.sh install` on the server; a release update does not rewrite
the installed units.

## Enabling auto-updates

Auto-updates are installed **disabled** so a fresh server stays quiet until you
opt in. No credentials are needed - release discovery uses the public GitHub API
over HTTPS. Just enable the timer, optionally adjusting the window/grace first:

```sh
sudoedit /etc/mascope/update.env          # optional: window / grace
sudo systemctl enable --now mascope-update.timer
```

`--auto` tracks the newest GitHub release tag (`vX.Y.Z`) automatically.

## What each `--auto` run does

- **Up to date** - nothing.
- **Fast update** (new images, no migration) - applied inside the maintenance
  window (`MASCOPE_UPDATE_WINDOW`), then health-checked. A failed health check
  alerts and stops; it never rolls back automatically.
- **Migration update** (downtime) - recorded and reported (exit 30). Applied at
  the next window once the grace period elapses (`MASCOPE_UPDATE_GRACE_DAYS`,
  default 7) or an operator confirms it, unless snoozed.

Steer a pending migration update:

```sh
mascope prod update --check        # classify the pending update, apply nothing
mascope prod update --confirm      # apply at the next window (skip the grace wait)
mascope prod update --snooze 7     # postpone it 7 days
```

## The disk-space monitor

A full disk is the one failure that takes the stack down *and* can corrupt
Postgres on the way - so unlike auto-updates, the disk check is **enabled by
default**. Every 15 minutes (cheap, read-only) `mascope-disk-check.timer` runs
`tooling/disk-check.sh`, which alerts when any monitored filesystem drops below
**either** threshold:

- `MIN_FREE_GB` (default 10) - absolute floor, the "about to crash" signal;
- `MIN_FREE_PCT` (default 10) - percentage floor, the earlier warning (set `0`
  to disable on very large disks).

With no configuration it only logs to the journal; set `HEALTHCHECK_URL` in
`/etc/mascope/disk-check.env` to get paged before the disk fills. The service
treats exit 1 ("a filesystem is low") as expected, so systemd does not pile a
`failed` unit on top of an alert the operator is already handling. Full
operator docs: [docs/maintaining.md](../../docs/maintaining.md) -> Disk space.

## Inspecting

```sh
systemctl list-timers mascope-update.timer mascope-disk-check.timer
journalctl -u mascope-update.service
journalctl -u mascope-disk-check.service         # disk check history
cat "$MASCOPE_PATH/.runtime/update/status.log"   # applied / pending history
cat "$MASCOPE_PATH/.runtime/update/state.json"   # the current pending update
```
