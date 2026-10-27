- **A reference source keeps the publications it comes from.** A load now
  records them on its source row (`reference_source.citations`, a new
  column): a list file's header `references`, or, for a CSV or a database,
  the one `mascope reference sync` and the backend's `reference_sync` script
  are given with the new `--citation` flag and `--doi` or `--isbn`.
  `GET /api/reference/sources` returns them as each source's `references`.
  Before, a list loaded from an operator's own file lost its publication at
  load, and a shipped list could be cited only from the installed file,
  whichever version was loaded. Rows loaded before this release record none:
  `mascope reference seed` (or `reference_seed` on a server) records a shipped
  list's citations on its active row without reloading it, and until then the
  route cites a shipped list from the installed file while the version loaded
  is the file's. Re-sync any other list to record what it cites.
