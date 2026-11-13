/**
 * What the batch's known-species decision says of a batch peak, in words.
 *
 * "Decide known species" (step 4.2h of the assignment plan,
 * `batch_known_species.py`) decides each known species once for the batch -
 * an ion of the source's own library a sample's reagent pass claimed, or a
 * compound a reference list tags as background - on the evidence of all its
 * samples, and claims a present one in every sample its peak is in. The batch
 * peak carries the decision under `known_species`: the species, the verdict
 * (`present`, `absent`, `unpooled`) and its evidence, what the claim wrote
 * and the samples it kept with their readings.
 */

import { num } from '@/lib/formatters'

const samples = (n) => `${n} sample${n === 1 ? '' : 's'}`
const moreSamples = (n) => `${n} more sample${n === 1 ? '' : 's'}`

const KEPT = {
  hand_assigned: 'assigned by hand',
  target_library: 'named by the target library',
  line_of_another: "another ion's isotope line",
  claimed_otherwise: "another source ion's claim",
  carries_lines: 'a reading with isotope lines of its own',
  reads_on_twin: 'reading it on another batch peak of its trace'
}

const HELD = {
  curated: 'the batch peak is curated by hand',
  vote_holds: "the samples it could claim would not turn the batch peak's reading",
  moved: 'the batch peak moved while it was decided'
}

/** Why a species decided present was not claimed, in words; a twin's names
 *  the batch peak that claims it by its m/z. */
function heldPhrase(record) {
  if (record.held === 'twin') {
    const at = record.twin_of_mz != null ? `, at m/z ${num.mz.format(record.twin_of_mz)},` : ''
    return `another batch peak of its trace${at} claims it`
  }
  return HELD[record.held] ?? record.held
}

/** The species a record names, as a reader knows it. */
function speciesName(record) {
  const species = record?.species ?? {}
  if (species.kind === 'listed') return species.formula ?? 'a listed compound'
  return species.label ?? species.ion_formula ?? 'a source ion'
}

/** The verdict a batch peak's record carries, or null. */
export function knownSpeciesVerdict(row) {
  return row?.known_species?.verdict ?? null
}

/** The mark's short account: the species and the batch's verdict. */
export function knownSpeciesSummary(record) {
  if (!record) return ''
  const name = speciesName(record)
  if (record.verdict === 'present') return `${name}: present across the batch`
  if (record.verdict === 'absent') return `${name}: not decided present`
  return `${name}: in too few samples to decide for the batch`
}

/** The samples kept, by why, as a phrase. */
function keptPhrase(record) {
  const entries = Object.entries(record.files_by_reason ?? {})
  if (!entries.length) return ''
  return entries.map(([reason, n]) => `${samples(n)} ${KEPT[reason] ?? reason}`).join(', ')
}

/** Of a species not decided present, the samples that read it anyway: on
 *  their own claim, and on an earlier batch decision's, said apart. */
function readAgainstPhrases(record) {
  const byReason = record.files_by_reason ?? {}
  const parts = []
  if (byReason.own_claim) {
    parts.push(`Each sample keeps its own reading: ${samples(byReason.own_claim)} claimed it.`)
  }
  if (byReason.batch_claim) {
    parts.push(`Still claimed on an earlier batch decision: ${samples(byReason.batch_claim)}.`)
  }
  return parts
}

/** The mark's tooltip: the verdict, the evidence it was read on, and what
 *  the claim wrote and kept. */
export function knownSpeciesTooltip(record) {
  if (!record) return ''
  const kind = record.species?.kind === 'listed' ? 'listed as background' : 'a source ion'
  const evidence =
    `Its peak is in ${samples(record.present ?? 0)} of ${record.files ?? 0}; ` +
    `${samples(record.test_held ?? 0)} read it as the species on their own.`
  const parts = [`${knownSpeciesSummary(record)} (${kind}).`, evidence]
  if (record.verdict === 'present') {
    if (record.held) {
      parts.push(`Not claimed: ${heldPhrase(record)}.`)
    } else if (record.members_rewritten) {
      parts.push(`Claimed in ${moreSamples(record.members_rewritten)}.`)
    }
    const kept = keptPhrase(record)
    if (kept) parts.push(`Kept as they read: ${kept}.`)
  } else {
    parts.push(...readAgainstPhrases(record))
  }
  return parts.join(' ')
}
