- **`mascope prod doctor` checks the scripts the systemd units run.** A new
  **Units** row reads the disk check's and the update unit's `ExecStart`. A
  unit whose script is gone is `MISSING` and makes the doctor exit 1. A unit
  that runs its script from inside a Python environment, as a hand-written or
  pre-release unit might, is flagged `STALE`: a reinstall of the CLI can move
  that path. Re-running `mascope prod units install` mends both.
