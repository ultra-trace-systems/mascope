- **`mascope_viz`'s views normalise by the sum of a reagent's ions.** A
  reagent spread over clusters whose shares move with humidity, such as
  iodide and its water cluster, is now normalised by their total:
  `normalise=["ion:...", "ion:..."]`, or `--normalise` repeated on
  `mascope-viz view`. Dividing by one of the ions left that ion's diurnal
  share in every species. A sample's normaliser is the sum of the ions
  with a value in it, and a frame's `attrs` say which keys divided it
  (`normalise_members`) and in what share of the samples all of them had
  a value (`normalise_complete`). A tuple of keys among a view's keys is
  their sum as one series, so the reagent's total is drawn and binned like
  any other key. A sum whose ions are read in two polarities is refused.
