- **`mascope prod units install` puts the systemd units in place.** A wheel
  install now gets the same boot, update, disk-check and retention units a
  checkout gets from `tooling/ubuntu.sh`, which now calls the command itself.
  `--dry-run` prints every file and command first, and `mascope prod units
  remove` takes the units out again. On a wheel install, the update unit
  completes the hand-over `--auto` asks for with exit 40: it installs the
  newer CLI, then runs the update again as a new process.
