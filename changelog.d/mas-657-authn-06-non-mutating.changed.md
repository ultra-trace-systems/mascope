- **The scheduled production security run now checks Turnstile.** `AUTHN-06`,
  which checks that a site with Turnstile on refuses a sign-in with no token,
  is now a non-mutating control, so the `--non-mutating` production run
  assesses it. Until now no scheduled run did: staging runs with Turnstile
  off. It is the one failed login the non-mutating set sends, and only on a
  site with Turnstile on: one request of the per-address login budget per
  run, for an account that does not exist.
