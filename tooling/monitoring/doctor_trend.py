"""
The disk part of doctor-push.sh's Uptime Kuma heartbeats.

Reads `mascope prod doctor --json` on stdin. By default it prints, for the
filesystem nearest to full, its label, its faster fill rate and the days until
it is full, e.g. "_filestore_fill19.0GiBperday_full21d". It prints nothing when
no filesystem has a rate yet, the input is not a doctor report, or the doctor
predates fill rates - the message then simply goes without one.

With --warning it prints nothing and exits 0 when the disk warning monitor
should be down - a filesystem is filling toward its floor or already below it -
and 1 otherwise. The doctor's own exit code cannot say this: an unhealthy stack
exits 1 whether or not a disk is also filling.

Runs on the host's python3 (standard library only, any 3.x a supported Ubuntu
ships), not the CLI's interpreter. The CLI tests import it and feed it the
doctor's own JSON, so a rename on the doctor's side fails a test rather than
quietly dropping the suffix from every server's heartbeat.
"""

import json
import sys


def _disks(report_json):
    """The report's disks, or [] when the input is not a doctor report."""
    try:
        disks = json.loads(report_json)["disks"]
    except (ValueError, TypeError, KeyError):
        return []
    if not isinstance(disks, list):
        return []
    return [d for d in disks if isinstance(d, dict)]


def summarize(report_json):
    """The message suffix for a doctor JSON report, or "" when there is none."""
    with_rates = [d for d in _disks(report_json) if d.get("rates")]
    if not with_rates:
        return ""

    def days_left(disk):
        full = disk.get("days_to_full")
        return float("inf") if full is None else full

    # The filesystem that fills first is the one worth a heartbeat; on a
    # shared filesystem every label carries the same numbers and the first wins.
    try:
        disk = min(with_rates, key=days_left)
        rate = max(r["gb_per_day"] for r in disk["rates"])
        # No "+" sign: in a query string it reads as a space.
        suffix = "_{}_fill{:.1f}GiBperday".format(disk["label"], rate)
        if disk.get("days_to_full") is not None:
            suffix += "_full{:.0f}d".format(disk["days_to_full"])
    except (KeyError, TypeError, ValueError):
        return ""
    return suffix


def disk_warning(report_json):
    """
    Whether a filesystem is projected below its floor within the doctor's
    horizon, or is below it already.

    Below the floor counts too: the doctor stops flagging a disk as filling once
    it is low, and a warning monitor that went up at that moment would read as
    "recovered" just as the disk ran out.
    """
    return any(d.get("filling") or d.get("low") for d in _disks(report_json))


if __name__ == "__main__":
    report = sys.stdin.read()
    if sys.argv[1:] == ["--warning"]:
        sys.exit(0 if disk_warning(report) else 1)
    sys.stdout.write(summarize(report))
