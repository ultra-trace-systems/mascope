- **Global ids in the provenance of an export.** The *Provenance* sheet of a
  batch's *Batch data* spreadsheet now names its dataset and batch by global
  id as well, `inputs.dataset_global_id` and `inputs.sample_batch_global_id`,
  in the form `<deployment_id>:<entity>:<local_id>`, which tells apart records
  of two deployments that happen to share a local id. A deployment without an
  id gives none. The format is in `docs/maintaining.md`, *Deployment
  identity*.
