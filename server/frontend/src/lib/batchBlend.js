/**
 * What a batch peak's probable-blend reason says, in words.
 *
 * The consensus (step 4.8 of the assignment plan, `batch_blend.py`) tests
 * the two formulas its vote ranked first: where their members part cleanly
 * along intensity - one formula on the bright samples, the other on the dim
 * ones - or along acquisition time - one early, the other late - two
 * chemistries share the trace, and the batch peak carries the reason under
 * `blend`: the two `formulas` (the consensus first), their `ions`, the `n`
 * members of each at candidate or better, the `axis` that parts them, and
 * each axis' reading under `intensity` (`separation`, `p`, `brighter`) and
 * `time` (`runs`, `expected_runs`, `p`, `earlier`), null where untested.
 * A reason only: the tier and the consensus stand as the vote left them.
 */

const AXIS_WORDS = { intensity: 'intensity', time: 'acquisition time' }

/** The blend reason a batch peak carries, or null. */
export function blendOf(row) {
  const record = row?.blend
  return record && Array.isArray(record.formulas) && record.formulas.length === 2 ? record : null
}

const p = (value) => (value < 0.001 ? 'p < 0.001' : `p = ${value.toPrecision(2)}`)

/** How one axis parts the two formulas, in a sentence; empty where it does
 *  not. */
function axisSentence(record, axis) {
  const reading = record[axis]
  if (!reading?.separates) return ''
  const [first, second] = record.formulas
  if (axis === 'intensity') {
    const bright = reading.brighter ?? first
    const dim = bright === first ? second : first
    const share = Math.round(Math.max(reading.separation, 1 - reading.separation) * 100)
    return (
      `${bright} holds the bright samples and ${dim} the dim ones: ` +
      `${share}% of their pairs order that way (${p(reading.p)}).`
    )
  }
  const early = reading.earlier ?? first
  const late = early === first ? second : first
  return (
    `${early} holds the early samples and ${late} the late ones: ` +
    `${reading.runs} runs in acquisition order, where interleaved they would ` +
    `give ${Math.round(reading.expected_runs)} (${p(reading.p)}).`
  )
}

/** The mark's short account. */
export function blendSummary(record) {
  if (!record) return ''
  const [first, second] = record.formulas
  return `Probable blend of ${first} and ${second}, parted by ${AXIS_WORDS[record.axis] ?? record.axis}`
}

/** The mark's tooltip: the two formulas with their ions and members, how
 *  each axis parts them, and what the reason does not do. */
export function blendTooltip(record) {
  if (!record) return ''
  const [first, second] = record.formulas
  const [ionFirst, ionSecond] = record.ions ?? []
  const [nFirst, nSecond] = record.n ?? []
  const side = (formula, ion, n) =>
    `${formula}${ion ? ` (${ion})` : ''}${n != null ? ` in ${n} sample${n === 1 ? '' : 's'}` : ''}`
  const axes = ['intensity', 'time']
    .map((axis) => axisSentence(record, axis))
    .filter(Boolean)
    .join(' ')
  return (
    `Probable blend: two compounds may share this peak. ` +
    `The samples read ${side(first, ionFirst, nFirst)} and ${side(second, ionSecond, nSecond)}. ` +
    `${axes} ` +
    'The consensus and its tier are unchanged; check the two before trusting either.'
  )
}
