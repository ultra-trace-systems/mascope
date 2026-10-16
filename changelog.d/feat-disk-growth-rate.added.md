- **The disk monitor warns by how fast a disk is filling, not only by how
  full it is.** A fixed free-space floor gives a disk that fills fast little
  lead time: at 19 GiB a day, the 10% floor of a 1 TiB disk is crossed about
  five days before it is full, and the doctor's 5 GiB floor hours before. The
  disk-check timer (`tooling/disk-check.sh`) now appends each run's free space
  to `.runtime/disk-check/history.tsv`, kept to 60 days, fits how many GiB a
  day each filesystem has been filling over the last 7 and 30 days, and logs
  the rates, when the floor would be reached and when the disk would be full.
  It alerts through the same healthcheck URL when, at the faster of the two
  rates, the floor is less than `MIN_DAYS_TO_FLOOR` days away (default 30;
  `0` disables). `mascope prod doctor` reads the same history: its Disk rows
  show the rates and the days until full, the JSON carries them, and a
  filesystem projected below the doctor's floor within
  `MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR` days (default 7) is flagged `FILLING` and
  makes it exit 1. The Uptime Kuma push (`doctor-push.sh`) adds the rate and
  the days until full to its message. Rates appear after a day of history
  (`docs/maintaining.md`, "Disk space").
