/**
 * Which isotopologue of a search hit's ion the searched peak actually is.
 *
 * The composition search scores a whole ION against the spectrum and reports one
 * row per candidate compound, but the peak in hand may be any isotope of that
 * ion - a heavy-isotope satellite lands in the results just as readily as the
 * main peak does. Committing every hit as an M0 would therefore enter a
 * compound's satellite into the ledger as the compound's main peak, which
 * everything that folds an isotopologue family onto its M0 (the tier histogram,
 * the batch consensus, a verification verdict) would then believe.
 *
 * The main isotopologue is the most abundant one in the candidate's predicted
 * pattern, and the label is the nominal mass offset from it - the same
 * convention the assignment engine's own `_isotope_offset_label` uses, so a
 * hand-assigned row reads like an engine-assigned one.
 *
 * @param {Object} hit a composition-search result row
 * @returns {{label: string, formula: string|null}} the isotopologue label
 *   ('M0', 'M+1', 'M-1' ...) and its full isotopologue formula when the hit
 *   carries one
 */
export function isotopeOfHit(hit) {
  const children = hit?.children ?? []
  // No predicted pattern to place the peak in: the honest default is the main
  // isotopologue, which is what a single-isotope candidate means anyway.
  if (!children.length) return { label: 'M0', formula: null }

  const main = children.reduce(
    (best, row) => ((row.relative_abundance ?? 0) > (best.relative_abundance ?? 0) ? row : best),
    children[0]
  )
  // The isotope the search matched at this peak. Taken from the hit's own
  // `cheminfo` rather than from the focused peak, so the answer does not depend
  // on which peak happens to be focused when the button is clicked.
  const searched = hit?.cheminfo?.target_isotope_mz
  const matched =
    searched == null
      ? main
      : children.reduce(
          (best, row) =>
            Math.abs((row.mz ?? 0) - searched) < Math.abs((best.mz ?? 0) - searched) ? row : best,
          children[0]
        )

  const offset = Math.round((matched.mz ?? 0) - (main.mz ?? 0))
  return {
    label: offset === 0 ? 'M0' : offset > 0 ? `M+${offset}` : `M${offset}`,
    formula: matched.target_isotope_formula ?? null
  }
}
