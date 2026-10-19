- **An SDK load above `confirm_above` no longer goes ahead unasked in a
  script.** With no interactive input to read the confirmation prompt from
  (stdin closed or at end of file, as in a pipeline or an agent's shell),
  `load_peaks`, `load_assignments` and `load_peak_timeseries` used to load
  without asking; they now raise the new `ConfirmationRequiredError`, with
  the sample count and threshold, before loading anything. Pass
  `confirm_above=None` to load without asking.
