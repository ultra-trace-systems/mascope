"""
The disk fill-rate suffix of doctor-push.sh's Uptime Kuma message.

Reads `mascope prod doctor --json` on stdin and prints, for the filesystem
nearest to full, its label, its faster fill rate and the days until it is
full, e.g. "_filestore_fill19.0GiBperday_full21d". Prints nothing when no
filesystem has a rate yet, the input is not a doctor report, or the doctor
predates fill rates - the message then simply goes without one.

Runs on the host's python3 (standard library only, any 3.x a supported Ubuntu
ships), not the CLI's interpreter. The CLI tests import it and feed it the
doctor's own JSON, so a rename on the doctor's side fails a test rather than
quietly dropping the suffix from every server's heartbeat.
"""

import json
import sys


def summarize(report_json):
    """The message suffix for a doctor JSON report, or "" when there is none."""
    try:
        disks = json.loads(report_json)["disks"]
    except (ValueError, TypeError, KeyError):
        return ""
    with_rates = [d for d in disks if isinstance(d, dict) and d.get("rates")]
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


if __name__ == "__main__":
    sys.stdout.write(summarize(sys.stdin.read()))
