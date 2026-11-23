- **A calibration collection can mark compounds as optional anchors.** An
  optional anchor is a background compound some instruments' spectra carry
  and others' do not. A TOF fit's share of its collection's lines counts an
  optional anchor's lines only where the fit kept them, so a file without
  them is held to the rest of the collection. The collection API sets the
  marks with `optional_anchor_compound_ids` when a collection is created or
  edited, and reads them back on the collection; a change of them flags the
  batches calibrated on the collection *recalibrate*. The shipped Uronium,
  positive calibrants mark their siloxanes and caprolactam, so a TOF uronium
  file that holds the urea series alone still clears the bar. A TOF record
  says how many lines of optional anchors it left out (`n_optional_lines`).
