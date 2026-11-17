- **The assignment ledger serves what the tiering rules recorded of each
  row.** `GET /api/peak-assignments/sample/{id}` and the SDK's
  `peak_assignments.get` now carry `tier_reason_rules`,
  `mass_gate_corroborated`, `envelope_absent_lines` and `peak_unseparated`
  on every row, each null where the row holds no such record. The gate card
  (`tooling/assignment_compare`) reads them off the paged ledger instead of
  one detail read per committed row, which took it about an hour over the
  gate sets; against a server from before these columns it still reads the
  details.
