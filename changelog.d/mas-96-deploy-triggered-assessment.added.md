- **The security report tells a throttled control from one not assessed for
  another reason.** A control that skipped because a rate limit answered its
  probe first is marked `throttled` under *Not assessed*, the summary counts
  them, and the report and the console open with a notice that the run was
  throttled. `AUTHN-05`, which follows the controls that spend the login
  budget on purpose, now waits out that window once instead of skipping.

- **The security assessment runs when a release reaches the assessed
  deployment, not only on its weekly clock.** `run-assessment.sh
  --on-deploy` (`tooling/monitoring/`), run every few minutes from cron on
  the monitoring box, reads the release the deployment reports and assesses
  each new one once, with the same records and retention as the periodic
  run. Runs never overlap: a deploy run is deferred while another run is
  going and for ten minutes after one ends (`MIN_INTERVAL`), and the
  periodic run waits its turn, its heartbeat unchanged. Each summary ends in
  `trigger=schedule` or `trigger=deploy` and counts the controls a rate limit
  throttled (`throttled=N`).
