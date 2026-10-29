- **`base.mascope.toml` writes every feature setting out as its default.**
  Each feature setting of `[meta]` and `[backend]` is now a live line holding
  the model's default, with a short comment naming its section in
  `docs/maintaining.md`, where the explanations moved. The settings that are
  unset by default (`mfa_required_min_role`, `reference_licenses`,
  `deployment_id`, `[frontend] acquisition_filter` and the File Agent's file
  name prefix and suffix) appear only in a marked examples block at the end of
  the file. No effective value changes, and a `tus_max_upload_gb` still under
  `[backend]` in an env's toml keeps winning over the base default.
