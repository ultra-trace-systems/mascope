- **A wheel install keeps the release it was updated to.** A runtime home
  without a source checkout records the release in
  `.runtime/deployed-release.json` once an update, an unattended `--auto` run
  or a pinned `prod up --detach` brings the stack up healthy. The next
  `mascope prod up`, a reboot included, deploys that release again instead of
  the rolling `latest` images. `prod doctor` reports DRIFT against it, and
  `--auto` never moves the stack back past it. `--version latest` removes the
  record. A checkout install still goes by its checked-out tag.
