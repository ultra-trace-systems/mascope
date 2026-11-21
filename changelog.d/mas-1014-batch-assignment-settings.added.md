- **A batch's assignment settings are saved on the batch.** The untargeted search's
  and the series detection's settings, and the tier bands, are now the batch's own:
  `assignment_settings` on `PATCH /api/sample/batches/{id}` and on the batch's read,
  and `mascope.batches.update(batch_id, assignment_settings=...)` in the SDK, which
  leaves them alone unless given. Every pass of the batch stages runs on them, whoever
  starts it, and the engine's defaults where a batch saved none. A pass records the
  settings in its run's `config`, with `settings_source`, and their hash in its
  summary. Saving changed settings marks the batch behind with `settings_changed` in
  its assignment status, and the next pass runs every stage on them. An unknown
  setting or a value out of bounds is refused with 422, naming the setting.
