- **Each processed file's chemistry is read off its reagent ions, as an
  audit.** When a file is bound to an ionization mode, or parked waiting for
  one, each of its polarities is read for the reagent ions of the shipped
  chemistries - bromide, iodide, nitrate, 15N-nitrate, uronium and the
  EASY-IC fluoranthene beam - and what the spectrum names is stored on the
  file beside the rung and mode that bound it. It binds nothing and changes
  no result: it is how the detector is measured before it may route a file
  nothing else binds. A spectrum whose method leaves the reagent ions outside
  its scan ranges is not called, and neither is one with too few peaks, two
  reagents alike or a mix of 15N- and 14N-nitrate. `backend.chemistry_detection
  = "off"` switches it off. The migration adds the nullable
  `sample_file.binding_evidence` column, empty on every existing file until it
  is processed again.
