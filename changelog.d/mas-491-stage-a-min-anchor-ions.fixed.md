- **A few bright ions no longer calibrate a peak assignment run on themselves.**
  A run measures a sample's mass offset and width from the lines of your
  target library that it matched, and it counted lines alone. One ion's lines
  all share that ion's own mass error, so a sample whose library matched a
  few bright ions with many lines each was scored at their error and at how
  well their lines agree, and so was the untargeted search. The offset and
  width are now measured only from lines of at least eight different ions.
  Below that, the sample is scored as one whose library matched too few
  lines: at the instrument class's width, centred on the reagent ions where
  they show an offset.
