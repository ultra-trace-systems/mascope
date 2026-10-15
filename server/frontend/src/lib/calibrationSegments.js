/**
 * How each scan range of a stitched sample came by its m/z calibration.
 *
 * A file whose method measures one chemistry as several scan ranges is
 * calibrated range by range: two ranges of one file read an ion up to a ppm
 * apart. The backend lists the ranges of the sample's polarity in the fit's
 * quality block, `quality.segments`, both in the fit the calibration dialog
 * previews and in the record a sample carries:
 *
 * - `source: "anchors"` - fitted on calibrants the range holds itself; its
 *   own `quality` block says on how many.
 * - `source: "overlap"` - it has no fit of its own, and takes the
 *   calibration of `origin` shifted by what both ranges read of the
 *   `shared_ions` in their overlap.
 * - `source: "borrowed"` - it has no fit of its own, and takes the
 *   calibration of `origin` as it is.
 *
 * `source` says how a range came by its calibration and not why it has no
 * fit of its own: that is in `note`, where the backend recorded one. A range
 * with no fit need not lack calibrants - the ones it holds may have failed
 * the fit's filters, or disagreed with each other - and a range takes
 * `origin`'s calibration unchanged also where the two share plenty of ions
 * but `origin` was not fitted itself. So the texts state what happened and
 * quote the recorded reason, and guess at neither.
 *
 * A sample that is not stitched has no `segments`, and nothing here says
 * anything about it.
 */

import { num } from '@/lib/formatters'

const signedPpm = (value) => `${num.mzCorrection.format(value)} ppm`

const lowerFirst = (text) => `${text.charAt(0).toLowerCase()}${text.slice(1)}`

// "not fitted on calibrants of its own", with the reason the fit recorded
// where it recorded one: the backend's sentence, as part of this one. A note
// can go on after a `;` to say what the range's own fit did next ("skipping
// calibration"), which this sentence says in its own words: what the range
// took instead.
const notFitted = (segment) => {
  const note = (segment.note ?? '').split(';')[0].trim().replace(/\.$/, '')
  const reason = note ? ` (${lowerFirst(note)})` : ''
  return `not fitted on calibrants of its own${reason}`
}

/**
 * The scan ranges of a fit or of a calibration record.
 *
 * @param {object|null|undefined} mzCalibration - a fit, or `sample.mz_calibration`
 * @returns {object[]} `quality.segments`, empty where there are none
 */
export function calibrationSegments(mzCalibration) {
  return mzCalibration?.quality?.segments ?? []
}

/**
 * One scan range's calibration, as a sentence.
 *
 * The correction is how far the range's factor moves its m/z from what the
 * instrument recorded.
 *
 * @param {object} segment - an entry of `quality.segments`
 * @returns {string}
 */
export function segmentCalibrationText(segment) {
  const correction = signedPpm((segment.calibration_factor - 1) * 1e6)
  if (segment.source === 'overlap') {
    return (
      `${segment.label}: ${notFitted(segment)}; takes the calibration of ${segment.origin} ` +
      `across the ${segment.shared_ions} ions both ranges measure (${correction}).`
    )
  }
  if (segment.source === 'borrowed') {
    return (
      `${segment.label}: ${notFitted(segment)}; takes the calibration of ${segment.origin} ` +
      `unchanged (${correction}).`
    )
  }
  const points = segment.quality?.n_points
  const fitted =
    points == null ? 'its own calibrants' : `${points} calibrant${points === 1 ? '' : 's'}`
  return `${segment.label}: fitted on ${fitted} (${correction}).`
}

// The quality issues of a fit or record that are its scan ranges' (an issue
// names its range under `segment`), by range key
const issuesByRange = (mzCalibration) => {
  const keys = new Set(calibrationSegments(mzCalibration).map((segment) => segment.key))
  const byRange = new Map()
  for (const issue of mzCalibration?.quality_issues ?? []) {
    if (issue.segment == null || !keys.has(issue.segment)) continue
    byRange.set(issue.segment, [...(byRange.get(issue.segment) ?? []), issue])
  }
  return byRange
}

// An issue's message as its range's line says it: the backend opens it with
// the range's label, which the line already starts with
const withoutLabel = (message, label) =>
  message.startsWith(`${label}: `) ? message.slice(label.length + 2) : message

// The issue whose sentence says what the range's own does - on how many
// calibration points it was fitted and how far that moves its axis - and so
// stands in its place
const SAYS_THE_FIT = 'points'

/**
 * The scan ranges of a fit or record as the calibration dialog lists them.
 *
 * Each line is the range's sentence, followed by why the range misses the
 * quality bar where it does, so that a range is named once, on its own line,
 * and not again in the warning above the list. A fit on too few points to
 * vouch for its correction is said by the issue alone, which names both. A
 * line is flagged - set in
 * the warning colour - for a range below the bar, and for one that took
 * another range's calibration unchanged, which nothing the range measured
 * checked. One that took it across an overlap was put on the m/z of the ions
 * both ranges measure, and is not.
 *
 * @param {object|null|undefined} mzCalibration - a fit, or `sample.mz_calibration`
 * @returns {{key: string, text: string, flagged: boolean}[]} empty for a
 *   sample that is not stitched
 */
export function segmentListItems(mzCalibration) {
  const byRange = issuesByRange(mzCalibration)
  return calibrationSegments(mzCalibration).map((segment) => {
    const issues = byRange.get(segment.key) ?? []
    const fit = issues.find((issue) => issue.code === SAYS_THE_FIT)
    const said = fit
      ? `${segment.label}: ${lowerFirst(withoutLabel(fit.message, segment.label))}`
      : segmentCalibrationText(segment)
    const others = issues
      .filter((issue) => issue !== fit)
      .map((issue) => withoutLabel(issue.message, segment.label))
    return {
      key: segment.key,
      text: [said, ...others].join(' '),
      flagged: segment.source === 'borrowed' || issues.length > 0
    }
  })
}

/**
 * A fit's or record's quality issues, split by where the dialog shows them.
 *
 * @param {object|null|undefined} mzCalibration - a fit, or `sample.mz_calibration`
 * @returns {{own: object[], ranges: number}} `own`: the issues of the fit as a
 *   whole, and any that name a range the fit does not list, for the warning;
 *   `ranges`: how many listed ranges miss the bar, whose issues are on their
 *   lines (`segmentListItems`)
 */
export function splitQualityIssues(mzCalibration) {
  const byRange = issuesByRange(mzCalibration)
  const onLines = new Set([...byRange.values()].flat())
  return {
    own: (mzCalibration?.quality_issues ?? []).filter((issue) => !onLines.has(issue)),
    ranges: byRange.size
  }
}

/**
 * For the sample's badge: which scan ranges run on another range's calibration.
 *
 * @param {object|null|undefined} mzCalibration - `sample.mz_calibration`
 * @returns {string} A sentence with a leading space, or an empty string when
 *   every range was fitted on its own calibrants or the sample is not stitched.
 *   It does not say why a range has no fit of its own: the calibration dialog
 *   lists the ranges of the record with the reason recorded for each, and
 *   those of the fit it previews beside them where the two differ.
 */
export function carriedSegmentsText(mzCalibration) {
  const carried = calibrationSegments(mzCalibration).filter(
    (segment) => segment.source !== 'anchors'
  )
  if (carried.length === 0) {
    return ''
  }
  const labels = carried.map((segment) => segment.label).join('; ')
  return carried.length === 1
    ? ` Scan range ${labels} was not fitted on calibrants of its own and takes a neighbouring range's calibration.`
    : ` Scan ranges ${labels} were not fitted on calibrants of their own and take a neighbouring range's calibration.`
}
