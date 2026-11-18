- **Adduct corroboration lifts `p_correct` by a likelihood ratio.** The
  built-in Orbitrap weights by which a compound seen through another adduct
  raises an assignment's `p_correct` were odds ratios, which overstate the
  update. They are now the log likelihood ratios of the same measured rates:
  bromide 1.63 instead of 2.28, ammonium and urea 0.49 instead of 0.83 and
  0.70. Tiers do not change. The corroboration benchmark prints the
  likelihood ratio and names the odds ratio as such. (#3161)
