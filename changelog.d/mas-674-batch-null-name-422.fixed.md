- **A batch update with a null name answers 422.** `PATCH /api/sample/batches/{id}`
  with `"sample_batch_name": null` used to fail with a server error on the
  database's NOT NULL column; it is now refused as invalid input. A null
  description is still stored, as before.
