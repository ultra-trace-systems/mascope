- **A wheel install records the release it runs.** A runtime home without a
  source checkout records the release in `.runtime/deployed-release.json`
  once an update, an unattended `--auto` run or a pinned `prod up --detach`
  brings the stack up healthy. A checkout install still goes by its
  checked-out tag.
