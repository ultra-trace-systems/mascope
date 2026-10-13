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

// How close the facing edges of two runs lie when the two meet in the map.
//
// A run's edges are where its own range's samples and peaks lie: the server
// places each run on the file's axis by that range's m/z calibration. Two
// ranges of one file are calibrated some parts per million apart, so an edge
// two runs share arrives as two m/z that far apart, a gap or an overlap far
// too narrow to show. A stretch no run owns is as wide as the gap between two
// scan ranges of the method: a whole m/z or more, hundreds of ppm anywhere
// in a spectrum.
export const SHARED_EDGE_PPM = 50

// The runs as they are drawn: an edge two of them share is one m/z, the
// lower run's, so that it is one boundary and both runs are drawn up to it.
export function runsAsDrawn(runs) {
  if (!runs?.length) return []
  const drawn = runs.map((run) => ({ ...run }))
  for (let at = 1; at < drawn.length; at++) {
    const edge = drawn[at - 1].mz_upper
    if (Math.abs(drawn[at].mz_lower - edge) <= edge * SHARED_EDGE_PPM * 1e-6) {
      drawn[at].mz_lower = edge
    }
  }
  return drawn
}

// How many of the steps next to a run's end are read for how far apart its
// samples lie there.
const STEPS_READ = 4

// The spacing of a run's samples at one of its ends: the narrowest of the few
// steps next to it. A profile is stored in clusters around its peaks, so the
// step right at an end can be the empty stretch between two clusters, and the
// steps inside a cluster are the narrow ones. Null for a run of fewer than two
// samples, which has no step.
function stepAt(mz, upper) {
  let narrowest = null
  for (let back = 0; back < Math.min(STEPS_READ, mz.length - 1); back++) {
    const at = upper ? mz.length - 1 - back : back + 1
    const step = mz[at] - mz[at - 1]
    if (narrowest === null || step < narrowest) narrowest = step
  }
  return narrowest
}

// The m/z at which a run's line is carried on at zero from its outermost
// sample to one of the run's bounds, in the order met going outward.
//
// A stored profile has samples only around its peaks, with a zero just outside
// each cluster, so that the line between two clusters is the baseline. A run's
// outermost cluster has that baseline cut off at the boundary, and the line
// would stop short of it: an empty stretch that reads as m/z nobody measured.
// A sample at zero is already on the baseline, which goes on to the bound. One
// above it is the end of a range's own profile, which has no zero outside it:
// the line comes down one sample step out, as it does after every cluster
// inside a run, and goes on from there. Nothing is added where the samples
// come up to the bound, which is a peak the boundary cuts: its two halves are
// what two ranges measured, and a zero between them would be a dip that is
// not there. A lone sample above zero has no step to say which it is, and is
// left as it is. Nor is anything added where the samples lie past the bound,
// as the first of a run can past an edge it shares with the run below, which
// is drawn at that run's (`runsAsDrawn`).
function baselineTo(mz, intensity, bound, upper) {
  if (!mz.length) return [bound]
  const edge = upper ? mz.length - 1 : 0
  const short = upper ? bound - mz[edge] : mz[edge] - bound
  if (short <= 0) return []
  if (intensity[edge] === 0) return [bound]
  const step = stepAt(mz, upper)
  if (step === null || short <= step) return []
  return [upper ? mz[edge] + step : mz[edge] - step, bound]
}

// The signal cut where the map cuts it: one stretch per run, or the whole
// signal as one where nothing is stitched.
//
// Each stretch is drawn out along the baseline to the bounds of its run (see
// `baselineTo`), so that two runs that meet are drawn up to their boundary
// from either side, and the only m/z left empty is what no run owns. A run
// with no sample at all is its baseline from bound to bound. That holds for
// the bounds that lie among the samples shown: the ends of the signal stay
// where its samples end, as those of a spectrum of one range do, and a run
// outside the part shown is left out.
export function signalStretches(spectrum) {
  if (!spectrum) return []
  const { mz, intensity, runs } = spectrum
  if (!runs?.length) return [{ segment: null, mz, intensity }]
  const among = (bound) => mz.length > 0 && mz[0] < bound && bound < mz[mz.length - 1]
  const zeros = (points) => points.map(() => 0)
  return runsAsDrawn(runs)
    .map((run) => {
      const x = mz.slice(run.from, run.to)
      const y = intensity.slice(run.from, run.to)
      const below = among(run.mz_lower) ? baselineTo(x, y, run.mz_lower, false).reverse() : []
      const above = among(run.mz_upper) ? baselineTo(x, y, run.mz_upper, true) : []
      return {
        segment: run.segment,
        mz: [...below, ...x, ...above],
        intensity: [...zeros(below), ...y, ...zeros(above)]
      }
    })
    .filter((stretch) => stretch.mz.length > 0)
}

// Where one run ends and the next begins, in m/z. An edge two runs share is
// one boundary, wherever each run's own calibration puts its side of it; a
// gap between two runs has one on either side.
export function runBoundaries(runs) {
  if (!runs || runs.length < 2) return []
  const edges = new Set()
  const drawn = runsAsDrawn(runs)
  drawn.forEach((run, position) => {
    if (position > 0) edges.add(run.mz_lower)
    if (position < drawn.length - 1) edges.add(run.mz_upper)
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
