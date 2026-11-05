- **A batch import keeps the engine's own verdict beside Mascope's tier.**
  A row of `POST /api/batch-peaks/batch/{id}/runs/import` (and of
  `mascope.batch_peaks.import_run`) takes an optional `engine_tier`, in the
  per-sample import's tier vocabulary, and an optional free-text
  `engine_claim`. Both are kept on the batch peak the row lands on, where its
  consensus names the row's identity, and come off when that identity moves,
  when a later import lands or when the ledger is rebuilt; the run snapshot
  keeps them. The consensus tier is still measured from the members and is
  the only one the ledger sorts on and the fold reads. The *Batch peaks*
  ledger shows an **Engine tier** column and offers **Engine disagrees** in
  its view options, the ledger route and `batch_peaks.list` filter with
  `engine_tier` and `tier_disagrees`, and both ledgers' view options gain an
  **Engine tier** switch, off by default, that counts and filters the tier
  chips by the engine's tier. Rows without the fields import as before. The
  SDK and import docs now say the mechanism table is a catalogue of the
  adducts a server can name, not a list of channels an engine should search.
