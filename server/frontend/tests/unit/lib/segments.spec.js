import { describe, it, expect } from 'vitest'

import {
  SEGMENT_COLORS,
  SHARED_EDGE_PPM,
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

  // (the third run above is a lone sample above zero, which has no step to
  // say whether it comes up to a bound, and is left as it is)
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

// A profile as it is stored: samples in clusters around the peaks, a zero just
// outside each cluster, and nothing between two clusters. Four runs: the first
// two meet at 73 with baseline on either side, the second and third meet at
// 116 in the middle of a peak, and no run owns 131 to 132.
const profile = {
  mz: [
    ...[41, 41.5, 42, 60, 60.5, 61],
    ...[80, 80.5, 81, 115.5, 115.75],
    ...[116, 116.25, 120, 120.25, 120.5],
    ...[140, 140.5, 141, 300, 300.5]
  ],
  intensity: [...[5, 9, 0, 0, 7, 0], ...[0, 4, 0, 3, 6], ...[8, 2, 1, 6, 3], ...[2, 6, 0, 0, 1]],
  runs: [
    { segment: 0, mz_lower: 40, mz_upper: 73, from: 0, to: 6 },
    { segment: 1, mz_lower: 73, mz_upper: 116, from: 6, to: 11 },
    { segment: 0, mz_lower: 116, mz_upper: 131, from: 11, to: 16 },
    { segment: 3, mz_lower: 132, mz_upper: 460, from: 16, to: 21 }
  ]
}

describe('a run is drawn out to its bounds along the baseline', () => {
  const [first, second, third, fourth] = signalStretches(profile)

  it('carries the baseline on from either side to a boundary two runs share', () => {
    expect(first.mz.slice(-2)).toEqual([61, 73])
    expect(first.intensity.slice(-2)).toEqual([0, 0])
    expect(second.mz.slice(0, 2)).toEqual([73, 80])
    expect(second.intensity.slice(0, 2)).toEqual([0, 0])
  })

  it('adds nothing where the boundary cuts a peak, whose halves two ranges measured', () => {
    expect(second.mz.slice(-2)).toEqual([115.5, 115.75])
    expect(second.intensity.slice(-2)).toEqual([3, 6])
    expect(third.mz.slice(0, 2)).toEqual([116, 116.25])
    expect(third.intensity.slice(0, 2)).toEqual([8, 2])
  })

  it('comes down one sample step outside the end of a range that stops above zero', () => {
    // the narrowest of the steps next to the end, not the empty stretch
    // before the last cluster
    expect(third.mz.slice(-3)).toEqual([120.5, 120.75, 131])
    expect(third.intensity.slice(-3)).toEqual([3, 0, 0])
    expect(fourth.mz.slice(0, 3)).toEqual([132, 139.5, 140])
    expect(fourth.intensity.slice(0, 3)).toEqual([0, 0, 2])
  })

  it('reads the sample step inside a cluster, not across the empty stretch before a last one', () => {
    const alone = {
      mz: [50, 50.5, 51, 60, 80, 80.5, 81],
      intensity: [0, 4, 0, 7, 0, 4, 0],
      runs: [
        { segment: 0, mz_lower: 40, mz_upper: 73, from: 0, to: 4 },
        { segment: 1, mz_lower: 73, mz_upper: 90, from: 4, to: 7 }
      ]
    }

    expect(signalStretches(alone)[0].mz).toEqual([50, 50.5, 51, 60, 60.5, 73])
    expect(signalStretches(alone)[0].intensity).toEqual([0, 4, 0, 7, 0, 0])
  })

  it('leaves empty only the m/z that no run owns', () => {
    expect(third.mz.at(-1)).toBe(131)
    expect(fourth.mz[0]).toBe(132)
  })

  it('leaves the ends of the signal where its samples end', () => {
    expect(first.mz[0]).toBe(41)
    expect(fourth.mz.at(-1)).toBe(300.5)
  })

  it('adds no sample the spectrum did not hold, only zeros', () => {
    const drawn = [first, second, third, fourth]
    const added = drawn.flatMap(({ mz, intensity }) =>
      mz.map((value, at) => [value, intensity[at]]).filter(([value]) => !profile.mz.includes(value))
    )

    expect(added).toEqual([
      [73, 0],
      [73, 0],
      [120.75, 0],
      [131, 0],
      [132, 0],
      [139.5, 0]
    ])
    expect(drawn.flatMap(({ mz }) => mz).filter((value) => profile.mz.includes(value))).toEqual(
      profile.mz
    )
  })

  it('draws a run with no sample among the samples shown as its baseline', () => {
    const quiet = {
      mz: [...profile.mz.slice(0, 11), ...profile.mz.slice(16)],
      intensity: [...profile.intensity.slice(0, 11), ...profile.intensity.slice(16)],
      runs: [
        profile.runs[0],
        profile.runs[1],
        { ...profile.runs[2], from: 11, to: 11 },
        { ...profile.runs[3], from: 11, to: 16 }
      ]
    }

    expect(signalStretches(quiet)[2]).toEqual({ segment: 0, mz: [116, 131], intensity: [0, 0] })
  })

  it('goes straight on from a sample at zero, however close the bound', () => {
    const close = {
      mz: [50, 50.5, 51, 80, 80.5, 81],
      intensity: [0, 4, 0, 0, 4, 0],
      runs: [
        { segment: 0, mz_lower: 40, mz_upper: 51.25, from: 0, to: 3 },
        { segment: 1, mz_lower: 51.25, mz_upper: 90, from: 3, to: 6 }
      ]
    }

    expect(signalStretches(close)[0].mz).toEqual([50, 50.5, 51, 51.25])
    expect(signalStretches(close)[1].mz).toEqual([51.25, 80, 80.5, 81])
  })
})

// Two runs that meet in the map, each placed by its own range's m/z
// calibration: the edge they share arrives as two m/z, parts per million
// apart, as a gap or as an overlap.
describe('an edge two runs share is one, wherever each calibration puts it', () => {
  const apart = (lower, upper) => ({
    mz: [50, 50.5, 51, 80, 80.5, 81],
    intensity: [0, 4, 0, 0, 4, 0],
    runs: [
      { segment: 0, mz_lower: 40, mz_upper: lower, from: 0, to: 3 },
      { segment: 1, mz_lower: upper, mz_upper: 90, from: 3, to: 6 }
    ]
  })
  const gap = apart(73, 73 * (1 + 1e-6))
  const overlap = apart(73 * (1 + 1e-6), 73)

  it("is one boundary line, at the lower run's edge", () => {
    expect(runBoundaries(gap.runs)).toEqual([73])
    expect(runBoundaries(overlap.runs)).toEqual([73 * (1 + 1e-6)])
    expect(boundaryShapes(gap.runs)).toHaveLength(1)
  })

  it('has both runs drawn up to that one m/z, with no sliver between them', () => {
    for (const spectrum of [gap, overlap]) {
      const [low, high] = signalStretches(spectrum)
      const [edge] = runBoundaries(spectrum.runs)

      expect(low.mz.at(-1)).toBe(edge)
      expect(high.mz[0]).toBe(edge)
      expect([low.intensity.at(-1), high.intensity[0]]).toEqual([0, 0])
    }
  })

  it('is still two where no run owns what lies between, however narrow', () => {
    const wide = 73 * (1 + 2 * SHARED_EDGE_PPM * 1e-6)

    expect(runBoundaries(apart(73, wide).runs)).toEqual([73, wide])
    const [low, high] = signalStretches(apart(73, wide))
    expect([low.mz.at(-1), high.mz[0]]).toEqual([73, wide])
  })

  it('adds nothing below a first sample that lies past the edge it is drawn at', () => {
    // The upper run's calibration put its first sample a hair under the
    // lower run's edge, which is where the boundary is drawn
    const edge = 73 * (1 + 2e-6)
    const past = {
      mz: [50, 50.5, 51, 73.0001, 73.5, 74],
      intensity: [0, 4, 0, 0, 4, 0],
      runs: [
        { segment: 0, mz_lower: 40, mz_upper: edge, from: 0, to: 3 },
        { segment: 1, mz_lower: 73, mz_upper: 90, from: 3, to: 6 }
      ]
    }
    expect(73.0001).toBeLessThan(edge)

    const [low, high] = signalStretches(past)

    expect(low.mz.at(-1)).toBe(edge)
    expect(high.mz).toEqual([73.0001, 73.5, 74])
  })

  it('leaves the runs it was given as they were', () => {
    const fresh = apart(73, 73 * (1 + 1e-6))
    const before = JSON.stringify(fresh.runs)

    runBoundaries(fresh.runs)
    signalStretches(fresh)

    expect(JSON.stringify(fresh.runs)).toBe(before)
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
