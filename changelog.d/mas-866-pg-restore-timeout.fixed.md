- **A database restore no longer gives up after ten minutes.** Restoring a
  large dump with `mascope prod db restore`, `mascope dev db restore`,
  `mascope env sync` or the demo seed runs for as long as it takes, where it
  used to end in a traceback after ten minutes while `pg_restore` went on
  inside the Postgres container. A restore interrupted with Ctrl-C now stops
  `pg_restore` in the container and says the database is incomplete.
  `mascope prod db restore --jobs N` restores with `N` parallel jobs.
