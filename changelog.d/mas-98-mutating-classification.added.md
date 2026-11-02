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
