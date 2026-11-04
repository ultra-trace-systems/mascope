- **`mascope_viz` keeps a scope's data in a local campaign store.**
  `sync(client, scope)` fills a parquet cache, one directory per server and
  scope, with the scope's samples, files, target-ion series
  (`ion:<target_ion_id>`), batch-peak series
  (`formula:<formula>|<mechanism>`, each with the ledger run that read it),
  species and hourly and daily rollups, reading each batch with one call per
  series kind. A later sync reads again only the batches the server lists
  as modified, whose sample count or ledger run changed, on whose ledger a
  batch run started or ended, or that hold a sample from the last day; a
  batch whose read fails is skipped, recorded, and tried again next time.
  `store.status()` says what is cached, how old it is, which engine runs
  read the batches' formulas, and whether they mix.
