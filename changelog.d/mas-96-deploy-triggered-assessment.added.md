- **The security report tells a throttled control from one not assessed for
  another reason.** A control that skipped because a rate limit answered its
  probe first is marked `throttled` under *Not assessed*, the summary counts
  them, and the report and the console open with a notice that the run was
  throttled. `AUTHN-05`, which follows the controls that spend the login
  budget on purpose, now waits out that window once instead of skipping.
