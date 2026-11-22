- **A batch pin is asked its isotope lines in every sample.** *Use this* on a
  batch peak asked the spectrum for the pinned formula's isotope lines only in
  samples whose assignment run had recorded the gate it asked at. A sample only
  folded into the batch ledger at ingest, the default, or one whose run predates
  the missing-line check, was asked none, so the pin could stay *assigned* with
  a line missing. Such a sample is now asked at the gate a run of it would ask
  at, the one a reading the batch carries into it by itself is asked at.
