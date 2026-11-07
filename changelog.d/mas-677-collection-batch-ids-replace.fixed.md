- **Collection update schema says that `sample_batch_ids` replaces the batch
  list.** On `PATCH /api/target/collections/{id}`, the batches the request
  names become the collection's whole batch list: a batch it leaves out is
  detached and marked for rematch. The schema and the operation description
  now say so, so a client that wants to add one batch sends the current ids
  too.
