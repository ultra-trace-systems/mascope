- **The width an assignment run scored at, on the run.** The runs listing,
  and the SDK's `list_runs`, give each run's `sigma_ppm` and `mu_ppm`, the
  mass-error width and centre it judged a mass error against, with where
  each came from (`sigma_source`, `mu_source`). An imported run gives what
  its engine disclosed in the import's new optional `scoring_width` field.

- **A sample's line width, with or without a run.** `GET /api/samples/{id}`,
  and the SDK's `samples.get`, answer `line_width`: the class the engine
  reads the file as (`orbi` or `tof`), and the resolving power and the line's
  full width at half maximum at m/z 200, from the instrument functions
  detection fitted to the file.
