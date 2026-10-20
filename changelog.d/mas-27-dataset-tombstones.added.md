- **A deleted dataset leaves a tombstone.** Deleting a dataset, alone or with
  its workspace, now keeps a record of what it was: its name, description,
  type, instrument and descriptive metadata, how many batches, samples and
  files it held, who deleted it, when and how, and the provenance block of the
  build that deleted it. `GET /api/datasets/tombstones` lists them, newest
  first, to members of the dataset's workspace and to global admins and
  owners, who also see those of deleted workspaces; the SDK reads them with
  `mascope.datasets.tombstones()`. Datasets removed by raw SQL or a database
  restore leave none.
