// The segments of a stitched spectrum, for the views that show them.
//
// A file whose method measures one chemistry as several scan ranges is read as
// one spectrum per polarity: each m/z taken from the scan range that owns it.
// Nothing is rescaled where two ranges meet, so the signal can step at a
// boundary and a peak's intensity is what its own range measured. The server
// says where the boundaries are (`runs`, in m/z order, each with the positions
// its samples take in the spectrum's arrays) and what the ranges are
// (`segments`, each with its `index`, which is what a peak's `segment` is).
// Both are null for a spectrum of one range, which is nearly every one.

// One colour per segment, the first the green a spectrum has always been
// drawn in, so a spectrum of one range looks as it did. Two runs that meet are
// never of one segment - they would be one run - so neighbours always differ.
export const SEGMENT_COLORS = ['green', '#1f8a9e', '#8a9a1c', '#3aa58a', '#b07f1d', '#5a7fc7']

// A segment's place among the sample's segments, which is what picks its colour.
export function segmentOrder(segments, index) {
  const order = (segments ?? []).findIndex((segment) => segment.index === index)
  return order < 0 ? 0 : order
}

export function segmentColor(segments, index) {
  return SEGMENT_COLORS[segmentOrder(segments, index) % SEGMENT_COLORS.length]
}

export function segmentOf(segments, index) {
  return (segments ?? []).find((segment) => segment.index === index) ?? null
}

// A segment as a cell shows it: its scan range, without the unit every row
// would repeat.
export function segmentName(segments, index) {
  const segment = segmentOf(segments, index)
  return segment ? segment.label.replace(/^m\/z /, '') : ''
}

// A segment in full, for a tooltip: its range, and how it was measured where
// the server knows.
export function segmentDetail(segments, index) {
  const segment = segmentOf(segments, index)
  if (!segment) return ''
  const parts = [segment.label]
  if (segment.scans != null) parts.push(`${segment.scans} scan${segment.scans === 1 ? '' : 's'}`)
  if (segment.microscans != null) {
    parts.push(`${segment.microscans} microscan${segment.microscans === 1 ? '' : 's'}`)
  }
  return parts.join(' · ')
}

// The signal cut where the map cuts it: one stretch per run that holds any
// sample, or the whole signal as one where nothing is stitched.
export function signalStretches(spectrum) {
  if (!spectrum) return []
  const { mz, intensity, runs } = spectrum
  if (!runs?.length) return [{ segment: null, mz, intensity }]
  return runs
    .filter((run) => run.to > run.from)
    .map((run) => ({
      segment: run.segment,
      mz: mz.slice(run.from, run.to),
      intensity: intensity.slice(run.from, run.to)
    }))
}

// Where one run ends and the next begins, in m/z. An edge two runs share is
// one boundary; a gap between two runs has one on either side.
export function runBoundaries(runs) {
  if (!runs || runs.length < 2) return []
  const edges = new Set()
  runs.forEach((run, position) => {
    if (position > 0) edges.add(run.mz_lower)
    if (position < runs.length - 1) edges.add(run.mz_upper)
  })
  return [...edges].sort((a, b) => a - b)
}

// The boundaries as Plotly layout shapes: dotted lines the height of the plot.
export function boundaryShapes(runs) {
  return runBoundaries(runs).map((mz) => ({
    type: 'line',
    xref: 'x',
    yref: 'paper',
    x0: mz,
    x1: mz,
    y0: 0,
    y1: 1,
    layer: 'below',
    line: { color: '#88888899', width: 1, dash: 'dot' }
  }))
}
