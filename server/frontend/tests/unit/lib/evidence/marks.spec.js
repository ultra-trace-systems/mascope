import { describe, it, expect } from 'vitest'

import {
  FORMULA_CELL_MARKS,
  MARKS,
  MARK_IDS,
  TONES,
  ledgerMarks,
  markOf,
  markTone
} from '@/lib/evidence/marks'
import * as batchChecks from '@/lib/batchChecks'

// One mark, one meaning (docs/dev/assignment_ledger_and_card.md, section 5):
// every reading of a peak's assignment is one entry, with a glyph no other
// reading wears and a tone that says what it does to confidence.

// Every reading the batch ledger shows today, by the batch peak's key: the
// marks beside a formula and the reading columns.
const LEDGER_READINGS = [
  'series',
  'diagnostic_isotopologues',
  'merged_isotopologues',
  'known_species',
  'water_ladder',
  'blend',
  'flat_series',
  'persistence',
  'curated',
  'co_variation',
  'batch_checks',
  'continuity'
]

describe('the evidence mark registry', () => {
  it('has one entry for every reading the batch ledger shows, and none for anything else', () => {
    const fields = MARK_IDS.flatMap((id) => MARKS[id].fields)
    expect([...fields].sort()).toEqual([...LEDGER_READINGS].sort())
    // Each reading is one entry's: no key is read by two.
    expect(new Set(fields).size).toBe(fields.length)
  })

  it('gives no two readings the same glyph', () => {
    const glyphs = MARK_IDS.map((id) => MARKS[id].glyph)
    expect(new Set(glyphs).size).toBe(glyphs.length)
    for (const glyph of glyphs) expect(glyph).toMatch(/^ph-[a-z-]+$/)
  })

  it('draws the note’s glyphs, the retired double meanings in their one remaining sense', () => {
    expect(Object.fromEntries(MARK_IDS.map((id) => [id, MARKS[id].glyph]))).toEqual({
      series: 'ph-stairs',
      coVariation: 'ph-wave-sine',
      isotopes: 'ph-chart-bar',
      ratio: 'ph-pulse',
      facts: 'ph-check',
      persistence: 'ph-repeat',
      blend: 'ph-intersect',
      flatSeries: 'ph-equals',
      reagent: 'ph-drop',
      manual: 'ph-hand-pointing'
    })
  })

  it('names every reading, and colours every tone with a design-system token', () => {
    for (const id of MARK_IDS) {
      const entry = MARKS[id]
      expect(entry.id).toBe(id)
      expect(entry.label).toEqual(expect.any(String))
      for (const fn of ['read', 'tone', 'summary', 'tooltip']) {
        expect(typeof entry[fn], `${id}.${fn}`).toBe('function')
      }
    }
    expect(Object.fromEntries(Object.values(TONES).map((t) => [t.key, t.color]))).toEqual({
      supports: 'var(--state-success)',
      'holds-back': 'var(--state-warning)',
      drifted: 'var(--state-info)',
      neutral: 'var(--p-text-muted-color)',
      human: 'var(--p-text-color)'
    })
  })

  it('knows only its own readings', () => {
    expect(markOf('constructor')).toBeNull()
    expect(markOf('nope')).toBeNull()
    expect(markTone('series', null)).toBeNull()
  })
})

// The tones, as the note's section 5 table gives them.
describe('the evidence mark tones', () => {
  it('supports on a series, a co-varying pair and isotope lines held', () => {
    expect(markTone('series', { unit: 'CH2' })).toBe('supports')
    expect(markTone('coVariation', { verdict: 'co_varying', r: 0.8 })).toBe('supports')
    expect(markTone('isotopes', { stamping: { n_found: 2, n_stamped: 1 } })).toBe('supports')
    expect(markTone('isotopes', { stripping: { n_lines: 2 } })).toBe('supports')
  })

  // Shown, and nothing to lean on: a pair that does not co-vary or cannot be
  // read, lines predicted and found but none held, another peak's line.
  it('moves nothing where the reading found nothing to lean on', () => {
    expect(markTone('coVariation', { verdict: 'not_co_varying', r: 0.1 })).toBe('neutral')
    expect(markTone('coVariation', { verdict: 'untestable' })).toBe('neutral')
    expect(markTone('isotopes', { stamping: { n_found: 1, n_stamped: 0 } })).toBe('neutral')
    expect(markTone('isotopes', { stampedAs: { label: '13C' } })).toBe('neutral')
  })

  // The batch checks' tags have tones of their own, where 'holds' is a fact
  // that holds, which supports. No registry tone may share one of their
  // strings: a tag compared against it would match the opposite meaning.
  it('shares no tone string with the batch checks', () => {
    const checks = Object.entries(batchChecks)
      .filter(([name]) => name.startsWith('TONE_'))
      .map(([, value]) => value)
    expect(checks).toContain('holds')
    for (const key of Object.keys(TONES)) expect(checks, key).not.toContain(key)
  })

  it('reads ratio stability as supports, drifted or holds back by its grade', () => {
    const row = (grade) => ({ continuity: { pairs: [] }, continuity_grade: grade })
    expect(markTone('ratio', row('stable'))).toBe('supports')
    expect(markTone('ratio', row('drift'))).toBe('drifted')
    expect(markTone('ratio', row('red'))).toBe('holds-back')
    expect(markTone('ratio', row('inconclusive'))).toBe('neutral')
    expect(markTone('ratio', row('insufficient'))).toBe('neutral')
    expect(markTone('ratio', row(null))).toBe('neutral')
  })

  it('reads the batch facts as support, or holding back where one refutes', () => {
    const locked = { element: 'Br', verdict: 'locked' }
    expect(markTone('facts', { halogen_lock: locked })).toBe('supports')
    expect(markTone('facts', { halogen_lock: locked, label_twin: { verdict: 'absent' } })).toBe(
      'holds-back'
    )
    expect(markTone('facts', { label_twin: { verdict: 'untestable' } })).toBe('neutral')
  })

  it('holds back on persistence alone, and moves nothing where something else saw it', () => {
    const held = [{ rule: 'persistence_only', detail: '...', caps: true }]
    const lifted = [{ rule: 'second_line', detail: '...', caps: false }]
    expect(
      markTone('persistence', { share: 0.9, tier_reasons: held, consensus_tier: 'candidate' })
    ).toBe('holds-back')
    expect(
      markTone('persistence', {
        share: 0.9,
        tier_reasons: held,
        consensus_tier: 'below_assignability'
      })
    ).toBe('holds-back')
    expect(markTone('persistence', { share: 0.9, tier_reasons: lifted })).toBe('neutral')
  })

  it('holds back on a probable blend; a flat series and a reagent decided move nothing', () => {
    expect(markTone('blend', { formulas: ['A', 'B'] })).toBe('holds-back')
    expect(markTone('flatSeries', { verdict: 'flat' })).toBe('neutral')
    expect(markTone('reagent', { waterLadder: { rung: {} } })).toBe('neutral')
    expect(markTone('reagent', { knownSpecies: { verdict: 'present' } })).toBe('neutral')
  })

  it("marks a person's act in the human tone", () => {
    expect(markTone('manual', { batch: true })).toBe('human')
    expect(MARKS.manual.summary({ batch: true })).toBe('Curated by hand for the whole batch')
    expect(MARKS.manual.summary({ batch: false })).toBe('Assigned by hand')
  })
})

describe('the readings of a batch peak', () => {
  const ROW = {
    consensus_tier: 'candidate',
    curated: true,
    series: { unit: 'CH2', step: 1, depth: 1, root_formula: 'C5H10O6', units: ['CH2'] },
    diagnostic_isotopologues: { formula: 'C2H4O2', n_predicted: 2, n_found: 1, n_stamped: 1 },
    merged_isotopologues: { n_lines: 2, lines: [] },
    persistence: { share: 0.9, n_present: 11, n_samples: 12, split: 0.4, formula: 'C2H4O2' },
    tier_reasons: [{ rule: 'persistence_only', detail: '...', caps: true }],
    blend: { formulas: ['C2H4O2', 'C3H8O'], axis: 'time' },
    flat_series: { verdict: 'flat', cv: 0.1, share: 0.9 },
    water_ladder: { rung: { ion: '[Br+H2O]-' }, passed_in: [0], segments: [] },
    known_species: { verdict: 'present', species: { label: 'Br-' } },
    co_variation: { verdict: 'co_varying', r: 0.8 },
    batch_checks: { halogen_lock: { element: 'Br', verdict: 'locked' } },
    continuity: { pairs: [] },
    continuity_grade: 'stable'
  }

  it('lists every reading the row carries in the registry’s order', () => {
    expect(ledgerMarks(ROW).map(({ id }) => id)).toEqual(MARK_IDS)
  })

  // The co-variation, facts and ratio-stability readings keep columns of
  // their own in the batch ledger; beside the formula they would say it twice.
  it('lists the formula cell’s readings only, when asked for them', () => {
    expect(ledgerMarks(ROW, FORMULA_CELL_MARKS).map(({ id }) => id)).toEqual([
      'series',
      'isotopes',
      'persistence',
      'blend',
      'flatSeries',
      'reagent',
      'manual'
    ])
  })

  it('carries the batch peak’s reasons and tier into the persistence record', () => {
    const [persistence] = ledgerMarks(ROW, ['persistence'])
    expect(persistence.record).toMatchObject({
      share: 0.9,
      tier_reasons: ROW.tier_reasons,
      consensus_tier: 'candidate'
    })
    expect(MARKS.persistence.tooltip(persistence.record)).toMatch(/is held at candidate until/)
  })

  it('folds a reading of two steps into one badge that says both', () => {
    const [isotopes] = ledgerMarks(ROW, ['isotopes'])
    expect(MARKS.isotopes.summary(isotopes.record)).toBe(
      "1 of 2 isotope lines stamped; 2 rows stripped to this compound's isotope lines"
    )
    const [reagent] = ledgerMarks(ROW, ['reagent'])
    expect(MARKS.reagent.summary(reagent.record)).toBe(
      'Br-: present across the batch; [Br+H2O]-: a water-ladder rung in 1 of 0 stretches of acquisition'
    )
  })

  it('lists nothing for a row with no readings, and skips the empty ones', () => {
    expect(ledgerMarks({ consensus_tier: 'assigned' })).toEqual([])
    const empty = {
      diagnostic_isotopologues: { n_found: 0, n_stamped: 0 },
      merged_isotopologues: { n_lines: 0, lines: [] },
      flat_series: { verdict: 'varying' },
      blend: { formulas: ['A'] },
      known_species: {},
      batch_checks: {},
      curated: false
    }
    expect(ledgerMarks(empty)).toEqual([])
  })
})
