- **A scheduled security report records whether its build lags the current
  release.** The assessment runner looks the release up before the suite runs
  and passes it to both vantages, so a report no longer says the lag is
  `unknown` while the runner's summary knows it.
