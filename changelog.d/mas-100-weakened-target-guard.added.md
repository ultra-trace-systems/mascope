- **A weekly guard checks that the security controls can fail.**
  `tooling/monitoring/run-weakened-guard.sh` brings up a throwaway demo stack
  on the monitoring host and weakens it by configuration, one edit at a time:
  a security header stripped, the nginx version banner on, a wildcard CORS
  header added. Each control the edit should trip must report a finding. The
  guard measures every edit on the target before it judges a control, and
  says inconclusive (exit 2) when one did not take, never that a control
  passed or missed. It covers 8 controls, and lists every other control with
  the reason it has no weakening. The selftests also sort every control by
  whether it passes on silence, and hold each silence control to a positive
  control or an allowlist with a reason, so a new control is sorted or the
  build fails.
