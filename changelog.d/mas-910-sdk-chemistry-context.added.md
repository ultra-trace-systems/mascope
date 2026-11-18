- **The SDK sets the chemistry context of a dataset or a batch.**
  `datasets.create`, `datasets.update` and `batches.update` take
  `chemistry_context`, the context a peak assignment run on `auto` takes
  before its reagent profile's default. An update that leaves it out keeps
  it; `chemistry_context=None` clears it.
