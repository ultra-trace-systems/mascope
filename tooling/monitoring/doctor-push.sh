#!/bin/bash
# Report Mascope stack health to Uptime Kuma (push monitor). Cron: every 30 min.
# Prefers `mascope prod doctor` (stack/disk/backups/migrations); falls back to
# direct container-health on releases without it. The msg carries disk usage
# and, once the disk-check timer has a day of history, the filesystem nearest
# to full with its fill rate and days until full; ping= carries disk-used% so
# Kuma's chart doubles as a disk trend graph.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
[ -r /etc/environment ] && { set -a; . /etc/environment; set +a; }
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/mascope" 2>/dev/null || true

TOKEN="__TOKEN__"
# Uptime Kuma as reachable from this server, e.g. http://<monitoring-tailnet-ip>:3001
KUMA_URL="__KUMA_URL__"
BASE="${KUMA_URL}/api/push/${TOKEN}"
# Optional: the token of a second Push monitor for the doctor's disk warning,
# down while a filesystem is filling toward its floor (doctor exit 3) or below
# it. A monitor of its own, because Kuma notifies on a change of state: sharing
# the first one, a server already down for the warning would report a later
# failure - a stopped container, version drift - to no one. Leave it empty and
# the warning only shows in the first monitor's messages.
FILLING_TOKEN=""

disk=$(df -P "$HOME/mascope" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
dockerdisk=$(df -P /var/lib/docker 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
[ -n "$disk" ] || disk=0
nums="disk${disk}pct_docker${dockerdisk:-?}pct"

status=up
report=$(mascope prod doctor --json 2>/dev/null); rc=$?

# e.g. "_filestore_fill19.0GiBperday_full21d", read out of the doctor's JSON by
# a helper in the checkout. Empty without a rate yet, without python3, or on a
# checkout or CLI that predates fill rates.
helper="${MASCOPE_PATH:-$HOME/mascope}/tooling/monitoring/doctor_trend.py"
trend=$(printf '%s' "$report" | python3 "$helper" 2>/dev/null)
nums="${nums}${trend}"

msg="OK_${nums}"
if [ "$rc" -eq 0 ]; then
  :
elif [ "$rc" -eq 1 ]; then
  status=down; msg="doctor_unhealthy_${nums}"
elif [ "$rc" -eq 3 ]; then
  # Healthy, but a filesystem is projected below its floor within the doctor's
  # horizon (MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR, default 7 days). Up here, so a
  # later failure still turns this monitor down; FILLING_TOKEN's monitor pages.
  msg="doctor_disk_filling_${nums}"
else
  # rc 2 => no `doctor` in this CLI; fall back to container health.
  bad=$(docker ps --filter name=mascope_prod_ --format '{{.Names}} {{.Status}}' \
        | grep -iE 'unhealthy|restarting|exited|dead' || true)
  running=$(docker ps --filter name=mascope_prod_ -q | wc -l)
  if [ -n "$bad" ] || [ "$running" -lt 3 ]; then
    status=down; msg="containers_${running}_running_${nums}"
  fi
fi
curl -fsS -m 10 "${BASE}?status=${status}&msg=${msg}&ping=${disk}" >/dev/null 2>&1

if [ -n "$FILLING_TOKEN" ]; then
  # Asked of the JSON, not the exit code: an unhealthy stack exits 1 whether or
  # not a disk is also filling. Exit 3 is the answer when the helper cannot run.
  if [ "$rc" -eq 3 ] || printf '%s' "$report" | python3 "$helper" --warning 2>/dev/null; then
    fstatus=down; fmsg="disk_filling_${nums}"
  else
    fstatus=up; fmsg="OK_${nums}"
  fi
  curl -fsS -m 10 "${KUMA_URL}/api/push/${FILLING_TOKEN}?status=${fstatus}&msg=${fmsg}&ping=${disk}" >/dev/null 2>&1
fi
