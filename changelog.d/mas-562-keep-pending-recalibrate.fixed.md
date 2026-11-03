- **A batch flagged for re-calibration keeps the flag after a later edit.**
  Editing an ionization mode's mechanisms or diagnostic collection, or a
  target collection, after its calibration collection changed used to lower
  the batch's flag to a rematch, so the m/z re-fit was never offered. Such an
  edit now leaves a pending re-calibration in place.
