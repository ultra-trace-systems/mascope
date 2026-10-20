- **A read-only database role on every fleet server.** The fleet's Ansible
  (`tooling/fleet/`) has a new role, `postgres_readonly`, that keeps a
  `mascope_readonly` login in each server's Postgres: SELECT on every table
  and view of the app databases, with default privileges for the tables later
  migrations add, `default_transaction_read_only = on`, and nothing else. Its
  password is made on the first run in the checkout's `.runtime/secrets/`,
  and `--check --diff` shows any drift. Audits and other read-only scripts
  connect as it from the backend container instead of with the app's own
  credentials; the fleet README's "Read-only database access" says how.
