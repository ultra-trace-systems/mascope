- **A restored database is readable by the fleet's read-only role again.**
  `mascope prod db restore` restores without privileges, so the role
  `mascope_readonly` lost its grants in the restored database, and read-only
  scripts failed with `permission denied` until the fleet role ran again.
  The restore, `mascope prod db create` and a `mascope env sync` into a local
  stack now grant the role read access to the database when the cluster has
  the role, and exit non-zero if that fails.
