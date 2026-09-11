/**
 * Why a committed assignment holds the tier it holds, as its run recorded it.
 *
 * Every row a run commits carries `provenance.tier_reasons`, the list the
 * backend's tiering pass writes (peak_assignments/tiering.py): one
 * `{rule, detail, caps}` per reason, where `detail` is the server's sentence
 * about this row and `caps` says the reason takes the top tier. This module
 * names the rules and says what `caps` did to a row - one place, so no two
 * surfaces word the same reason two ways.
 */

/**
 * Short names for the rules, keyed by what the server writes.
 *
 * A rule this build does not know - a newer rule set, or another engine's own -
 * is shown by its key rather than dropped: the sentence beside it is still the
 * server's, and hiding it would make a capped row read as one nothing capped.
 */
export const TIER_REASON_LABELS = Object.freeze({
  // What takes the top tier.
  odd_electron: 'radical neutral',
  candidate_density: 'rivals left standing',
  envelope_neighbour: "a neighbour's isotope line",
  oxygen_lattice: 'oxygen lattice',
  carbon_free: 'carbon-free formula',
  // What an earlier pass of the run took it on, restated in the same list.
  off_calibration: 'off calibration',
  ambiguous_nitrogen: 'ambiguous nitrogen',
  minor_channel: 'minor channel only',
  // What a row that kept its tier kept it on.
  corroborated: 'second channel',
  no_close_rival: 'no close rival',
  not_measured: 'not measured',
  // An isotopologue is judged through its M0.
  inherited_from_owner: 'follows its M0'
})

/**
 * The name a rule is shown by.
 *
 * Looked up as an own key rather than by indexing: a record whose rule read
 * "constructor" would otherwise be labelled with a function.
 *
 * @param {string} rule - the rule as the server wrote it
 * @returns {string}
 */
export function tierReasonLabel(rule) {
  if (typeof rule !== 'string' || !rule) return 'unnamed rule'
  return Object.prototype.hasOwnProperty.call(TIER_REASON_LABELS, rule)
    ? TIER_REASON_LABELS[rule]
    : rule.replaceAll('_', ' ')
}

/**
 * The reasons a row's provenance carries, named - or an empty list when it
 * carries none, which is a row no tiering pass judged: a run from before the
 * pass, an imported run, a row assigned by hand.
 *
 * @param {object|null} provenance - the row's provenance, from the detail fetch
 * @returns {Array<{rule: string|null, label: string, detail: string, caps: boolean}>}
 */
export function tierReasonsOf(provenance) {
  const reasons = provenance?.tier_reasons
  if (!Array.isArray(reasons)) return []
  return reasons
    .filter((reason) => reason && typeof reason === 'object')
    .map((reason) => ({
      rule: typeof reason.rule === 'string' ? reason.rule : null,
      label: tierReasonLabel(reason.rule),
      detail: typeof reason.detail === 'string' ? reason.detail : '',
      caps: reason.caps === true
    }))
}

/**
 * The icon a reason is marked with: a down arrow for one that caps, a dash for
 * one that claims nothing, an elbow for an isotopologue that follows its M0
 * without being taken down, a check for what a row kept its tier on.
 *
 * Capping decides first, including for an isotopologue its M0 took down, so
 * every line that holds a tier down wears the same mark.
 *
 * @param {object} reason - one entry of `tierReasonsOf`
 * @returns {string} a phosphor icon class
 */
export function reasonIcon(reason) {
  if (reason?.caps) return 'ph-arrow-down'
  if (reason?.rule === 'not_measured') return 'ph-minus'
  if (reason?.rule === 'inherited_from_owner') return 'ph-arrow-elbow-down-right'
  return 'ph-check'
}

/**
 * What a reason did to the row it was recorded on, for its hover text.
 *
 * `caps` records what a rule would take, not what it took: one that fires on a
 * row its evidence already put below candidate changes nothing and still says
 * what it found. So the sentence is read off the row's tier.
 *
 * @param {object} reason - one entry of `tierReasonsOf`
 * @param {string} tier - the tier of the row the reason was recorded on
 * @param {{viaM0?: boolean}} [options] - `viaM0` for a reason read off the M0
 *   of the isotopologue in view, whose tier `tier` then is
 * @returns {string}
 */
export function reasonTooltip(reason, tier, { viaM0 = false } = {}) {
  const subject = viaM0 ? 'the M0' : 'this row'
  let text
  if (!reason?.caps) {
    text = `Caps nothing: ${subject} holds the tier its evidence earned`
  } else if (tier === 'candidate') {
    text = `Holds ${subject} at candidate - it cannot be assigned while this stands`
  } else if (tier === 'below_assignability') {
    text = `Would hold ${subject} at candidate, but its evidence already puts it lower`
  } else {
    text = `Caps ${subject} at candidate`
  }
  return viaM0 ? `${text}. Recorded on the M0, which this isotopologue follows.` : text
}
