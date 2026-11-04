- **Stopping the boot service no longer times out.** Stopping
  `mascope.service` (a reboot, a shutdown, `mascope prod units remove`) took
  exactly 90 s and ended in a timeout. The backend, Postgres and Redis were
  then never stopped by it. The file converter ignored the stop signal: it
  kept taking files until Docker killed it at the end of its grace period,
  cutting off any conversion it was running. It now stops within a few
  seconds when idle, and finishes a conversion in flight before it exits,
  given up to its 90 s stop grace period; Docker still kills one that takes
  longer. The
  unit also waits up to 300 s for `prod down`, longer than the stack's stop
  grace periods add up to. Re-run `mascope prod units install` (or
  `tooling/ubuntu.sh install`) to pick up the new unit (#2903).
