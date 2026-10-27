- **The target isotope list checks the batch or collection it filters by.**
  `GET /api/target/isotopes` answered its `target_collection_id` and
  `sample_batch_id` filters for a collection or batch of any workspace, as the
  ion and compound lists did before. Either filter now needs the guest role on
  its workspace, and a global collection stays readable by anyone signed in.
  Its `show_target_collection` flag lists only the collections the caller can
  read: the global ones and those of their workspaces. The list without a
  filter, the shared isotope library, is unchanged.
