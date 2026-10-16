- **The disk monitor warns by how fast a disk is filling, not only by how
  full it is.** A fixed free-space floor gives a disk that fills fast little
  lead time: at 19 GiB a day, the 10% floor of a 1 TiB disk is crossed about
  five days before it is full, and the doctor's 5 GiB floor hours before. The
  disk-check timer (`tooling/disk-check.sh`) now appends each run's free space
  to `.runtime/disk-check/history.tsv`, kept to 60 days, fits how many GiB a
  day each filesystem has been filling over the last 7 and 30 days, and logs
  the rates, when the floor would be reached and when the disk would be full.
  It warns when, at the faster of the two rates, the floor is less than
  `MIN_DAYS_TO_FLOOR` days away (default 30; `0` disables), on a healthcheck
  of its own (`HEALTHCHECK_FILLING_URL`), so `HEALTHCHECK_URL` still goes down
  only below the floor and crossing it still notifies; without the second
  check the warning is journalled and logged to the first. An env's filestore
  that is a symlink to a volume of its own is now measured as well - before,
  only the filesystem holding `MASCOPE_PATH` was. A threshold that is not a
  plain number falls back to its default instead of being compared as text.
  `mascope prod doctor` reads the same history: its Disk rows, one per
  filesystem and now including the filestore, show the rates and the days
  until full, the JSON carries them, and a filesystem projected below the
  doctor's floor within `MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR` days (default 7) is
  flagged `FILLING` and makes it exit 3, a warning: the fleet playbooks let a
  rollout or reboot window carry on past it, and the Uptime Kuma push
  (`doctor-push.sh`) reports it as down, with the filesystem nearest to full,
  its rate and its days until full in the message. Rates appear after a day of
  history (`docs/maintaining.md`, "Disk space").
