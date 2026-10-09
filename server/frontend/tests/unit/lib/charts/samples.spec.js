import { describe, it, expect } from 'vitest'

import {
  HIDE_SAMPLES_BELOW_PX,
  SAMPLE_MARKER_SIZE,
  SHOW_SAMPLES_FROM_PX,
  samplesShown,
  withSampleMarkers
} from '@/lib/charts/samples.js'

/** `count` samples one unit apart, from `start`. */
const evenly = (count, start = 0) => Float32Array.from({ length: count }, (_, i) => start + i)

/**
 * A stored profile: `clusters` peaks of `perCluster` samples one unit apart,
 * the peaks `apart` units from one another.
 */
function clustered(clusters, perCluster, apart) {
  const x = []
  for (let c = 0; c < clusters; c++) {
    for (let i = 0; i < perCluster; i++) x.push(c * apart + i)
  }
  return Float32Array.from(x)
}

describe('samplesShown', () => {
  // Ten one-unit gaps across a 100 px plot are 10 px apart, across 50 px 5 px.
  it('dots samples once they are far enough apart on screen', () => {
    const x = evenly(11)
    expect(samplesShown(x, [0, 10], 100)).toBe(true)
    expect(samplesShown(x, [0, 10], 50)).toBe(false)
  })

  it('needs the full gap to add dots, and less to keep them', () => {
    const x = evenly(11)
    // A gap between the two thresholds keeps whatever it found.
    const between = ((SHOW_SAMPLES_FROM_PX + HIDE_SAMPLES_BELOW_PX) / 2) * 10
    expect(samplesShown(x, [0, 10], between, false)).toBe(false)
    expect(samplesShown(x, [0, 10], between, true)).toBe(true)
    // Below the lower one they go.
    expect(samplesShown(x, [0, 10], (HIDE_SAMPLES_BELOW_PX - 0.5) * 10, true)).toBe(false)
  })

  // A profile is stored around its peaks with long empty stretches between
  // them, so its mean gap is wide even where the samples are packed tight. Three
  // peaks of five samples, 100 units apart, across a 1000 px plot: the mean gap
  // is 71 px, the gap inside a peak 4.9 px.
  it('decides on the spacing inside a peak, not the stretches between peaks', () => {
    const x = clustered(3, 5, 100)
    expect(samplesShown(x, [0, 204], 1000)).toBe(false)
    // Twice the width puts the samples of a peak 9.8 px apart.
    expect(samplesShown(x, [0, 204], 2000)).toBe(true)
  })

  it('counts only the samples in view', () => {
    // Packed samples left of the view, sparse ones in it.
    const x = Float32Array.from([...Array.from({ length: 50 }, (_, i) => i * 0.01), 10, 20, 30, 40])
    expect(samplesShown(x, [10, 40], 300)).toBe(true)
    expect(samplesShown(x, [0, 40], 300)).toBe(false)
  })

  it('leaves a whole spectrum undotted', () => {
    // 30,000 samples in 3,000 peaks across a 1,500 px plot.
    expect(samplesShown(clustered(3000, 10, 50), [0, 150000], 1500)).toBe(false)
  })

  it('reads a range given high to low the same way', () => {
    const x = evenly(11)
    expect(samplesShown(x, [10, 0], 100)).toBe(true)
    expect(samplesShown(x, [10, 0], 50)).toBe(false)
  })

  it('dots a lone sample in view, and has nothing to dot in an empty view', () => {
    const x = Float32Array.from([0, 10, 20])
    expect(samplesShown(x, [5, 15], 100)).toBe(true)
    expect(samplesShown(x, [11, 19], 100)).toBe(false)
  })

  it('is false for what it cannot measure', () => {
    expect(samplesShown(new Float32Array(), [0, 10], 100)).toBe(false)
    expect(samplesShown(undefined, [0, 10], 100)).toBe(false)
    expect(samplesShown(evenly(11), null, 100)).toBe(false)
    expect(samplesShown(evenly(11), [0, 10], 0)).toBe(false)
    expect(samplesShown(evenly(11), [5, 5], 100)).toBe(false)
  })
})

describe('withSampleMarkers', () => {
  const trace = () => ({
    name: 'Signal',
    mode: 'lines',
    line: { color: 'green' },
    x: Float32Array.from([1, 2, 3, 4, 5]),
    y: Float32Array.from([0, 10, 30, 10, 0])
  })

  it('dots each sample in the line colour', () => {
    const marked = withSampleMarkers(trace())
    expect(marked.mode).toBe('lines+markers')
    expect(marked.marker.color).toBe('green')
    expect(marked.name).toBe('Signal')
  })

  // The averaged profile carries a zero where the stored samples of a peak
  // stop, so that the line returns to the baseline; nothing was recorded there.
  it('gives the zeros around a peak no dot', () => {
    const S = SAMPLE_MARKER_SIZE
    expect(withSampleMarkers(trace()).marker.size).toEqual([0, S, S, S, 0])
  })

  it('dots a negative sample, which was recorded', () => {
    const negative = { ...trace(), y: Float32Array.from([-2, 10, 30, 10, -1]) }
    expect(withSampleMarkers(negative).marker.size[0]).toBe(SAMPLE_MARKER_SIZE)
  })

  // Set to the trace's own length, maxdisplayed thins out nothing; it is there
  // so that Plotly draws only the dots in view.
  it('lets Plotly draw every dot in view, and only those', () => {
    expect(withSampleMarkers(trace()).marker.maxdisplayed).toBe(5)
  })

  // A size per point is what Plotly takes for a bubble chart, and it would draw
  // the dots translucent and ringed in white.
  it('draws the dots solid and unringed', () => {
    const { marker } = withSampleMarkers(trace())
    expect(marker.opacity).toBe(1)
    expect(marker.line).toEqual({ width: 0 })
  })

  it('keeps the marker settings it was given', () => {
    const styled = {
      ...trace(),
      marker: { color: 'black', symbol: 'square', opacity: 0.5, line: { width: 1 } }
    }
    const marked = withSampleMarkers(styled)
    expect(marked.marker.color).toBe('black')
    expect(marked.marker.symbol).toBe('square')
    expect(marked.marker.opacity).toBe(0.5)
    expect(marked.marker.line).toEqual({ width: 1 })
  })

  it('leaves the trace it was given as it was', () => {
    const original = trace()
    withSampleMarkers(original)
    expect(original.mode).toBe('lines')
    expect(original.marker).toBeUndefined()
  })
})
