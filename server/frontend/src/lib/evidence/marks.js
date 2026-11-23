/**
 * One mark, one meaning: the registry of the readings a peak's assignment can
 * carry beside its tier, and how each one is drawn.
 *
 * Each batch step of the assignment quality plan added a glyph where its
 * reading showed, and several glyphs came to mean three or four things while
 * each one's meaning, including whether it moves the tier, lived only in its
 * tooltip (docs/dev/assignment_ledger_and_card.md, sections 1 and 5). Here
 * every reading is one entry, and `BaseEvidenceBadge` draws it the same in the
 * ledgers and the inspector card:
 *
 * - the **glyph** says which reading it is, and no two readings share one;
 * - the **tone** says what the reading does to confidence, in the design
 *   system's state colours (`TONES`). A tier chip is a state and a badge is a
 *   reading, and they share colours on purpose: green leans toward
 *   confidence and amber holds it back, whichever shape it is on.
 *
 * An entry's functions take the reading's record, the value a badge is given:
 * `tone(record)`, `summary(record)` (the badge's accessible name) and
 * `tooltip(record)`. The words are the reading's own module's
 * (`lib/batch*.js`), which the columns, popovers and the card's lines share,
 * so a badge and the line beside it cannot word a reading differently.
 * `read(row)` takes a batch peak as the batch ledger serves it and returns the
 * record, or null where the row carries no such reading; `fields` names the
 * batch peak's keys it reads.
 */

import { blendOf, blendSummary, blendTooltip } from '@/lib/batchBlend'
import {
  TONE_HOLDS,
  TONE_REFUTES,
  checkTags,
  checksSummary,
  checksTooltip
} from '@/lib/batchChecks'
import {
  GRADE_DRIFT,
  GRADE_RED,
  GRADE_STABLE,
  continuityLabel,
  continuityTooltip
} from '@/lib/batchContinuity'
import { VERDICT_CO_VARYING, coVariationSummary, coVariationTooltip } from '@/lib/batchCoVariation'
import { flatSeriesOf, flatSeriesSummary, flatSeriesTooltip } from '@/lib/batchFlatSeries'
import { isotopologuesSummary, isotopologuesTooltip } from '@/lib/batchIsotopologues'
import {
  knownSpeciesSummary,
  knownSpeciesTooltip,
  knownSpeciesVerdict
} from '@/lib/batchKnownSpecies'
import {
  strippedLinesCount,
  strippedLinesSummary,
  strippedLinesTooltip
} from '@/lib/batchMergedIsotopologues'
import { persistenceHold, persistenceSummary, persistenceTooltip } from '@/lib/batchPersistence'
import { seriesTooltip } from '@/lib/batchSeries'
import { waterLadderSummary, waterLadderTooltip } from '@/lib/batchWaterLadder'

/** The tones a reading can take: what it does to the row's confidence. */
export const TONE_SUPPORTS = 'supports'
export const TONE_HOLDS_BACK = 'holds-back'
export const TONE_DRIFTED = 'drifted'
export const TONE_NEUTRAL = 'neutral'
export const TONE_HUMAN = 'human'

/**
 * Each tone's colour and what it says, in the legend's words. The colours are
 * the design system's tokens in the meanings it records for them
 * (frontend_design_system.md, section 3): a person's act is an outline in the
 * text colour, which reads on both grounds and is never the accent.
 */
export const TONES = Object.freeze({
  [TONE_SUPPORTS]: {
    key: TONE_SUPPORTS,
    color: 'var(--state-success)',
    label: 'supports',
    meaning: 'leans toward confidence in the reading'
  },
  [TONE_HOLDS_BACK]: {
    key: TONE_HOLDS_BACK,
    color: 'var(--state-warning)',
    label: 'holds back',
    meaning: 'one of the reasons the row is not more confident'
  },
  [TONE_DRIFTED]: {
    key: TONE_DRIFTED,
    color: 'var(--state-info)',
    label: 'drifted',
    meaning: 'the ratio drifts while the channels co-vary: the assignment stands'
  },
  [TONE_NEUTRAL]: {
    key: TONE_NEUTRAL,
    color: 'var(--p-text-muted-color)',
    label: 'moves nothing',
    meaning: 'says something of the peak, not of its formula'
  },
  [TONE_HUMAN]: {
    key: TONE_HUMAN,
    color: 'var(--p-text-color)',
    label: 'a person',
    meaning: 'a person changed this'
  }
})

const joined = (parts, separator) => parts.filter(Boolean).join(separator)

const MANUAL_BATCH = 'Curated by hand for the whole batch'
const MANUAL_SAMPLE = 'Assigned by hand'

/**
 * The readings, in the order a row shows them: what supports first, then
 * what holds back, then what moves nothing, then a person's act.
 */
const DEFINITIONS = [
  {
    id: 'series',
    glyph: 'ph-stairs',
    label: 'Homologous series',
    fields: ['series'],
    read: (row) => row?.series ?? null,
    tone: () => TONE_SUPPORTS,
    summary: (record) => seriesTooltip(record),
    tooltip: (record) => seriesTooltip(record)
  },
  {
    id: 'coVariation',
    glyph: 'ph-wave-sine',
    label: 'Channels co-vary',
    fields: ['co_variation'],
    read: (row) => row?.co_variation ?? null,
    // A pair that does not co-vary, or cannot be read, demotes nothing.
    tone: (record) => (record?.verdict === VERDICT_CO_VARYING ? TONE_SUPPORTS : TONE_NEUTRAL),
    summary: (record) => coVariationSummary(record),
    tooltip: (record) => coVariationTooltip(record)
  },
  {
    // The batch's isotope lines of a compound: the ones "Stamp
    // isotopologues" stamped and the rows "Strip isotopologue rows" took.
    // The record holds either or both, `{ stamping, stripping }`.
    id: 'isotopes',
    glyph: 'ph-chart-bar',
    label: 'Isotope lines stamped',
    fields: ['diagnostic_isotopologues', 'merged_isotopologues'],
    read: (row) => {
      const stamping = row?.diagnostic_isotopologues?.n_found ? row.diagnostic_isotopologues : null
      const stripping =
        strippedLinesCount(row?.merged_isotopologues) > 0 ? row.merged_isotopologues : null
      return stamping || stripping ? { stamping, stripping } : null
    },
    // Lines predicted and found but none held: shown, and it supports nothing.
    tone: (record) =>
      (record?.stamping?.n_stamped ?? 0) > 0 || strippedLinesCount(record?.stripping) > 0
        ? TONE_SUPPORTS
        : TONE_NEUTRAL,
    summary: (record) =>
      joined(
        [
          record?.stamping && isotopologuesSummary(record.stamping),
          record?.stripping && strippedLinesSummary(record.stripping)
        ],
        '; '
      ),
    tooltip: (record) =>
      joined(
        [
          record?.stamping && isotopologuesTooltip(record.stamping),
          record?.stripping && strippedLinesTooltip(record.stripping)
        ],
        '\n'
      )
  },
  {
    // The record is the batch peak itself: the grade, its pairs and whether
    // the ledger has moved past it are all read off the row.
    id: 'ratio',
    glyph: 'ph-pulse',
    label: 'Ratio stability',
    fields: ['continuity'],
    read: (row) => (row?.continuity ? row : null),
    tone: (record) => {
      const grade = record?.continuity_grade
      if (grade === GRADE_STABLE) return TONE_SUPPORTS
      if (grade === GRADE_DRIFT) return TONE_DRIFTED
      if (grade === GRADE_RED) return TONE_HOLDS_BACK
      return TONE_NEUTRAL
    },
    summary: (record) => `Ratio stability: ${continuityLabel(record) || 'not graded'}`,
    tooltip: (record) => continuityTooltip(record)
  },
  {
    id: 'facts',
    glyph: 'ph-check',
    label: 'Batch facts',
    fields: ['batch_checks'],
    read: (row) => (checkTags(row?.batch_checks).length ? row.batch_checks : null),
    // A fact that refutes holds the reading back whatever the others say.
    tone: (record) => {
      const tones = checkTags(record).map((tag) => tag.tone)
      if (tones.includes(TONE_REFUTES)) return TONE_HOLDS_BACK
      if (tones.includes(TONE_HOLDS)) return TONE_SUPPORTS
      return TONE_NEUTRAL
    },
    summary: (record) => checksSummary(record),
    tooltip: (record) => checksTooltip(record)
  },
  {
    // The admission, with the batch peak's reasons and tier beside it: what
    // the rule does is read off the tier (`persistenceHold`).
    id: 'persistence',
    glyph: 'ph-repeat',
    label: 'Persistence only',
    fields: ['persistence'],
    read: (row) =>
      row?.persistence
        ? {
            ...row.persistence,
            tier_reasons: row.tier_reasons ?? null,
            consensus_tier: row.consensus_tier ?? null
          }
        : null,
    // Where something besides its peak has seen the reading, the admission
    // holds nothing back: it only says why the search took the peak.
    tone: (record) =>
      persistenceHold(record?.tier_reasons, record?.consensus_tier)
        ? TONE_HOLDS_BACK
        : TONE_NEUTRAL,
    summary: (record) => persistenceSummary(record),
    tooltip: (record) => persistenceTooltip(record)
  },
  {
    id: 'blend',
    glyph: 'ph-intersect',
    label: 'Probable blend',
    fields: ['blend'],
    read: (row) => blendOf(row),
    tone: () => TONE_HOLDS_BACK,
    summary: (record) => blendSummary(record),
    tooltip: (record) => blendTooltip(record)
  },
  {
    id: 'flatSeries',
    glyph: 'ph-equals',
    label: 'Flat series',
    fields: ['flat_series'],
    read: (row) => flatSeriesOf(row),
    tone: () => TONE_NEUTRAL,
    summary: (record) => flatSeriesSummary(record),
    tooltip: (record) => flatSeriesTooltip(record)
  },
  {
    // What the batch decided a known species is: a rung of a reagent ion's
    // water ladder, or a source ion or listed background decided once for the
    // batch. The record holds either or both, `{ knownSpecies, waterLadder }`.
    id: 'reagent',
    glyph: 'ph-drop',
    label: 'Reagent decided',
    fields: ['known_species', 'water_ladder'],
    read: (row) => {
      const knownSpecies = knownSpeciesVerdict(row) ? row.known_species : null
      const waterLadder = row?.water_ladder ?? null
      return knownSpecies || waterLadder ? { knownSpecies, waterLadder } : null
    },
    tone: () => TONE_NEUTRAL,
    summary: (record) =>
      joined(
        [
          record?.knownSpecies && knownSpeciesSummary(record.knownSpecies),
          record?.waterLadder && waterLadderSummary(record.waterLadder)
        ],
        '; '
      ),
    tooltip: (record) =>
      joined(
        [
          record?.knownSpecies && knownSpeciesTooltip(record.knownSpecies),
          record?.waterLadder && waterLadderTooltip(record.waterLadder)
        ],
        '\n'
      )
  },
  {
    // A person's act: `{ batch: true }` for a batch peak curated for the
    // whole batch, `{ batch: false }` for a sample's row assigned by hand.
    id: 'manual',
    glyph: 'ph-hand-pointing',
    label: 'Changed by hand',
    fields: ['curated'],
    read: (row) => (row?.curated ? { batch: true } : null),
    tone: () => TONE_HUMAN,
    summary: (record) => (record?.batch ? MANUAL_BATCH : MANUAL_SAMPLE),
    tooltip: (record) =>
      `${record?.batch ? MANUAL_BATCH : MANUAL_SAMPLE}; the next assignment run supersedes this`
  }
]

/** Every reading, by id. */
export const MARKS = Object.freeze(
  Object.fromEntries(DEFINITIONS.map((entry) => [entry.id, Object.freeze(entry)]))
)

/** The readings' ids, in the order a row shows them. */
export const MARK_IDS = DEFINITIONS.map((entry) => entry.id)

/**
 * A reading's entry, or null for an id the registry does not know.
 *
 * @param {string} id
 * @returns {object|null}
 */
export const markOf = (id) => (Object.prototype.hasOwnProperty.call(MARKS, id) ? MARKS[id] : null)

/**
 * The tone a reading's record takes, or null where there is no record.
 *
 * @param {string} id
 * @param {*} record
 * @returns {string|null} one of the `TONE_*` keys
 */
export function markTone(id, record) {
  const entry = markOf(id)
  return entry && record ? entry.tone(record) : null
}

/**
 * The readings a batch peak carries, each with its record, in the registry's
 * order.
 *
 * @param {object} row - a batch peak as the batch ledger serves it
 * @param {Array<string>} [ids] - the readings to look for; every one by default
 * @returns {Array<{id: string, record: *}>}
 */
export function ledgerMarks(row, ids = MARK_IDS) {
  return MARK_IDS.filter((id) => ids.includes(id))
    .map((id) => ({ id, record: MARKS[id].read(row) }))
    .filter(({ record }) => record)
}
