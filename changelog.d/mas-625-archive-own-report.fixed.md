- **A scheduled assessment's record holds only its own report.** Each
  `<ts>-<vantage>.tar.gz` and `<ts>-prod-<NAME>.tar.gz` that
  `run-assessment.sh` keeps now archives `report/pentest-report.json` and
  `report/pentest-report.md` alone. It used to archive the suite's whole
  `report/` directory, history included, so every record repeated every
  earlier run, a production record carried the other deployments' reports,
  and each archive grew until the 20 MB cap could cut it short. The evidence
  trail is the series of per-run archives on the monitoring box.
