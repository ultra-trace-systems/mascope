- **Every security control says whether it is safe against production.** Each
  control's marker carries `mutating=True` or `mutating=False`, with no default:
  a control without it fails the run at collection. A control is non-mutating
  only if it signs in to no account, spends no budget, writes nothing, and
  stays harmless on the day the control it checks is broken; 16 of the 53 are.
  `pytest --non-mutating` runs only those and signs in nowhere, reading the
  build from `GET /api/version/public`. Every report says which selection ran,
  and every request the suite sends carries the user agent
  `mascope-security-assessment/1`, so an assessment is identifiable in the
  target's logs and error tracking.

- **The scheduled assessment can watch production.**
  `run-assessment.sh --production` runs the suite's non-mutating subset from
  the monitoring box against every deployment in `PRODUCTION_TARGETS`, through the CDN with the
  operator header and TLS verified, and with no account. Each deployment's
  report has the format and the 400-day retention of a staging run's, as
  `<ts>-prod-<NAME>.*`, and its own heartbeat, `PRODUCTION_HC_URL`. One
  failing deployment fails the run and the rest are still assessed. A
  deployment behind the current release is recorded, not failed. Each
  deployment must run v1.14.0 or later, which serves the anonymous version
  route the subset reads its build from.
  `tooling/monitoring/README.md`, section 10, names the controls that may run
  where.
