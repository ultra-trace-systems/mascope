- **The security suite's weakened target now shows the rate limits and the
  cross-site write check can fail.** Two test-only settings,
  `MASCOPE_PENTEST_RATE_LIMIT_OFF` and `MASCOPE_PENTEST_ORIGIN_GUARD_OFF`,
  switch the backend's rate limiter and its refusal of a foreign-origin
  write off, and the guard's weakened demo stack sets them to require that
  RL-01, RL-02, RL-03 and CSRF-01 then report. The backend honours them only
  on a demo stack, beside `MASCOPE_ALLOW_DEMO_SEED`, which a real deployment
  never sets; there it logs an error and keeps both controls on. A demo stack
  running with one logs a warning at start.
