import { tierMeta } from '@/lib/tiers'

/**
 * What the inspector says of a batch peak's identity measured on this sample
 * before "use this" pins it (design note step 3.4c).
 *
 * A derived row's alternatives are the other identities the batch peak's
 * members carried, each measured in the sample that brought it and none in
 * this one. The server measures one on this sample's member on request
 * (`/batch-peaks/batch/{id}/measure-candidate`) the way the pin then does, so
 * the fit, the lines, the plausibility and the tier read here are the ones the
 * pin writes on this member. `takes_member` false says the pin would leave
 * this sample reading what it does now, and `blocked_reason` why.
 */

const percent = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0
})
const pct = (value) => (value != null && !Number.isNaN(value) ? percent.format(value) : '—')

/** The tier's short name, as its chip says it; null for none. */
export const measuredTierLabel = (measurement) =>
  measurement?.tier ? tierMeta(measurement.tier).label : null

/** "2 of 3 lines", or null when the ion predicted none. */
export function measuredLines(measurement) {
  const lines = measurement?.lines ?? []
  if (!lines.length) return null
  const found = measurement.lines_found ?? lines.filter((line) => line.sample_peak_id).length
  return `${found} of ${lines.length} line${lines.length === 1 ? '' : 's'}`
}

/** What the member reads now, in one line; null when it reads nothing. */
export function currentReading(measurement) {
  const current = measurement?.current
  if (!current?.assigned_formula) return null
  const parts = [current.assigned_formula]
  if (current.fit_score != null) parts.push(`fit ${pct(current.fit_score)}`)
  if (current.evidence != null) parts.push(`evidence ${pct(current.evidence)}`)
  if (current.tier) parts.push(tierMeta(current.tier).label)
  return parts.join(', ')
}

/**
 * The tooltip lines a measurement adds to its alternative, after the fit and
 * the m/z error the entry already shows.
 */
export function measuredTooltipLines(measurement) {
  if (!measurement) return []
  const lines = []
  if (measurement.evidence != null) lines.push(`evidence: ${pct(measurement.evidence)}`)
  if (measurement.takes_member) {
    lines.push(`tier if pinned: ${tierMeta(measurement.tier).description}`)
  }
  const found = measuredLines(measurement)
  if (found) {
    // A label once: an isotopologue's fine structure shares its nominal one.
    const labels = [
      ...new Set(
        (measurement.lines ?? [])
          .filter((line) => line.sample_peak_id)
          .map((line) => line.isotope_label)
          .filter(Boolean)
      )
    ]
    lines.push(`lines in this spectrum: ${found}${labels.length ? ` (${labels.join(', ')})` : ''}`)
  }
  if (!measurement.takes_member && measurement.blocked_reason) {
    lines.push(`if pinned: ${measurement.blocked_reason}`)
  }
  const now = currentReading(measurement)
  if (now) lines.push(`this sample now: ${now}`)
  return lines
}
