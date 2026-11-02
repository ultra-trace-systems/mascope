- **The security report names the listener it measured.** `pentest-report.json`
  records the target's port and whether it is the host's loopback listener,
  `:8443`, which the origin vantage uses once authenticated origin pulls are
  on. Against that listener the report, the console and the scheduled
  runner's summary (`listener=loopback:8443 origin_pull=not_assessed`) say
  that the origin-pull certificate refusal was not assessed, so an archived
  run is no longer read as having verified it.
