- **The update timer no longer re-applies a release that failed its health
  check.** After an unattended apply failed, the next maintenance window
  applied the same release again, undoing the operator's manual rollback and,
  for a migration update, migrating the restored database again, every night.
  `--auto` now records the failure, whether the migration failed or the
  backend never turned healthy, and holds that release: each run exits 2
  without applying it, and `prod doctor` reports it as failed.
  `--confirm` retries it once; `--snooze <days>` keeps it held but quiet. A
  newer release is tried as usual.
