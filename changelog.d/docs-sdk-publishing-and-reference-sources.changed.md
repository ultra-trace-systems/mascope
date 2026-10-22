- **The contract for publishing an external engine's assignment run is a user
  page.** *SDK & API > Publishing an assignment run* says what
  `POST /api/peak-assignments/sample/{id}/runs/import` takes and refuses: the
  request and its caps, the row fields and the intensity quantity per
  instrument, how tiers are derived and checked and where an engine's own
  verdict goes, the provenance keys the server reserves, how an import is
  chunked and retried, and what an import replaces and keeps. The reference
  library's README lists the public sources its adapters mirror, with the
  dump each reads and its licence. Both moved from design notes under
  `docs/dev`, which point there now.
