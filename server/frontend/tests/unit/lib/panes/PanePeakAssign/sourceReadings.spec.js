import { describe, it, expect } from 'vitest'

import { num } from '@/lib/formatters'
import {
  sourceAbsences,
  sourceRole,
  sourceStatement,
  sourceTiles
} from '@/lib/panes/PanePeakAssign/card/sourceReadings.js'

// What the inspector card reads off a reagent or an artifact row: what it is,
// which claim took the peak, its tiles and the lines for what it has none of.

const REAGENT = {
  role: 'reagent',
  assigned_formula: null,
  ion_formula: 'Br-',
  isotope_label: null,
  mz_error_ppm: 0.42,
  sample_peak_intensity: 5.0e6,
  tier: 'unassigned'
}
const LINE = { ...REAGENT, isotope_label: '81Br', owner_peak_assignment_id: 'pa-1' }
const ARTIFACT = {
  role: 'artifact',
  assigned_formula: null,
  ion_formula: null,
  mz_error_ppm: null,
  sample_peak_intensity: 1.2e4,
  tier: 'unassigned'
}
const LIBRARY = { reagent: { ion: 'Br-', family: 'reagent', references: ['a', 'b'], mz: 78.91889 } }
const MODEL = { mu_ppm: 0.3, anchors: { commits: 40, source_lines: 3, source_capped: 1 } }
const ISOTOPES = {
  value: '1 of 1',
  caption: 'isotope lines found of those predicted',
  tooltip: 't'
}

const tile = (tiles, key) => tiles.find((t) => t.key === key)

describe('sourceRole', () => {
  it('reads the reagent and the artifact role, and nothing else', () => {
    expect(sourceRole(REAGENT)).toBe('reagent')
    expect(sourceRole(ARTIFACT)).toBe('artifact')
    expect(sourceRole({ role: 'M0' })).toBeNull()
    expect(sourceRole(null)).toBeNull()
  })
})

describe('sourceStatement', () => {
  it('names the ion, its family and the reagent pass on a library claim', () => {
    const why = sourceStatement(REAGENT, LIBRARY)
    expect(why.title).toBe('Why reagent')
    expect(why.lead).toBe(
      "Br- is an ion of the run's chemistry, from the reagent's own ladder: the source made it, not the sample."
    )
    expect(why.claim).toBe("Claimed by the reagent pass, from the library of the run's chemistry.")
    expect(why.notes).toEqual(['Named in 2 published works.'])
  })

  it('names the line of its ion on an isotope line', () => {
    expect(sourceStatement(LINE, LIBRARY).lead).toMatch(/^The \[81Br\] line of Br- is an ion/)
  })

  it("falls back to the row's ion before its detail lands", () => {
    const why = sourceStatement(REAGENT, null)
    expect(why.lead).toBe(
      "Br- is an ion of the run's chemistry: the source made it, not the sample."
    )
    expect(why.notes).toEqual([])
  })

  it("names the batch's known-species decision, with its counts", () => {
    const provenance = {
      reagent: {
        ion: 'Br-',
        reason: 'batch_known_species',
        batch_known_species: { verdict: 'present', present: 5, files: 6 }
      }
    }
    expect(sourceStatement(REAGENT, provenance).claim).toBe(
      "Claimed on the batch's word: Decide known species found it present in 5 of 6 samples."
    )
    provenance.reagent.batch_known_species = { verdict: 'present' }
    expect(sourceStatement(REAGENT, provenance).claim).toBe(
      "Claimed on the batch's word: Decide known species found it present."
    )
  })

  it("names the batch's water ladder", () => {
    const provenance = { reagent: { ion: 'Br-(H2O)', reason: 'batch_water_ladder' } }
    expect(sourceStatement(REAGENT, provenance).claim).toContain("batch's water ladder")
  })

  it('names the fragment claim and its parent', () => {
    const provenance = {
      reagent: {
        ion: 'C6H7+',
        kind: 'fragment',
        family: 'fragment',
        parent: { label: 'a monoterpene', ratio: 0.12, max_ratio: 0.5 }
      }
    }
    const why = sourceStatement(REAGENT, provenance)
    expect(why.lead).toContain("from a fragment of an analyte's ion")
    expect(why.claim).toBe(
      "Claimed by the fragment claim, after the stages committed a monoterpene: its height is 12 % of the parent's, at most 50 %."
    )
  })

  it("reads a calibrant's own fragment ion as a library claim, not the fragment claim", () => {
    const provenance = {
      reagent: { ion: '[C16H10-H]+', kind: 'fragment', family: 'calibrant', mz: 201.07 }
    }
    const why = sourceStatement(REAGENT, provenance)
    expect(why.lead).toContain('from the calibrant beam')
    expect(why.claim).toBe("Claimed by the reagent pass, from the library of the run's chemistry.")
  })

  it("defers a line to its ion's claim, and says when no claim is recorded", () => {
    // A ladder rung's isotope line records no ladder of its own.
    const line = { reagent: { ion: 'H3O+(H2O)2', family: 'solvent', predicted_relative: 0.01 } }
    expect(sourceStatement(LINE, line).claim).toBe(
      'Claimed by the reagent pass as a line of the ion it claimed.'
    )
    // A derived row carries the batch's reading of the peak, and no reagent record.
    expect(sourceStatement(REAGENT, { batch_peak: { n_present: 4 } }).claim).toBe(
      "Read off the batch's peak, which does not record which claim took it."
    )
    expect(sourceStatement(REAGENT, null).claim).toBe(
      'The row does not record which claim took the peak.'
    )
  })

  it('names the ladder and the rung below on a ladder claim, and what shows an unnamed ion', () => {
    const provenance = {
      reagent: {
        ion: 'H3O+(H2O)2',
        family: 'solvent',
        observed: 'no work found names it; seen in every file of a set',
        ladder: { ladder: 'water', below: { ion: 'H3O+(H2O)', step: 'H2O' }, share: 0.0042 }
      }
    }
    const why = sourceStatement(REAGENT, provenance)
    expect(why.claim).toBe(
      'Claimed by the reagent pass on the water ladder: one H2O above H3O+(H2O).'
    )
    expect(why.notes).toEqual(['No work found names it; seen in every file of a set.'])
  })

  it('says an artifact is a sidelobe and no ion', () => {
    const why = sourceStatement(ARTIFACT, { artifact: { kind: 'ft_sidelobe' } })
    expect(why.title).toBe('Why artifact')
    expect(why.lead).toMatch(/^A side lobe: /)
    expect(why.lead).toContain('It is no ion, so nothing is assigned to it.')
    expect(why.claim).toContain('artifact pass')
    expect(sourceStatement(ARTIFACT, { artifact: { kind: 'other' } }).lead).toMatch(
      /^An artifact of the detector \(other\)\./
    )
    expect(sourceStatement(ARTIFACT, null).lead).toMatch(/^A peak the detector made\./)
  })

  it('says nothing of an analyte row', () => {
    expect(sourceStatement({ role: 'M0' }, null)).toBeNull()
  })
})

describe('sourceTiles', () => {
  it("measures a reagent row's mass, intensity, lines and the run's anchors", () => {
    const tiles = sourceTiles({
      row: REAGENT,
      provenance: LIBRARY,
      isotopes: ISOTOPES,
      model: MODEL
    })
    expect(tiles.map((t) => t.key)).toEqual(['mass', 'intensity', 'isotopes', 'anchor'])
    expect(tiles.map((t) => t.title)).toEqual([
      'Mass error',
      'Intensity',
      'Isotope lines found',
      'Calibration anchor'
    ])
    expect(tile(tiles, 'mass').value).toBe('0.42 ppm')
    expect(tile(tiles, 'mass').caption).toBe(`against Br- at m/z ${num.mz.format(78.91889)}`)
    expect(tile(tiles, 'intensity').value).toBe(num.peakIntensity.format(5.0e6))
    expect(tile(tiles, 'intensity').caption).toBe(
      "its share of the spectrum's signal is not recorded"
    )
    expect(tile(tiles, 'isotopes').value).toBe('1 of 1')
    const anchor = tile(tiles, 'anchor')
    expect(anchor.value).toBe('3 source lines')
    expect(anchor.caption).toBe("the run's mass centre at +0.30 ppm was fitted on them")
    expect(anchor.tooltip).toContain("on 3 of the source's own lines and 40 committed rows.")
    expect(anchor.tooltip).toContain('1 line brighter than the calibrant cap was left out.')
    expect(anchor.tooltip).toContain('Which lines anchored it is not recorded.')
  })

  it("reads a rung's share of the spectrum and a line's predicted share", () => {
    const rung = { reagent: { ion: 'X', ladder: { share: 0.0042 } } }
    expect(tile(sourceTiles({ row: REAGENT, provenance: rung }), 'intensity').caption).toBe(
      "0.42 % of the spectrum's signal"
    )
    const line = { reagent: { ion: 'Br-', predicted_relative: 0.97278 } }
    const tiles = sourceTiles({ row: LINE, provenance: line, model: MODEL })
    expect(tile(tiles, 'intensity').caption).toBe(
      "predicted at 97 % of the ion's monoisotopic peak"
    )
    // A line is measured against its own mass, and anchors no calibration.
    expect(tile(tiles, 'mass').caption).toBe("against the line's own exact mass")
    expect(tile(tiles, 'anchor').value).toBe('—')
    expect(tile(tiles, 'anchor').caption).toBe('an isotope line anchors no calibration')
  })

  it("reads a v1 model's source lines as the source's offset, not the run's centre", () => {
    const v1 = {
      version: 1,
      mu_ppm: -0.13,
      mu_source: 'fitted',
      reagent_mu_ppm: 0.25,
      anchors: { library_lines: 12, library_ions: 9, reagent_lines: 2 }
    }
    const anchor = tile(sourceTiles({ row: REAGENT, model: v1 }), 'anchor')
    expect(anchor.value).toBe('2 source lines')
    expect(anchor.caption).toBe(
      "the source's offset at +0.25 ppm was read on them, not the run's centre"
    )
    expect(anchor.tooltip).toContain(
      "The run read the source's own offset on 2 of the source's own lines."
    )
    expect(anchor.tooltip).toContain(
      "The run's mass centre was fitted on 12 of the library's lines, not on these."
    )
    // Where no centre was fitted on the library, the offset stands in for it.
    const stand = tile(
      sourceTiles({ row: REAGENT, model: { ...v1, mu_ppm: 0.25, mu_source: 'reagent' } }),
      'anchor'
    )
    expect(stand.caption).toBe(
      "the source's offset at +0.25 ppm on them stands in as the run's mass centre"
    )
    expect(stand.tooltip).toContain("that offset is the run's mass centre.")
    const unfitted = tile(
      sourceTiles({ row: REAGENT, model: { ...v1, mu_ppm: null, mu_source: 'none' } }),
      'anchor'
    )
    expect(unfitted.tooltip).toContain('The run measured no mass centre.')
    const none = tile(sourceTiles({ row: REAGENT, model: null }), 'anchor')
    expect(none.value).toBe('—')
    expect(none.caption).toBe('the run records no source lines among its anchors')
  })

  it('says when a v2 model measured no centre, and when no source line anchored it', () => {
    const open = { ...MODEL, mu_ppm: null }
    expect(tile(sourceTiles({ row: REAGENT, model: open }), 'anchor').caption).toBe(
      'no mass centre was measured on them'
    )
    const zero = { mu_ppm: 0.3, anchors: { commits: 40, source_lines: 0, source_capped: 0 } }
    const anchor = tile(sourceTiles({ row: REAGENT, model: zero }), 'anchor')
    expect(anchor.value).toBe('0 source lines')
    expect(anchor.caption).toBe('none anchored the run')
    expect(anchor.tooltip).toContain("read none of the source's own lines")
    const v1 = { mu_ppm: 0.3, mu_source: 'fitted', anchors: { reagent_lines: 0 } }
    expect(tile(sourceTiles({ row: REAGENT, model: v1 }), 'anchor').caption).toBe(
      'none anchored the run'
    )
  })

  it("keeps the isotope tile's caption hover and adds the abundance error's", () => {
    const isotopes = { ...ISOTOPES, captionTooltip: 'why the M0 reads -' }
    const iso = tile(
      sourceTiles({ row: REAGENT, isotopes, abundanceTooltip: 'abundance hover' }),
      'isotopes'
    )
    expect(iso.captionTooltip).toBe('why the M0 reads -')
    expect(iso.tooltip).toBe('t\nabundance hover')
  })

  it('dashes a reagent row with no m/z error', () => {
    const mass = tile(sourceTiles({ row: { ...REAGENT, mz_error_ppm: null } }), 'mass')
    expect(mass.value).toBe('—')
    expect(mass.caption).toBe('no m/z error recorded for this row')
  })

  it('dashes what an artifact has no reading of, and keeps its intensity', () => {
    const tiles = sourceTiles({ row: ARTIFACT, provenance: null, isotopes: ISOTOPES, model: MODEL })
    expect(tile(tiles, 'mass').value).toBe('—')
    expect(tile(tiles, 'mass').caption).toBe('no ion to measure against')
    expect(tile(tiles, 'intensity').value).toBe(num.peakIntensity.format(1.2e4))
    expect(tile(tiles, 'isotopes').value).toBe('—')
    expect(tile(tiles, 'anchor').caption).toBe('an artifact anchors nothing')
  })

  it('has no tiles for an analyte row', () => {
    expect(sourceTiles({ row: { role: 'M0' } })).toEqual([])
  })
})

describe('sourceAbsences', () => {
  it('says a reagent row has no tier contest, rivals or level', () => {
    const lines = sourceAbsences(REAGENT)
    expect(lines.map((l) => l.key)).toEqual(['tier', 'rivals', 'level'])
    expect(lines[0].text).toBe(
      'A reagent row commits no formula, so no evidence is weighed and no tier is read off it.'
    )
    expect(lines[1].text).toBe(
      "No formula contests the claim: the peak is the source's, not a candidate's."
    )
  })

  it('points to the displaced reading where the claim marked one', () => {
    const alternatives = [{ assigned_formula: 'CHBr', displaced_by_claim: true }]
    const [, rivals] = sourceAbsences(REAGENT, { alternatives })
    expect(rivals.text).toBe(
      'No formula contests the claim. The reading it displaced is under Alternatives.'
    )
  })

  it('names what the stages had read the peak as', () => {
    const provenance = {
      reagent: { ion: 'C6H7+', parent: {}, read_as: { formula: 'C6H6', ionization: '[M+H]+' } }
    }
    const [, rivals] = sourceAbsences(REAGENT, { alternatives: [], provenance })
    expect(rivals.text).toBe(
      'No formula contests the claim. The stages had read the peak as C6H6 [M+H]+ before the claim.'
    )
    const batch = {
      reagent: { ion: 'Br-', read_as: { formula: 'CHBr', ion_formula: 'CHBr-' } }
    }
    const alternatives = [{ assigned_formula: 'CHBr', displaced_by_claim: true }]
    expect(sourceAbsences(REAGENT, { alternatives, provenance: batch })[1].text).toBe(
      'No formula contests the claim. The stages had read the peak as CHBr before the claim. ' +
        'The reading it displaced is under Alternatives.'
    )
    // A reading with no formula (the peak was unassigned) names nothing.
    const blank = {
      reagent: { ion: 'Br-', read_as: { formula: null, ionization: '[M-H]-', tier: 'unassigned' } }
    }
    expect(sourceAbsences(REAGENT, { provenance: blank })[1].text).toBe(
      "No formula contests the claim: the peak is the source's, not a candidate's."
    )
  })

  it('does not call other readings of the m/z the displaced one', () => {
    // A derived row's batch candidates, or the runner-ups a batch-claimed line kept.
    const [, rivals] = sourceAbsences(REAGENT, { alternatives: [{ assigned_formula: 'CHBr' }] })
    expect(rivals.text).toBe(
      'The claim weighed no formula against it. ' +
        'Alternatives lists other readings of this m/z, which it was not weighed against.'
    )
  })

  it('adds the isotope pattern on an artifact row', () => {
    const lines = sourceAbsences(ARTIFACT)
    expect(lines.map((l) => l.key)).toEqual(['tier', 'rivals', 'level', 'pattern'])
    expect(lines[0].text).toMatch(/^An artifact row commits no formula/)
    expect(lines[1].text).toContain("the peak is the detector's")
  })

  it('has none for an analyte row', () => {
    expect(sourceAbsences({ role: 'M0' })).toEqual([])
  })
})
