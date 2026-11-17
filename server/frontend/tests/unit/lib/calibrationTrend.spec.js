import { describe, it, expect, vi } from 'vitest'

vi.mock('@/lib/runtime.js', () => ({ runtime: { api_path: 'http://api.test' } }))

import {
  BIN_MS,
  COLLECTION_COLORS,
  DENSE_POINTS,
  MARK_COLOR,
  OTHER_COLOR,
  PANEL_GAP,
  PANEL_HEIGHT,
  binLabel,
  binValues,
  calibrantTitle,
  changeLabel,
  collectionGroups,
  figureHeight,
  plotTime,
  quartiles,
  trendExportUrl,
  trendFigure,
  trendPanels,
  trendQuery
} from '@/lib/calibrationTrend'

// The calibration trend of a TOF instrument, as GET /api/calibration/trend
// answers it: per file what its record says, per calibrant line a series
// aligned with the files, the collection versions and where they changed.

const file = (n, more = {}) => ({
  sample_file_id: `f${n}`,
  filename: `tof_${n}.h5`,
  datetime_utc: `2026-10-1${n}T08:00:00+00:00`,
  status: 'ok',
  collection: 0,
  n_points: 3,
  n_candidates: 4,
  post_fit_ppm: 1.2,
  pre_fit_abs_ppm: 80,
  axis_offset_ppm: -80,
  ...more
})

const TREND = {
  instrument: 'TOF-X',
  polarity: '+',
  truncated: false,
  binned: false,
  bin: 'hour',
  n_files: 4,
  bins: [],
  files: [
    file(1),
    file(2, { collection: 1, axis_offset_ppm: -75 }),
    file(3, {
      collection: null,
      status: 'failed',
      n_points: null,
      n_candidates: null,
      post_fit_ppm: null,
      pre_fit_abs_ppm: null,
      axis_offset_ppm: null
    }),
    file(4, { collection: 2, axis_offset_ppm: null, n_candidates: null })
  ],
  calibrants: [
    {
      ion: 'C3H10N+',
      target_mz: 60.0808,
      residual_ppm: [0.5, -0.4, null, null],
      retained: [true, false, null, null]
    },
    {
      ion: 'C9H24N3+',
      target_mz: 174.1971,
      residual_ppm: [2.0, 9.0, null, null],
      retained: [true, true, null, null]
    }
  ],
  collections: [
    { id: 'set', isotopes_sha256: 'a'.repeat(64), name: 'Propylamine set' },
    { id: 'set', isotopes_sha256: 'b'.repeat(64), name: 'Propylamine set' },
    { id: 'hidden', isotopes_sha256: 'c'.repeat(64), name: null }
  ],
  changes: [
    {
      datetime_utc: '2026-10-12T08:00:00+00:00',
      sample_file_id: 'f2',
      kind: 'edited',
      collection: 1,
      collection_id: 'set',
      isotopes_sha256: 'b'.repeat(64),
      previous_isotopes_sha256: 'a'.repeat(64)
    },
    {
      datetime_utc: '2026-10-14T08:00:00+00:00',
      sample_file_id: 'f4',
      kind: 'first_used',
      collection: 2,
      collection_id: 'hidden',
      isotopes_sha256: 'c'.repeat(64),
      previous_isotopes_sha256: null
    }
  ]
}

describe('trendQuery and trendExportUrl', () => {
  it('reads the given days back from now', () => {
    const now = new Date('2026-10-20T00:00:00Z')

    expect(trendQuery('TOF-X', '+', 30, now)).toEqual({
      instrument: 'TOF-X',
      polarity: '+',
      datetime_min: '2026-09-20T00:00:00.000Z',
      datetime_max: '2026-10-20T00:00:00.000Z'
    })
  })

  it('encodes the polarity sign in the export link', () => {
    const url = trendExportUrl(trendQuery('TOF X', '+', 30, new Date('2026-10-20T00:00:00Z')))

    expect(url.startsWith('http://api.test/api/calibration/trend/export?')).toBe(true)
    const params = new URL(url).searchParams
    expect(params.get('polarity')).toBe('+')
    expect(params.get('instrument')).toBe('TOF X')
    expect(url).toContain('polarity=%2B')
  })
})

describe('collectionGroups', () => {
  it('gives each collection one colour, its versions together, in order', () => {
    const groups = collectionGroups(TREND)

    expect(groups.map((g) => [g.id, g.versions, g.color])).toEqual([
      ['set', [0, 1], COLLECTION_COLORS.light[0]],
      ['hidden', [2], COLLECTION_COLORS.light[1]]
    ])
  })

  it('names a collection served without its name as hidden or deleted', () => {
    // The route serves no name for a collection the asker cannot read, nor
    // for one deleted since: the label holds for both.
    expect(collectionGroups(TREND)[1].label).toBe('Hidden or deleted collection')
  })

  it('folds a fourth collection and later ones into the other colour', () => {
    const many = {
      collections: ['a', 'b', 'c', 'd', 'e'].map((id) => ({ id, name: id }))
    }

    const groups = collectionGroups(many, true)

    expect(groups.map((g) => g.color)).toEqual([
      ...COLLECTION_COLORS.dark,
      OTHER_COLOR.dark,
      OTHER_COLOR.dark
    ])
    expect(groups.map((g) => g.other)).toEqual([false, false, false, true, true])
  })
})

describe('changeLabel and calibrantTitle', () => {
  it('says which collection changed and how', () => {
    expect(changeLabel(TREND.changes[0], TREND)).toBe('Propylamine set edited')
    expect(changeLabel(TREND.changes[1], TREND)).toBe('Hidden or deleted collection first used')
  })

  it('names a line by its ion and exact m/z', () => {
    expect(calibrantTitle(TREND.calibrants[1])).toBe('C9H24N3+ 174.197')
    expect(calibrantTitle({ ion: null, target_mz: 99.5 })).toBe('m/z 99.500')
  })
})

describe('trendPanels', () => {
  it('draws the offset, the residual and the counts, then each line by mass', () => {
    const panels = trendPanels(TREND)

    expect(panels.map((p) => p.key)).toEqual([
      'offset',
      'residual',
      'counts',
      'line-C3H10N+-60.0808',
      'line-C9H24N3+-174.1971'
    ])
    expect(panels[0].values).toEqual([-80, -75, null, null])
    expect(panels[2].values).toEqual([3, 3, null, 3])
    expect(panels[2].candidates).toEqual([4, 4, null, null])
    expect(panels[3].kept).toEqual([true, false, null, null])
  })
})

describe('plotTime', () => {
  it('writes an instant in UTC without an offset, which Plotly reads', () => {
    expect(plotTime('2026-10-19T14:30:00+03:00')).toBe('2026-10-19 11:30:00')
  })
})

describe('trendFigure', () => {
  const figure = trendFigure(TREND)
  const onAxis = (yaxis) => figure.data.filter((trace) => trace.yaxis === yaxis)

  it('gives every panel an axis of its own over one time axis', () => {
    expect(figure.layout.yaxis.domain[1]).toBe(1)
    expect(figure.layout.yaxis5.domain[0]).toBeCloseTo(0)
    expect(figure.data.every((trace) => trace.xaxis === 'x')).toBe(true)
    expect(figure.layout.xaxis.anchor).toBe('y5')
    expect(figure.height).toBe(figureHeight(5))
    expect(figureHeight(2)).toBe(56 + 40 + 2 * PANEL_HEIGHT + PANEL_GAP)
  })

  it('colours a point by its collection and leaves out files with no value', () => {
    const offset = onAxis('y')

    expect(offset.map((trace) => [trace.name, trace.x, trace.y])).toEqual([
      ['Propylamine set', ['2026-10-11 08:00:00', '2026-10-12 08:00:00'], [-80, -75]]
    ])
    expect(offset[0].marker.color).toBe(COLLECTION_COLORS.light[0])
  })

  it('draws a line the fit left out as an open point', () => {
    const line = onAxis('y4')

    expect(line).toHaveLength(1)
    expect(line[0].marker.symbol).toEqual(['circle', 'circle-open'])
    expect(line[0].text[1]).toContain('left out by the fit')
  })

  it('draws a file whose record names no collection in the other colour', () => {
    const older = trendFigure({
      files: [file(1, { collection: null }), file(2)],
      calibrants: [],
      collections: [{ id: 'set', isotopes_sha256: 'a'.repeat(64), name: 'Propylamine set' }],
      changes: []
    })
    const residual = older.data.filter((trace) => trace.yaxis === 'y2')

    expect(residual.map((trace) => [trace.name, trace.marker.color, trace.x])).toEqual([
      ['Propylamine set', COLLECTION_COLORS.light[0], ['2026-10-12 08:00:00']],
      ['No collection on record', OTHER_COLOR.light, ['2026-10-11 08:00:00']]
    ])
  })

  it('draws a failed calibration as a cross at zero on the counts', () => {
    const counts = onAxis('y3')
    const kept = counts.filter((trace) => trace.marker.size === 6)

    expect(kept.map((trace) => trace.name)).toEqual([
      'Propylamine set',
      'Hidden or deleted collection'
    ])
    const failed = counts.find((t) => t.name === 'Calibration failed')
    expect(failed.x).toEqual(['2026-10-13 08:00:00'])
    expect(failed.y).toEqual([0])
    expect(failed.marker.symbol).toBe('x')
  })

  it('reads whole lines on the counts, and ppm to two decimals elsewhere', () => {
    const template = (yaxis) => onAxis(yaxis)[0].hovertemplate

    expect(template('y3')).toContain('<br>%{y} lines<br>')
    for (const yaxis of ['y', 'y2', 'y4']) expect(template(yaxis)).toContain('<br>%{y:.2f} ppm<br>')
  })

  it('draws the lines matched beside the lines kept', () => {
    const matched = onAxis('y3').filter((trace) => trace.marker.symbol === 'circle-open')

    expect(matched.map((trace) => trace.y)).toEqual([[4, 4]])
  })

  it('marks each change through every panel, numbered in order', () => {
    expect(figure.layout.shapes.map((s) => [s.x0, s.yref, s.y0, s.y1])).toEqual([
      ['2026-10-12 08:00:00', 'paper', 0, 1],
      ['2026-10-14 08:00:00', 'paper', 0, 1]
    ])
    const numbers = figure.layout.annotations.filter((a) => a.xref === 'x')
    expect(numbers.map((a) => [a.text, a.hovertext])).toEqual([
      ['1', 'Propylamine set edited'],
      ['2', 'Hidden or deleted collection first used']
    ])
  })

  it('titles each panel', () => {
    const titles = figure.layout.annotations.filter((a) => a.xref === 'paper').map((a) => a.text)

    expect(titles).toHaveLength(5)
    expect(titles[3]).toBe('C3H10N+ 60.081: residual after the fit')
  })

  it('takes the dark steps in the dark theme', () => {
    const darkFigure = trendFigure(TREND, { dark: true })

    expect(darkFigure.groups[0].color).toBe(COLLECTION_COLORS.dark[0])
  })

  it('draws an empty trend as three empty panels', () => {
    const empty = trendFigure({ files: [], calibrants: [], collections: [], changes: [] })

    expect(empty.data).toEqual([])
    expect(empty.layout.yaxis3).toBeDefined()
  })
})

describe('quartiles', () => {
  it('reads each fraction linearly between the two values nearest it', () => {
    expect(quartiles([4, 1, 3, 2])).toEqual([1.75, 2.5, 3.25])
    expect(quartiles([1, 2, 3, 4, 5])).toEqual([2, 3, 4])
    expect(quartiles([5])).toEqual([5, 5, 5])
  })
})

describe('binValues and binLabel', () => {
  const at = (iso) => Date.parse(iso)
  const times = [
    '2026-10-19T08:10:00Z',
    '2026-10-19T08:50:00Z',
    '2026-10-19T09:05:00Z',
    '2026-10-19T09:30:00Z',
    '2026-10-20T00:00:00Z'
  ].map(at)
  const values = [1, 3, 5, null, 7]

  it('bins each file in its whole hour in UTC, and skips files with no value', () => {
    expect(binValues(times, values, [0, 1, 2, 3, 4], BIN_MS.hour)).toEqual([
      { start: at('2026-10-19T08:00:00Z'), quartiles: [1.5, 2, 2.5], n: 2 },
      { start: at('2026-10-19T09:00:00Z'), quartiles: [5, 5, 5], n: 1 },
      { start: at('2026-10-20T00:00:00Z'), quartiles: [7, 7, 7], n: 1 }
    ])
  })

  it('bins only the files given, per day from midnight UTC', () => {
    expect(binValues(times, values, [4, 0, 2, 1], BIN_MS.day)).toEqual([
      { start: at('2026-10-19T00:00:00Z'), quartiles: [2, 3, 4], n: 3 },
      { start: at('2026-10-20T00:00:00Z'), quartiles: [7, 7, 7], n: 1 }
    ])
  })

  it('names an hour and a day', () => {
    expect(binLabel(at('2026-10-19T23:00:00Z'), 'hour')).toBe('2026-10-19 23:00 to 00:00')
    expect(binLabel(at('2026-10-19T00:00:00Z'), 'day')).toBe('2026-10-19')
  })
})

describe('trendFigure of a dense window', () => {
  // One file every 20 minutes, three to an hour, with axis offsets 0, 1 and
  // 2 ppm in each hour; ten hours pass without a file after the 250th. One
  // line is listed by every file, and the first file left it out; another
  // by the first ten files alone.
  const T0 = Date.parse('2026-10-19T00:00:00Z')
  const denseTrend = (n) => {
    const files = Array.from({ length: n }, (_, i) =>
      file(1, {
        sample_file_id: `d${i}`,
        filename: `d${i}.h5`,
        datetime_utc: new Date(T0 + i * 1_200_000 + (i >= 250 ? 36_000_000 : 0)).toISOString(),
        axis_offset_ppm: i % 3
      })
    )
    return {
      binned: false,
      bin: 'hour',
      n_files: n,
      files,
      bins: [],
      calibrants: [
        {
          ion: 'NO3-',
          target_mz: 61.9884,
          residual_ppm: files.map(() => 0.5),
          retained: files.map((_, i) => i !== 0)
        },
        {
          ion: 'Br3-',
          target_mz: 236.7536,
          residual_ppm: files.map((_, i) => (i < 10 ? 2 : null)),
          retained: files.map((_, i) => (i < 10 ? true : null))
        }
      ],
      collections: [TREND.collections[0]],
      changes: []
    }
  }
  const figure = trendFigure(denseTrend(DENSE_POINTS + 1))
  const onAxis = (yaxis) => figure.data.filter((trace) => trace.yaxis === yaxis)
  const named = (yaxis, suffix) => onAxis(yaxis).find((trace) => trace.name.endsWith(suffix))

  it('draws the lines kept small and translucent, those left out as they were', () => {
    const [points] = onAxis('y4')

    expect(points.marker.size.slice(0, 2)).toEqual([6, 3])
    expect(points.marker.opacity.slice(0, 2)).toEqual([1, 0.3])
    expect(points.marker.line.width.slice(0, 2)).toEqual([1.5, 0])
    expect(points.marker.symbol.slice(0, 2)).toEqual(['circle-open', 'circle'])
  })

  it('leaves a line that few files of the window list as it was', () => {
    const line = onAxis('y5')

    expect(line).toHaveLength(1)
    expect(line[0].marker.size).toBe(6)
  })

  it('draws the lines matched small and translucent too', () => {
    const matched = onAxis('y3').find((trace) => trace.marker.symbol === 'circle-open')

    expect([matched.marker.size, matched.marker.opacity, matched.marker.line.width]).toEqual([
      5, 0.3, 1
    ])
  })

  it("draws each hour's median over the band of its middle half, after the points", () => {
    expect(onAxis('y').map((trace) => trace.name)).toEqual([
      'Propylamine set',
      'Propylamine set: middle half',
      'Propylamine set: median'
    ])
    const band = named('y', ': middle half')
    const median = named('y', ': median')
    expect(band.fill).toBe('toself')
    expect(band.fillcolor).toBe(`${COLLECTION_COLORS.light[0]}38`)
    expect(median.mode).toBe('lines')
    expect(median.line.color).toBe(COLLECTION_COLORS.light[0])
    // At the middle of the hour: its median, and the band from its third
    // quartile, back along its first.
    expect([median.x[0], median.y[0]]).toEqual(['2026-10-19 00:30:00', 1])
    expect(band.y[0]).toBe(1.5)
    expect(band.y[band.x.indexOf(null) - 1]).toBe(0.5)
    expect(median.text[0]).toBe(
      '2026-10-19 00:00 to 01:00 UTC<br>median 1.00 ppm, middle half 0.50 to 1.50<br>' +
        '3 files<br>Propylamine set'
    )
    expect([figure.summary, figure.points]).toEqual([true, true])
  })

  it('breaks the median and its band where more than three hours have no file', () => {
    const median = named('y', ': median')
    const band = named('y', ': middle half')

    // 84 hours, then a gap of ten, then the rest: two runs.
    expect(median.x.filter((x) => x === null)).toHaveLength(1)
    expect(median.x.indexOf(null)).toBe(84)
    expect(band.x.indexOf(null)).toBe(2 * 84)
    expect(median.x[85]).toBe('2026-10-22 21:30:00')
  })

  it('bridges two empty hours, and breaks at three', () => {
    // 101 files in each of hours 0, 3 and 7.
    const hours = [0, 3, 7]
    const files = hours.flatMap((hour) =>
      Array.from({ length: 101 }, (_, i) =>
        file(1, {
          sample_file_id: `h${hour}-${i}`,
          datetime_utc: new Date(T0 + hour * 3_600_000 + i * 30_000).toISOString(),
          axis_offset_ppm: hour
        })
      )
    )
    const gaps = trendFigure({ ...denseTrend(0), n_files: files.length, files, calibrants: [] })
    const median = gaps.data.find((t) => t.yaxis === 'y' && t.name.endsWith(': median'))

    expect(median.y).toEqual([0, 3, null, 7])
  })

  it('bins per day in a window the route bins per day', () => {
    const daily = trendFigure({ ...denseTrend(DENSE_POINTS + 1), bin: 'day' })
    const median = daily.data.find((t) => t.yaxis === 'y' && t.name.endsWith(': median'))

    expect(median.x.slice(0, 2)).toEqual(['2026-10-19 12:00:00', '2026-10-20 12:00:00'])
  })

  it('leaves a panel of as many points as the limit as it was', () => {
    const sparse = trendFigure(denseTrend(DENSE_POINTS))
    const offset = sparse.data.filter((trace) => trace.yaxis === 'y')

    expect(offset).toHaveLength(1)
    expect(offset[0].marker.size).toBe(6)
    expect(offset[0].marker.opacity).toBeUndefined()
    expect(sparse.summary).toBe(false)
  })
})

describe('trendFigure of a binned trend', () => {
  const bin = (start, collectionId, more = {}) => ({
    start_utc: `${start}T00:00:00+00:00`,
    collection_id: collectionId,
    n_files: 5,
    n_failed: 0,
    n_without_lines: 0,
    axis_offset_ppm: [-82, -80, -78],
    post_fit_ppm: [0.4, 0.6, 0.8],
    n_points: [3, 3, 4],
    n_candidates: [4, 4, 4],
    ...more
  })
  const nothing = {
    axis_offset_ppm: null,
    post_fit_ppm: null,
    n_points: null,
    n_candidates: null
  }
  const BINNED = {
    binned: true,
    bin: 'day',
    n_files: 15,
    files: [],
    bins: [
      bin('2026-10-19', 'set', { n_failed: 1 }),
      bin('2026-10-19', null, { n_files: 2, n_failed: 2, ...nothing }),
      bin('2026-10-20', 'set', { axis_offset_ppm: [-81, -79, -77] }),
      bin('2026-10-25', 'hidden', { n_files: 2 })
    ],
    calibrants: [
      {
        ion: 'NO3-',
        target_mz: 61.9884,
        residual_ppm: [[0.1, 0.2, 0.3], null, [0.2, 0.3, 0.4], null],
        n_listed: [5, 0, 4, 0],
        n_kept: [5, 0, 3, 0]
      }
    ],
    collections: TREND.collections,
    changes: []
  }
  const figure = trendFigure(BINNED)
  const onAxis = (yaxis) => figure.data.filter((trace) => trace.yaxis === yaxis)

  it('serves its panels per bin', () => {
    const panels = trendPanels(BINNED)

    expect(panels[0].quartiles[2]).toEqual([-81, -79, -77])
    expect(panels[0].values).toBeUndefined()
    expect(panels[2].title).toBe('Lines kept (solid) of the lines matched (dashed)')
    expect(panels[2].candidates[0]).toEqual([4, 4, 4])
    expect(panels[3].listed).toEqual([5, 0, 4, 0])
    expect(panels[3].keptIn).toEqual([5, 0, 3, 0])
  })

  it('draws the medians and middle halves alone, a dot on each median', () => {
    expect(onAxis('y').map((trace) => trace.name)).toEqual([
      'Propylamine set: middle half',
      'Propylamine set: median',
      'Hidden or deleted collection: middle half',
      'Hidden or deleted collection: median'
    ])
    const [, median, , hidden] = onAxis('y')
    expect(median.mode).toBe('lines+markers')
    expect(median.x).toEqual(['2026-10-19 12:00:00', '2026-10-20 12:00:00'])
    expect(median.y).toEqual([-80, -79])
    expect(hidden.line.color).toBe(COLLECTION_COLORS.light[1])
    expect(median.text[0]).toBe(
      '2026-10-19 UTC<br>median -80.00 ppm, middle half -82.00 to -78.00<br>' +
        '4 files<br>Propylamine set'
    )
    expect([figure.summary, figure.points]).toEqual([true, false])
  })

  it('draws the lines matched as a dashed median with no band', () => {
    const counts = onAxis('y3').filter((trace) => trace.name.startsWith('Propylamine set'))

    expect(counts.map((trace) => [trace.name, trace.line.dash])).toEqual([
      ['Propylamine set: middle half', undefined],
      ['Propylamine set: median', 'solid'],
      ['Propylamine set: median', 'dash']
    ])
    expect(counts[2].y).toEqual([4, 4])
    expect(counts[1].text[0]).toContain('median 3 lines, middle half 3 to 4')
  })

  it("says in a line's hover how many files list it and kept it", () => {
    const median = onAxis('y4').find((trace) => trace.name.endsWith(': median'))

    expect(median.text[1]).toContain('4 files list it, the fit kept it in 3')
  })

  it("draws a cross for a bin's failed calibrations, a day's together", () => {
    const failed = onAxis('y3').find((trace) => trace.name === 'Calibration failed')

    expect(failed.x).toEqual(['2026-10-19 12:00:00'])
    expect(failed.y).toEqual([0])
    expect(failed.text).toEqual(['2026-10-19 UTC<br>3 calibrations failed'])
    expect(failed.marker.color).toBe(MARK_COLOR.light)
  })

  it('draws a bin of fits that name no collection in the other colour', () => {
    const unnamed = trendFigure({
      ...BINNED,
      bins: [bin('2026-10-19', null)],
      calibrants: []
    })

    expect(unnamed.data[0].name).toBe('No collection on record: middle half')
    expect(unnamed.data[0].fillcolor).toBe(`${OTHER_COLOR.light}38`)
  })
})
