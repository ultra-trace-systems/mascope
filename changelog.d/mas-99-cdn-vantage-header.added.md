- **The security suite can run through the CDN as well as at the origin.**
  `MASCOPE_PENTEST_HEADER_NAME` and `MASCOPE_PENTEST_HEADER_VALUE` add an
  operator header to every request the suite makes, for a WAF skip rule keyed
  on it; the value never appears in the report or the console output. Each
  report records its vantage (`MASCOPE_PENTEST_VANTAGE`). With `CDN_TARGET`
  set, `tooling/monitoring/run-assessment.sh` runs the CDN vantage from the
  monitoring box after the origin run and keeps both reports under one
  timestamp, with a comparison that names, per control, where the two
  disagree and which layer answered. `TLS-02`, the HTTPS redirect the edge
  issues and the origin never sees, becomes assessable from the CDN vantage.
