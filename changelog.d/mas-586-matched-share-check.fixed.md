- **A TOF fit kept on a few of its collection's lines no longer verifies.**
  When a TOF's acquisition axis read further off than the refine window,
  three peaks beside sample-ion lines could fit each other and be stored as
  a verified calibration hundreds of ppm off. A TOF fit must now keep at
  least 40% of the distinct lines its calibration collection offers; one
  below that is stored below the quality bar with a `lines` issue, and its
  record counts the collection's lines (`n_lines`).
