- **`mascope demo` on a wheel install points at the demo stack instead of
  crashing.** The bare command, `snapshot` and `verify` drive the source tree,
  so a CLI installed from PyPI now offers only `demo fetch` and `demo info`.
  `mascope demo` there exits with the `docker compose` command that starts the
  demo stack `mascope init` wrote, rather than a `psycopg2` import error.
