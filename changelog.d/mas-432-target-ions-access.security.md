- **The target ion and compound lists check the batch or collection they
  filter by.** `GET /api/target/ions` and `GET /api/target/compounds` answered
  their `target_collection_id` and `sample_batch_id` filters for a collection
  or batch of any workspace. Either filter now needs the guest role on its
  workspace, as `GET /api/target/collections/{id}` and the association routes
  do, and a global collection stays readable by anyone signed in. The lists
  without a filter, the shared compound and ion library, are unchanged.
