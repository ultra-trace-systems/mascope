- **The operator documentation covers the method-binding switch, the TOF
  calibration audit, and what a backend start does for peak assignment.**
  `docs/maintaining.md` gains "Method bindings" (`backend.method_binding`,
  the two backfills that prime the table from a server's history, and the
  two read-only reports, `report_method_binding_disagreements` and
  `report_stitch_overlaps`), "Auditing a TOF instrument's mass calibration"
  (`audit_tof_calibration` and its `TOF_AUDIT_*` variables), and, under peak
  assignment, what every start seeds and why the first one may be slow, the
  standard mechanism notation and its migration, and how interrupted runs are
  reclaimed at startup. The how-it-works calibration page states the TOF
  matching constants and that a TOF fit is absolute. All of it moved from
  design notes under `docs/dev`, which point there now.
