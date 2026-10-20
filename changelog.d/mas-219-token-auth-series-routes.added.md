- **An API token reads a batch's two chart series.**
  `POST /api/match/records/ion/series` (per target ion, each sample's
  intensity sum and match category) and `POST /api/batch-peaks/records/series`
  (per batch peak, each sample's peak, intensity and tier), which the app's
  two batch charts draw from, now answer an SDK token as well as a signed-in
  browser, with the same workspace permissions; before, a token got 401
  there. A script gets a batch's series in one call instead of one per
  sample.
