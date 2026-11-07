- **A guest of a workspace is no longer offered a paste into a new dataset
  or batch.** *Paste batch into a new dataset* for a copied batch, and
  *Paste samples into a new dataset and batch* and *Paste samples into a new
  batch*, were shown to a guest, whose paste then failed with a 403: creating
  a dataset or a batch takes the editor role in the workspace. They now
  appear only in a workspace you may edit, as the cut-batch paste already did
  (`docs/user/guides/import-files.md`).
