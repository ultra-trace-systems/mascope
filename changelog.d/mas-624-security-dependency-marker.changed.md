- **The security-fix distance check counts dependency security updates.**
  A commit whose subject ends in `[security]`, as Renovate's vulnerability
  updates do, now counts as a security fix when it changes the version of a
  package a deployment runs: one the root project reaches in `uv.lock`, or
  a production package of the frontend's `package-lock.json`. A bump of
  development tooling alone counts as none. The rule is in
  `tooling/monitoring/README.md`, section 11.
