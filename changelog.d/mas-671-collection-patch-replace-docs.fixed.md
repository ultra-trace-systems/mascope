- **Collection update schema says that the compound fields replace the list.**
  On `PATCH /api/target/collections/{id}`, `target_compound_ids` and
  `target_compounds_create` together become the collection's whole compound
  list, and the schema and the operation description now say so instead of
  "added": to add a compound, send the current ids too.
