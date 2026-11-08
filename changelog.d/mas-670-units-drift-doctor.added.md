- **`mascope prod doctor` says when the systemd units are out of date.** An
  update moves the server to a newer release but leaves the installed units
  and the scripts copied to `/usr/local/lib/mascope` as the release that
  installed them wrote them. The doctor's Units row now compares each of them
  with what `mascope prod units install` would write now, names every one that
  differs or is missing as `OUTDATED`, and says to re-run `units install`. The
  exit code does not change: the units still run. The `units install` help no
  longer says it leaves the update timer disabled: it never changes the
  timer's state.
