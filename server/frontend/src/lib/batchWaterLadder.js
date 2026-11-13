/**
 * What the batch's water ladder measurement says of a batch peak, in words.
 *
 * "Measure water ladders" (step 4.2j of the assignment plan,
 * `batch_water_ladder.py`) reads how far each reagent ion's water ladder
 * reaches on the batch's own samples - Br-.(H2O)n, NO3-.(H2O)n and the like -
 * per stretch of acquisition, and claims a hydrate that passes as the
 * reagent's ion in that stretch's samples. A batch peak at a passing hydrate
 * carries the reading under `water_ladder`: the rung, its reading in each
 * stretch, what the claim wrote and the samples it kept with their readings.
 */

const samples = (n) => `${n} sample${n === 1 ? '' : 's'}`
const moreSamples = (n) => `${n} more sample${n === 1 ? '' : 's'}`

const KEPT = {
  hand_assigned: 'assigned by hand',
  target_library: 'named by the target library',
  listed: 'named by a reference list',
  line_of_another: "another ion's isotope line",
  claimed_otherwise: "another source ion's claim",
  carries_lines: 'a reading with an isotope line the hydrate cannot have'
}

const HELD = {
  curated: 'the batch peak is curated by hand'
}

const percent = (share) => `${Math.round((share ?? 0) * 100)} %`

/** The rung as a reader knows it: the ion, its line beside it where it is
 *  a heavier one. */
function rungName(record) {
  const rung = record?.rung ?? {}
  const ion = rung.ion ?? rung.ion_formula ?? 'a hydrate'
  return rung.track && rung.track !== 'M0' ? `${ion} (${rung.track} line)` : ion
}

/** The mark's short account: the rung and where it passed. */
export function waterLadderSummary(record) {
  if (!record) return ''
  const passed = record.passed_in ?? []
  const stretches = (record.segments ?? []).length
  return `${rungName(record)}: a water-ladder rung in ${passed.length} of ${stretches} stretch${
    stretches === 1 ? '' : 'es'
  } of acquisition`
}

/** The samples kept, by why, as a phrase. */
function keptPhrase(record) {
  const entries = Object.entries(record.files_by_reason ?? {})
  if (!entries.length) return ''
  return entries.map(([reason, n]) => `${samples(n)} ${KEPT[reason] ?? reason}`).join(', ')
}

/** The mark's tooltip: the rung, its reading in each stretch it passed in,
 *  and what the claim wrote and kept. */
export function waterLadderTooltip(record) {
  if (!record) return ''
  const parts = [`${waterLadderSummary(record)}.`]
  const passed = (record.segments ?? []).filter((s) => s.verdict === 'passed')
  if (passed.length) {
    parts.push(
      passed
        .map(
          (s) =>
            `Stretch ${s.segment + 1}: in ${percent(s.presence)} of its ${samples(
              s.spectra ?? 0
            )}, ${percent(s.decoy_presence)} at the masses beside it.`
        )
        .join(' ')
    )
  }
  if (record.held) {
    parts.push(`Not claimed: ${HELD[record.held] ?? record.held}.`)
  } else if (record.members_rewritten) {
    parts.push(`Claimed in ${moreSamples(record.members_rewritten)}.`)
  }
  if (record.moved_analyte) {
    const before = record.read_before?.formula
    parts.push(`The batch peak read ${before ?? 'an analyte'} before.`)
  } else if (record.reads?.role !== 'reagent') {
    parts.push("Its other samples' readings keep the batch peak's own.")
  }
  const kept = keptPhrase(record)
  if (kept) parts.push(`Kept as they read: ${kept}.`)
  return parts.join(' ')
}
