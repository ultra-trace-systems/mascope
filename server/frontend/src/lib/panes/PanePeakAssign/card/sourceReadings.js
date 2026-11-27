/**
 * What the inspector card reads off a row the source or the detector made:
 * a *reagent* row, an ion of the run's chemistry that a claim took before the
 * stages searched the peak (or, for a fragment, after they committed its
 * parent), and an *artifact* row, a peak the detector made that is no ion at
 * all. Neither commits a formula, so the card's analyte reading - the
 * evidence bar, the tier's reasons, the confidence, the level - has nothing
 * to read. These functions say what the row is, what was measured of it, and
 * why the rest is not there, from what the row and its run record
 * (`reagent_pass.py`, `artifact_pass.py`); nothing is computed here that the
 * server did not record.
 */

import { formatIsotopeLabel } from '@/lib/chem'
import { num } from '@/lib/formatters'
import { TILE_TITLES } from './cardReadings.js'

/** The roles the card reads as the source's or the detector's. */
export const SOURCE_ROLES = Object.freeze(['reagent', 'artifact'])

/** The titles of the tiles a reagent or artifact row adds to the four. */
export const SOURCE_TILE_TITLES = Object.freeze({
  ...TILE_TITLES,
  intensity: 'Intensity',
  anchor: 'Calibration anchor'
})

/** Where an ion the reagent pass claims comes from, as a reader knows it
 *  (`mascope_tools.composition.reagents`, the families). */
const FAMILIES = Object.freeze({
  reagent: "the reagent's own ladder",
  air: "the discharge's own ions in air",
  calibrant: 'the calibrant beam',
  solvent: 'the solvent clusters the source builds',
  fragment: "a fragment of an analyte's ion"
})

/** The artifact kinds the artifact pass names (`artifact_pass.py`). */
const ARTIFACT_KINDS = Object.freeze({
  ft_sidelobe:
    'A side lobe: the ringing a Fourier-transform spectrum leaves beside a very intense ' +
    'peak, a few tens of ppm away and a fraction of a percent of its height.'
})

const isRecord = (value) => value != null && typeof value === 'object' && !Array.isArray(value)
const isCount = (value) => Number.isInteger(value) && value >= 0
const isNumber = (value) => typeof value === 'number' && Number.isFinite(value)
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
// A share as small as a source line's of the whole spectrum ("0.42 %",
// "0.0031 %"): two significant digits, never rounded to zero.
const smallShare = (value) => `${Number((value * 100).toPrecision(2))} %`

/**
 * The row's role where it is the source's or the detector's, else null.
 *
 * @param {object|null} row - a ledger row
 * @returns {'reagent'|'artifact'|null}
 */
export function sourceRole(row) {
  return SOURCE_ROLES.includes(row?.role) ? row.role : null
}

/** Whether a row is a heavier line of its ion, not the ion's monoisotopic peak. */
const isLine = (row) => Boolean(row?.isotope_label) && row.isotope_label !== 'M0'

/** The record a reagent row keeps of its claim, or an empty one. */
const reagentRecord = (provenance) => (isRecord(provenance?.reagent) ? provenance.reagent : {})

/** The ion a reagent row names: its claim's label, else its ion formula. */
function ionName(row, record) {
  return record.ion || row?.ion_formula || 'a source ion'
}

/**
 * Which claim took the peak, in one sentence: the batch's word (a known
 * species decided present, a rung of its water ladder), the fragment claim,
 * the reagent pass on a ladder or on its library, or, on an isotope line the
 * pass took with its ion, the ion's claim. A row that records no claim (a
 * derived row, which carries the batch's reading of the peak) says so.
 */
function claimSentence(row, record, provenance) {
  if (record.reason === 'batch_known_species') {
    const decision = isRecord(record.batch_known_species) ? record.batch_known_species : {}
    const counts =
      isCount(decision.present) && isCount(decision.files)
        ? ` in ${decision.present} of ${plural(decision.files, 'sample')}`
        : ''
    return `Claimed on the batch's word: Decide known species found it present${counts}.`
  }
  if (record.reason === 'batch_water_ladder') {
    return "Claimed on the batch's word: a rung of the batch's water ladder that held in this sample's segment."
  }
  // Only the fragment claim records a parent: the library's own fragment ions
  // (a calibrant's, say) carry the fragment kind and no parent.
  if (isRecord(record.parent)) {
    const parent = record.parent
    const of = parent.label || parent.formula || 'its parent'
    const ratio =
      isNumber(parent.ratio) && isNumber(parent.max_ratio)
        ? `: its height is ${smallShare(parent.ratio)} of the parent's, at most ${smallShare(parent.max_ratio)}`
        : ''
    return `Claimed by the fragment claim, after the stages committed ${of}${ratio}.`
  }
  if (isRecord(record.ladder)) {
    const ladder = record.ladder
    const below = isRecord(ladder.below) ? ladder.below : null
    const name = ladder.ladder ? ` on the ${ladder.ladder} ladder` : ' on a ladder'
    const step = below?.ion && below?.step ? `: one ${below.step} above ${below.ion}` : ''
    return `Claimed by the reagent pass${name}${step}.`
  }
  if (!record.ion) {
    return isRecord(provenance?.batch_peak)
      ? "Read off the batch's peak, which does not record which claim took it."
      : 'The row does not record which claim took the peak.'
  }
  // A line records no claim of its own: the pass takes it with its ion, so
  // the claim is the ion's, on the library or on a ladder.
  if (isLine(row)) return 'Claimed by the reagent pass as a line of the ion it claimed.'
  return "Claimed by the reagent pass, from the library of the run's chemistry."
}

/**
 * The Why box of a reagent or artifact row: its title, what the row is, which
 * claim took the peak, and what the claim rests on.
 *
 * @param {object} row - the ledger row
 * @param {object|null} provenance - the row's provenance, once its detail is in
 * @returns {{title: string, lead: string, claim: string, notes: string[]}|null}
 */
export function sourceStatement(row, provenance) {
  const role = sourceRole(row)
  if (role === 'artifact') {
    const kind = isRecord(provenance?.artifact) ? provenance.artifact.kind : null
    const what = ARTIFACT_KINDS[kind] ?? (kind ? `An artifact of the detector (${kind}).` : null)
    return {
      title: 'Why artifact',
      lead: `${what ?? 'A peak the detector made.'} It is no ion, so nothing is assigned to it.`,
      claim: 'Claimed by the artifact pass, which reads Fourier-transform spectra only.',
      notes: []
    }
  }
  if (role !== 'reagent') return null
  const record = reagentRecord(provenance)
  const ion = ionName(row, record)
  const from = FAMILIES[record.family]
  const subject = isLine(row) ? `The ${formatIsotopeLabel(row.isotope_label)} line of ${ion}` : ion
  const lead =
    `${subject} is an ion of the run's chemistry` +
    (from ? `, from ${from}` : '') +
    ': the source made it, not the sample.'
  const notes = []
  const references = Array.isArray(record.references) ? record.references.length : 0
  if (references) notes.push(`Named in ${plural(references, 'published work')}.`)
  if (record.observed) {
    const observed = String(record.observed)
    notes.push(`${observed.charAt(0).toUpperCase()}${observed.slice(1)}.`)
  }
  return { title: 'Why reagent', lead, claim: claimSentence(row, record, provenance), notes }
}

/**
 * The mass tile: the m/z error against the ion's exact mass, which the claim
 * measured; a dash on an artifact, which names no ion to measure against.
 */
function massTile(row, record, role) {
  if (role === 'artifact') {
    return {
      key: 'mass',
      title: SOURCE_TILE_TITLES.mass,
      value: '—',
      caption: 'no ion to measure against',
      tooltip: 'An artifact names no ion, so it has no exact mass and no m/z error'
    }
  }
  const error = row.mz_error_ppm
  if (!isNumber(error)) {
    return {
      key: 'mass',
      title: SOURCE_TILE_TITLES.mass,
      value: '—',
      caption: 'no m/z error recorded for this row',
      tooltip: 'Not measured: the run recorded no m/z error for this row'
    }
  }
  const exact = isNumber(record.mz) && !isLine(row) ? ` at m/z ${num.mz.format(record.mz)}` : ''
  return {
    key: 'mass',
    title: SOURCE_TILE_TITLES.mass,
    value: `${num.mzError.format(error)} ppm`,
    caption: isLine(row)
      ? "against the line's own exact mass"
      : `against ${ionName(row, record)}${exact}`,
    tooltip: 'Measured minus exact m/z of the ion, in ppm'
  }
}

/**
 * The intensity tile: the peak's height, and its share of the spectrum's
 * signal where the claim recorded one (a ladder's rung), or an isotope line's
 * predicted share of its ion's monoisotopic peak.
 */
function intensityTile(row, record) {
  const intensity = row.sample_peak_intensity
  const value = isNumber(intensity) ? num.peakIntensity.format(intensity) : '—'
  const share = isRecord(record.ladder) ? record.ladder.share : null
  let caption = "its share of the spectrum's signal is not recorded"
  let tooltip =
    "The peak's intensity. The run records no share of the spectrum's signal for this row"
  if (isNumber(share)) {
    caption = `${smallShare(share)} of the spectrum's signal`
    tooltip = "The peak's intensity, and its share of the spectrum's signal as the ladder read it"
  } else if (isNumber(record.predicted_relative) && isLine(row)) {
    caption = `predicted at ${smallShare(record.predicted_relative)} of the ion's monoisotopic peak`
    tooltip =
      "The peak's intensity, and the share of the monoisotopic peak the ion's pattern predicts"
  }
  return { key: 'intensity', title: SOURCE_TILE_TITLES.intensity, value, caption, tooltip }
}

/**
 * The calibration tile: how many of the source's own lines the run's mass
 * calibration read, and what it read on them. The run records the count, not
 * which lines: a line brighter than the calibrant cap is left out.
 *
 * A refitted (v2) model fitted its centre on the source lines and the
 * committed rows together. A first (v1) model, which a run keeps when its
 * refit is not adopted, fitted its centre on the library's lines; it read the
 * source lines on their own for the source's offset, which stands in as the
 * centre only where its `mu_source` is `reagent`.
 */
function anchorTile(row, role, model) {
  const dash = (caption, tooltip) => ({
    key: 'anchor',
    title: SOURCE_TILE_TITLES.anchor,
    value: '—',
    caption,
    tooltip
  })
  if (role === 'artifact') {
    return dash(
      'an artifact anchors nothing',
      'An artifact has no exact mass, so no calibration reads it'
    )
  }
  if (isLine(row)) {
    return dash(
      'an isotope line anchors no calibration',
      "The run fits its mass calibration on the source's monoisotopic lines: an isotope line is the same ion on a weaker peak"
    )
  }
  const anchors = isRecord(model?.anchors) ? model.anchors : {}
  const refit = isCount(anchors.source_lines)
  const lines = refit ? anchors.source_lines : anchors.reagent_lines
  if (!isCount(lines)) {
    return dash(
      'the run records no source lines among its anchors',
      "The run's mass calibration records no count of the source's lines it was fitted on"
    )
  }
  const ppm = (value) => `${num.mzCorrection.format(value)} ppm`
  const capped =
    isCount(anchors.source_capped) && anchors.source_capped > 0 ? anchors.source_capped : 0
  const notes = [
    capped
      ? `${plural(capped, 'line')} brighter than the calibrant cap ${capped === 1 ? 'was' : 'were'} left out.`
      : null,
    'Which lines anchored it is not recorded.'
  ]
  let caption
  let lead
  if (!lines) {
    caption = 'none anchored the run'
    lead = "The run's mass calibration read none of the source's own lines."
  } else if (refit) {
    caption = isNumber(model.mu_ppm)
      ? `the run's mass centre at ${ppm(model.mu_ppm)} was fitted on them`
      : 'no mass centre was measured on them'
    lead =
      `The run fitted its mass calibration on ${plural(lines, "of the source's own line")}` +
      (isCount(anchors.commits) ? ` and ${plural(anchors.commits, 'committed row')}.` : '.')
  } else {
    const offset = isNumber(model.reagent_mu_ppm) ? ` at ${ppm(model.reagent_mu_ppm)}` : ''
    caption =
      model.mu_source === 'reagent'
        ? `the source's offset${offset} on them stands in as the run's mass centre`
        : `the source's offset${offset} was read on them, not the run's centre`
    const library = isCount(anchors.library_lines)
      ? ` on ${plural(anchors.library_lines, "of the library's line")}`
      : " on the library's lines"
    const centre = {
      reagent:
        "With no centre fitted on the library's lines, that offset is the run's mass centre.",
      fitted: `The run's mass centre was fitted${library}, not on these.`
    }
    lead =
      `The run read the source's own offset on ${plural(lines, "of the source's own line")}. ` +
      (centre[model.mu_source] ?? 'The run measured no mass centre.')
  }
  return {
    key: 'anchor',
    title: SOURCE_TILE_TITLES.anchor,
    value: plural(lines, 'source line'),
    caption,
    tooltip: [lead, ...notes].filter(Boolean).join('\n')
  }
}

/**
 * The tiles of a reagent or artifact row, in the card's order: its mass, its
 * intensity, its isotope lines, the calibration it anchors, and the batch.
 *
 * @param {object} options
 * @param {object} options.row - the ledger row
 * @param {object|null} options.provenance - its provenance, once its detail is in
 * @param {{value: string, caption: string, captionTooltip: string|null, tooltip: string}} options.isotopes -
 *   the card's isotope tile (`isotopeTile`) of the row's family
 * @param {string} [options.abundanceTooltip] - what the isotope tile's
 *   abundance error measures, added to its hover as an analyte's tile does
 * @param {object|null} options.model - the run's measurement model
 * @returns {Array<{key: string, title: string, value: string, caption: string, captionTooltip?: string|null, tooltip: string}>}
 */
export function sourceTiles({ row, provenance, isotopes, abundanceTooltip, model }) {
  const role = sourceRole(row)
  if (!role) return []
  const record = reagentRecord(provenance)
  const isotopeTile =
    role === 'artifact'
      ? {
          key: 'isotopes',
          title: SOURCE_TILE_TITLES.isotopes,
          value: '—',
          caption: 'no ion, so no isotope lines',
          tooltip: 'An artifact is no ion: it has no isotope pattern to find lines of'
        }
      : {
          key: 'isotopes',
          title: SOURCE_TILE_TITLES.isotopes,
          value: isotopes?.value ?? '—',
          caption: isotopes?.caption ?? '',
          captionTooltip: isotopes?.captionTooltip ?? null,
          tooltip: [isotopes?.tooltip, abundanceTooltip].filter(Boolean).join('\n')
        }
  return [
    massTile(row, record, role),
    intensityTile(row, record),
    isotopeTile,
    anchorTile(row, role, model)
  ]
}

/**
 * What the stages had read the peak as before the claim took it, where the
 * claim recorded a formula: the fragment claim and a claim on the batch's word
 * keep it under `provenance.reagent.read_as`.
 */
function readAsName(provenance) {
  const read = reagentRecord(provenance).read_as
  if (!isRecord(read)) return null
  const formula = read.formula || null
  if (!formula) return null
  return read.ionization ? `${formula} ${read.ionization}` : formula
}

/**
 * The *No rivals* line. A claim weighs no formula against the source's ion;
 * where it displaced a reading, that reading is listed under Alternatives
 * (the `displaced_by_claim` entry) and named from `read_as`. Other
 * alternatives (a derived row's batch candidates, a line's old runner-ups)
 * are readings the claim was not weighed against.
 */
function rivalsText(role, alternatives, provenance) {
  const displaced = alternatives.some((alt) => alt?.displaced_by_claim === true)
  const readAs = role === 'reagent' ? readAsName(provenance) : null
  const owner = role === 'artifact' ? "detector's" : "source's"
  const before = readAs ? ` The stages had read the peak as ${readAs} before the claim.` : ''
  if (displaced) {
    return `No formula contests the claim.${before} The reading it displaced is under Alternatives.`
  }
  if (alternatives.length) {
    // A derived row's batch candidates, or the runner-ups a line kept: other
    // readings of the m/z, which the claim was never weighed against.
    return (
      `The claim weighed no formula against it.${before} ` +
      'Alternatives lists other readings of this m/z, which it was not weighed against.'
    )
  }
  if (readAs) return `No formula contests the claim.${before}`
  return `No formula contests the claim: the peak is the ${owner}, not a candidate's.`
}

/**
 * One line for each part of an analyte's card a reagent or artifact row has
 * no reading for, in place of a blank: the tier contest, the rivals, the
 * identification level, and on an artifact the isotope pattern.
 *
 * @param {object} row - the ledger row
 * @param {{alternatives?: Array<object>, provenance?: object|null}} [options] -
 *   the alternatives the card lists, among which a claim marks the reading it
 *   displaced, and the row's provenance, which records what it was read as
 * @returns {Array<{key: string, title: string, text: string}>}
 */
export function sourceAbsences(row, { alternatives = [], provenance = null } = {}) {
  const role = sourceRole(row)
  if (!role) return []
  const what = role === 'artifact' ? 'An artifact row' : 'A reagent row'
  const lines = [
    {
      key: 'tier',
      title: 'No tier contest',
      text: `${what} commits no formula, so no evidence is weighed and no tier is read off it.`
    },
    {
      key: 'rivals',
      title: 'No rivals',
      text: rivalsText(role, alternatives ?? [], provenance)
    },
    {
      key: 'level',
      title: 'No identification level',
      text: 'A level is stated of a formula, and this row commits none.'
    }
  ]
  if (role === 'artifact') {
    lines.push({
      key: 'pattern',
      title: 'No isotope pattern',
      text: 'An artifact is no ion, so it has no isotope lines.'
    })
  }
  return lines
}
