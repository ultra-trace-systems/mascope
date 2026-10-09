import { describe, it, expect } from 'vitest'

import {
  SEGMENT_COLORS,
  boundaryShapes,
  runBoundaries,
  segmentColor,
  segmentDetail,
  segmentName,
  signalStretches
} from '@/lib/segments'

// A stitched spectrum as the server answers it: a reagent scan that owns the
// bottom of the range and a band further up, and two windows between and above.
const segments = [
  { index: 0, key: 'reagent', label: 'm/z 40-138', scans: 5, microscans: 1 },
  { index: 1, key: 'low', label: 'm/z 66-124, SIM', scans: 3, microscans: 10 },
  { index: 3, key: 'high', label: 'm/z 440-900', scans: null, microscans: null }
]
const runs = [
  { segment: 0, mz_lower: 40, mz_upper: 67, from: 0, to: 2 },
  { segment: 1, mz_lower: 67, mz_upper: 122, from: 2, to: 5 },
  { segment: 0, mz_lower: 122, mz_upper: 133, from: 5, to: 6 },
  { segment: 3, mz_lower: 444, mz_upper: 900, from: 6, to: 8 }
]
const spectrum = {
  mz: [41, 60, 70, 90, 110, 125, 500, 700],
  intensity: [9, 9, 4, 4, 4, 9, 2, 2],
  segments,
  runs
}

describe('a stitched spectrum is drawn run by run', () => {
  it('cuts the signal where the map cuts it', () => {
    expect(signalStretches(spectrum)).toEqual([
      { segment: 0, mz: [41, 60], intensity: [9, 9] },
      { segment: 1, mz: [70, 90, 110], intensity: [4, 4, 4] },
      { segment: 0, mz: [125], intensity: [9] },
      { segment: 3, mz: [500, 700], intensity: [2, 2] }
    ])
  })

  it('leaves out a run that holds no sample of the part shown', () => {
    const part = {
      ...spectrum,
      runs: [
        { segment: 0, mz_lower: 40, mz_upper: 67, from: 0, to: 0 },
        { segment: 1, mz_lower: 67, mz_upper: 122, from: 0, to: 3 }
      ],
      mz: [70, 90, 110],
      intensity: [4, 4, 4]
    }

    expect(signalStretches(part)).toEqual([{ segment: 1, mz: [70, 90, 110], intensity: [4, 4, 4] }])
  })

  it('draws a spectrum of one range as the one signal it is', () => {
    for (const none of [null, undefined, []]) {
      const single = { mz: [1, 2], intensity: [3, 4], segments: null, runs: none }
      expect(signalStretches(single)).toEqual([{ segment: null, mz: [1, 2], intensity: [3, 4] }])
    }
    expect(signalStretches(null)).toEqual([])
  })
})

describe('a segment keeps its colour', () => {
  it('gives a spectrum of one range the green it has always had', () => {
    expect(segmentColor(null, null)).toBe('green')
    expect(SEGMENT_COLORS[0]).toBe('green')
  })

  it('colours a segment by its place among the segments, not by its index', () => {
    // Index 3 is the third segment: the store's indexes have holes where a
    // stream owns nothing of the composite
    expect(segmentColor(segments, 0)).toBe(SEGMENT_COLORS[0])
    expect(segmentColor(segments, 1)).toBe(SEGMENT_COLORS[1])
    expect(segmentColor(segments, 3)).toBe(SEGMENT_COLORS[2])
  })

  it('never gives two runs that meet the same colour', () => {
    const colours = runs.map((run) => segmentColor(segments, run.segment))
    colours.slice(1).forEach((colour, i) => expect(colour).not.toBe(colours[i]))
  })
})

describe('where two runs meet', () => {
  it('is one boundary for a shared edge and two around a gap', () => {
    // 67 and 122 are shared; nothing owns 133 to 444
    expect(runBoundaries(runs)).toEqual([67, 122, 133, 444])
  })

  it('is nowhere for a spectrum of one run or none', () => {
    expect(runBoundaries([runs[0]])).toEqual([])
    expect(runBoundaries(null)).toEqual([])
    expect(boundaryShapes(undefined)).toEqual([])
  })

  it('is drawn as a line the height of the plot at that m/z', () => {
    const [first] = boundaryShapes(runs)
    expect(first).toMatchObject({ type: 'line', xref: 'x', yref: 'paper', x0: 67, x1: 67 })
    expect([first.y0, first.y1]).toEqual([0, 1])
    expect(boundaryShapes(runs)).toHaveLength(4)
  })
})

describe('how a segment is named', () => {
  it('is its scan range in a cell, without the unit every row would repeat', () => {
    expect(segmentName(segments, 1)).toBe('66-124, SIM')
    expect(segmentName(segments, 0)).toBe('40-138')
  })

  it('says how it was measured in full, where the server knows', () => {
    expect(segmentDetail(segments, 0)).toBe('m/z 40-138 · 5 scans · 1 microscan')
    expect(segmentDetail(segments, 1)).toBe('m/z 66-124, SIM · 3 scans · 10 microscans')
    expect(segmentDetail(segments, 3)).toBe('m/z 440-900')
  })

  it('is empty for a peak of no segment', () => {
    expect(segmentName(segments, null)).toBe('')
    expect(segmentDetail(null, 0)).toBe('')
    expect(segmentName(segments, 7)).toBe('')
  })
})
