- **The security report says whether the assessed build lags the release.**
  Set `MASCOPE_PENTEST_CURRENT_RELEASE` on a hand-run of `security/pentest/`
  and the report records `lag`: `current`, `behind`, `ahead`, or `unknown`
  with the reason when no release is supplied or either side is not a release
  tag. The suite never looks the release up itself and the verdict never
  changes the exit code; the scheduled runner keeps that decision. The
  runner's lag check now has a test harness that CI runs.
