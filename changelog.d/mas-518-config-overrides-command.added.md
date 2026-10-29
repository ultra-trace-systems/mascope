- **`mascope prod config` shows which settings a deployment overrides.**
  It lists every config setting whose value differs from the model's
  default, with the layer that set it: the release's `base.mascope.toml`,
  `prod.mascope.toml`, or the env's own toml. It reads the layers inside the
  backend container, so it shows the image's files, and warns when the host's
  copies differ. A key the config does not know is flagged, credentials are
  never printed, and `--all` prints the whole effective config.
  `mascope dev config` does the same for a development env.
