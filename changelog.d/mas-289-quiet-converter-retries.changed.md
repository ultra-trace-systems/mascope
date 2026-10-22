- **The file converter no longer reports its retries to error monitoring.**
  When a request to the backend fails and is retried, each retry is now
  logged at INFO instead of WARNING. A backend restart that the next attempt
  outlasts no longer opens a GlitchTip issue. A request that still fails
  after the last retry is reported as before, by the step that made it.
