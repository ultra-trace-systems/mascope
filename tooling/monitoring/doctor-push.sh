#!/bin/bash
# Report Mascope stack health to Uptime Kuma (push monitor). Cron: every 30 min.
# Prefers `mascope prod doctor` (stack/disk/backups/migrations); falls back to
# direct container-health on releases without it. The msg carries disk usage
# and, once the disk-check timer has a day of history, how fast the state
# filesystem is filling and when it would be full; ping= carries disk-used% so
# Kuma's chart doubles as a disk trend graph.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
[ -r /etc/environment ] && { set -a; . /etc/environment; set +a; }
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/mascope" 2>/dev/null || true

TOKEN="__TOKEN__"
# Uptime Kuma as reachable from this server, e.g. http://<monitoring-tailnet-ip>:3001
KUMA_URL="__KUMA_URL__"
BASE="${KUMA_URL}/api/push/${TOKEN}"

disk=$(df -P "$HOME/mascope" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
dockerdisk=$(df -P /var/lib/docker 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')
[ -n "$disk" ] || disk=0
nums="disk${disk}pct_docker${dockerdisk:-?}pct"

status=up
report=$(mascope prod doctor --json 2>/dev/null); rc=$?

# The state filesystem's faster fill rate and days until full, e.g.
# "_fill19.0GiBperday_full34d". Empty without a rate yet, or on a CLI whose
# doctor predates rates. No "+" sign: in a query string it reads as a space.
trend=$(printf '%s' "$report" | python3 -c '
import json, sys
try:
    disk = next(d for d in json.load(sys.stdin)["disks"] if d["label"] == "state")
    rate = max(r["gb_per_day"] for r in disk["rates"])
except Exception:
    sys.exit(0)
full = disk.get("days_to_full")
print(f"_fill{rate:.1f}GiBperday" + ("" if full is None else f"_full{full:.0f}d"))
' 2>/dev/null)
nums="${nums}${trend}"

msg="OK_${nums}"
if [ "$rc" -eq 0 ]; then
  :
elif [ "$rc" -eq 1 ]; then
  status=down; msg="doctor_unhealthy_${nums}"
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
