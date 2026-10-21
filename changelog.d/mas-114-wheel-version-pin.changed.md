- **A wheel install says when an update's `--version` will not last.**
  Without a source checkout at `MASCOPE_PATH`, `prod update --version vX.Y.Z`
  pins the release for that command only, so the next `prod up` deploys
  `latest` again. The update now ends with a warning saying so and how to keep
  the release: set `MASCOPE_VERSION` in the environment the stack is started
  from. The CLI's README gains a "Limits of a wheel install" section listing
  this and the other gaps a wheel install still has next to a checkout.
