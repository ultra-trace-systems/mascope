- **An ion's intensity no longer halves in files where an isotope line misses
  its ratio.** A matched isotope line outside the isotope ratio tolerance lost
  its intensity as well as its score, so the ion's intensity in that file was
  the sum of its other lines alone. For an ion with two near-equal lines, such
  as a bromide adduct, that halved it in every such file, and the batch time
  series jumped between 1x and 2x from file to file. The line now keeps its
  intensity whenever it is on its mass; its score, and so the match category,
  still drops. A line outside the m/z tolerance loses both, as before. Batches
  matched before the change keep their old intensities until an operator runs
  the `reaggregate_ratio_gated_intensity` maintenance script once, which
  writes the new ones from the stored matches without matching again
  (`docs/maintaining.md`).
