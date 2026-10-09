// Dots at the samples of a measured profile, for a trace that asks for them
// with `markSamples` (BaseChartPlotly draws them).
//
// A raw Orbitrap file keeps about three points across each peak, and a line
// through them cannot say so: it looks the same whether it joins three samples
// or thirty. A dot at each sample can, but only once the samples are far enough
// apart on screen to be told apart. Zoomed out, a dot per sample is a solid band
// that hides the line it sits on.

/** Diameter of a sample dot, in pixels. */
export const SAMPLE_MARKER_SIZE = 5

/** The on-screen gap between neighbouring samples from which they are dotted. */
export const SHOW_SAMPLES_FROM_PX = 6

/**
 * The gap below which dotted samples lose their dots again. Lower than the gap
 * that adds them, because adding them can widen the view: an autoranged axis
 * pads itself to fit the dots, which narrows every gap a little, and a single
 * threshold would then take the dots off and put them back without end.
 */
export const HIDE_SAMPLES_BELOW_PX = 5

/** Index of the first element of ascending `x` that is not below `value`. */
function lowerBound(x, value) {
  let lo = 0
  let hi = x.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (x[mid] < value) lo = mid + 1
    else hi = mid
  }
  return lo
}

/** Index of the first element of ascending `x` that is above `value`. */
function upperBound(x, value) {
  let lo = 0
  let hi = x.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (x[mid] <= value) lo = mid + 1
    else hi = mid
  }
  return lo
}

/**
 * Whether the samples of a profile inside the visible range sit far enough
 * apart on screen to be dotted one by one.
 *
 * A profile is stored in clusters around its peaks, with empty stretches
 * between them, so the mean gap says little about how close the dots would
 * be. The median gap between neighbouring visible samples is the spacing inside
 * a cluster, which is what decides whether its dots can be told apart.
 *
 * @param {ArrayLike<number>} x - The sample positions, ascending.
 * @param {[number, number]} range - The visible x range.
 * @param {number} widthPx - The width of the plot area, in pixels.
 * @param {boolean} [shown] - Whether the samples are dotted now, which lowers
 *   the gap they need to stay dotted (see HIDE_SAMPLES_BELOW_PX).
 * @returns {boolean}
 */
export function samplesShown(x, range, widthPx, shown = false) {
  if (!x?.length || !range || !(widthPx > 0)) return false
  const [x0, x1] = range[0] <= range[1] ? range : [range[1], range[0]]
  if (!(x1 > x0)) return false

  const first = lowerBound(x, x0)
  const gaps = upperBound(x, x1) - first - 1
  // No sample in view has nothing to dot; a lone one is as far from its
  // neighbours as it can be.
  if (gaps < 1) return gaps === 0

  const threshold = shown ? HIDE_SAMPLES_BELOW_PX : SHOW_SAMPLES_FROM_PX
  // At least half of the gaps are no narrower than their median, and together
  // they span no more than the view, so the median is at most twice the mean.
  // That settles a zoomed-out view without sorting the tens of thousands of
  // gaps of a whole spectrum.
  if ((2 * widthPx) / gaps < threshold) return false

  const sorted = new Float64Array(gaps)
  for (let i = 0; i < gaps; i++) sorted[i] = x[first + i + 1] - x[first + i]
  sorted.sort()
  const half = gaps >> 1
  const median = gaps % 2 ? sorted[half] : (sorted[half - 1] + sorted[half]) / 2
  return (median * widthPx) / (x1 - x0) >= threshold
}

/**
 * A copy of a line trace that also dots each of its samples.
 *
 * A point at exactly zero gets no dot. In a measured profile it marks where the
 * stored samples stop: the averaged Orbitrap profile carries one just outside
 * each cluster, as the Thermo library's does, so that the line returns to the
 * baseline between peaks. It is not an intensity the instrument recorded.
 *
 * A size per point makes Plotly take the trace for a bubble chart, which it
 * draws translucent and ringed in white, a white that stays white on a dark
 * theme. The dots are drawn solid and unringed instead, unless the trace says
 * otherwise.
 *
 * `marker.maxdisplayed` at the trace's own length thins nothing out, and it
 * makes Plotly draw only the dots inside the view rather than one element for
 * every sample of the whole spectrum.
 *
 * @param {object} trace - A Plotly scatter trace drawn as a line.
 * @returns {object}
 */
export function withSampleMarkers(trace) {
  return {
    ...trace,
    mode: 'lines+markers',
    marker: {
      opacity: 1,
      line: { width: 0 },
      ...trace.marker,
      color: trace.marker?.color ?? trace.line?.color,
      size: Array.from(trace.y, (value) => (value !== 0 ? SAMPLE_MARKER_SIZE : 0)),
      maxdisplayed: trace.x.length
    }
  }
}
