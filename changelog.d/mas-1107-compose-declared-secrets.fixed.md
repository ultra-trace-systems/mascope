- **A missing secret file no longer leaves a server without its database.**
  An update or a `mascope prod up` used to let compose stop half-way when a
  secret file the compose file declares was missing: Postgres and Redis were
  replaced and never started. This happened when the installed CLI was older
  than the compose file. The CLI now checks every declared secret file before
  any container is touched. It makes a missing one the compose file's new
  `x-mascope-secrets` block says how to make, so a CLI older than the compose
  file still makes a secret a newer release added. Any other missing file
  stops the start with the stack untouched, and an unattended update reports
  it without holding the release (docs/maintaining.md, "Unattended updates").
