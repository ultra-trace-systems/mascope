- **The sample peaks listing no longer logs a warning for a peak without a
  signal-to-noise.** Since 1.13.0 a TOF peak with no baseline around it, or a
  flat one, has no signal-to-noise, and every read of
  `GET /api/samples/{sample_item_id}/peaks` that listed one logged a warning
  that the route returned non-finite floats. The route now sends that peak's
  `signal_to_noise` as `null` itself. The response is the same as before.
