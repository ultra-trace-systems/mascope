- **`prod update` messages name a held release's kind.** The settle message
  after a manual deploy, the `--confirm` and `--snooze` help and their
  "nothing pending" warnings no longer call every pending record a migration
  update. `--help` now says `--confirm` retries a release held after a failed
  apply, and a confirm says it also lifts the hold on a release fixed in place.
