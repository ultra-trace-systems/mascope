- **The SDK runs peak assignment on a sample and records verdicts.**
  `mascope.peak_assignments.assign(sample_id)` launches a run, waits for it
  and returns its ledger; with `wait=False` it returns the run's id, and
  `wait_for_run` waits for it later. A sample with a run already going raises
  `AssignmentInFlightError` naming that run, and one that cannot be assigned
  raises `SampleIneligibleError` with the reason, without waiting. A run that
  fails or is cancelled raises `AssignmentRunError`; a wait that runs out
  raises `AssignmentTimeoutError` with the run's id. `verify` records a
  verdict on one assignment and `list_verifications` lists them;
  `fit_aggregate` scores any formula and ionization mechanism against a
  sample. `assign` and `verify` are sent once, never again on a lost answer,
  so a verdict cannot be counted twice in the confidence calibration. Every
  SDK error now keeps the server's answer (`body`, `detail` and `code`), and
  a 409 raises `ConflictError`. The sample assign route names its refusals
  with a code, `assignment_in_flight` or `sample_ineligible`. The SDK is
  version 2026.10.28.1.
