/**
 * The calibration trend of a TOF instrument, as its dialog draws it.
 *
 * `GET /api/calibration/trend` reads one polarity of an instrument's files
 * over a window of acquisition times and answers, per file, what its
 * calibration record says: each calibrant line's residual under the fit, kept
 * or left out; the fit's mean residual; how many lines it kept and chose
 * from; and the signed offset of the axis it read its peaks on. It also lists
 * the versions of the calibrant collections the files were fitted with, and
 * the files where a new version was first used (`changes`).
 *
 * The dialog draws them as small multiples on one time axis: the axis
 * offset, the mean residual and the line counts, then one panel per
 * calibrant line by m/z. A point's colour says which collection fitted it,
 * its fill whether the fit kept the line. A dashed line through every panel,
 * numbered at the top, marks each change of collection.
 *
 * Where a panel holds more points than it can draw apart
 * ({@link DENSE_POINTS}), its points turn small and translucent, and a line
 * through each bin's median, over a band of the middle half of its points,
 * shows where they crowd: per hour in a window of a month or less, per day
 * in a longer one (the route's `bin`). A window of more files than the route
 * serves one by one comes in those bins already (`binned`), summed up in the
 * database, and is drawn as the lines and bands alone.
 */

import { runtime } from '@/lib/runtime.js'

/** The windows the dialog offers, each ending now. */
export const TREND_WINDOWS = [
  { label: 'Last 30 days', days: 30 },
  { label: 'Last 90 days', days: 90 },
  { label: 'Last 365 days', days: 365 }
]

/**
 * How long the dialog waits for a trend, in milliseconds: a year of a busy
 * instrument is some 60,000 records summed up, longer than the client's
 * usual 20 seconds may allow.
 */
export const TREND_TIMEOUT_MS = 120_000

/**
 * The colours of the collections, in the order their files first appear: a
 * fixed categorical order, light and dark steps of the same hues. Three at
 * most tell apart as points; a fourth collection and later ones share
 * {@link OTHER_COLOR}.
 */
export const COLLECTION_COLORS = {
  light: ['#2a78d6', '#eb6834', '#1baf7a'],
  dark: ['#3987e5', '#d95926', '#199e70']
}

/** The colour of the collections past the third, and of the legend's keys. */
export const OTHER_COLOR = { light: '#7a7974', dark: '#a3a29b' }

/** The colour of the change marks and of a failed calibration's mark. */
export const MARK_COLOR = { light: '#52514e', dark: '#c3c2b7' }

/**
 * The label of a collection the route serves no name for: one in a workspace
 * the asker is not a member of, or one deleted since its files were fitted.
 * The route says neither apart.
 */
export const UNNAMED_COLLECTION = 'Hidden or deleted collection'

/**
 * The most files the export lists, the latest of a window that holds more;
 * the route's `EXPORT_MAX_FILES` (`calibration_trend.py`). It lists files,
 * never bins.
 */
export const EXPORT_MAX_FILES = 20_000

/**
 * A panel drawing more points than this draws them small and translucent,
 * under each bin's median and the band of its middle half: past a few
 * hundred, opaque markers hide where the points crowd, which is the trend.
 */
export const DENSE_POINTS = 300

/** The width of a bin the route names (`bin`), in milliseconds. */
export const BIN_MS = { hour: 3_600_000, day: 86_400_000 }

/**
 * A median line and its band break where two bins start more than this many
 * bin widths apart, three empty bins or more between them: across a longer
 * gap they would draw files that are not there.
 */
export const MAX_GAP_BINS = 3

/** The opacity of a band, as two hex digits after its colour's six. */
const BAND_ALPHA = '38'

/** A dense panel's points: their size in pixels and their opacity. */
const DENSE_SIZE = 3
const DENSE_OPACITY = 0.3

/** A panel's height and the gap above it, in pixels. */
export const PANEL_HEIGHT = 110
export const PANEL_GAP = 34

/** The figure's margins, in pixels: the top holds the change numbers. */
const MARGIN = { t: 56, b: 40, l: 64, r: 16 }

/**
 * An instant as Plotly's date axis reads it: Plotly knows no time zones and
 * would misread an offset, so the instant is written in UTC without one, and
 * the hover says UTC.
 *
 * @param {string} iso - an ISO 8601 instant, with its offset
 * @returns {string} `YYYY-MM-DD HH:MM:SS`, in UTC
 */
export const plotTime = (iso) => new Date(iso).toISOString().slice(0, 19).replace('T', ' ')

/**
 * The quartiles of some numbers as the database's `percentile_cont` takes
 * them: each fraction read linearly between the two values nearest it.
 *
 * @param {number[]} values - in any order, at least one
 * @returns {number[]} `[q1, median, q3]`
 */
export function quartiles(values) {
  const sorted = [...values].sort((a, b) => a - b)
  const at = (fraction) => {
    const position = fraction * (sorted.length - 1)
    const below = Math.floor(position)
    const above = Math.ceil(position)
    return sorted[below] + (sorted[above] - sorted[below]) * (position - below)
  }
  return [at(0.25), at(0.5), at(0.75)]
}

/**
 * Some files' values in bins of one width, as the route bins a window of
 * more files: each bin starts on a whole hour or day in UTC.
 *
 * @param {number[]} times - each file's acquisition time, in milliseconds
 * @param {(number|null)[]} values - each file's value, null for none
 * @param {number[]} indices - the files to bin
 * @param {number} width - a bin's width, in milliseconds
 * @returns {{ start: number, quartiles: number[], n: number }[]} oldest
 *   first: each bin's start in milliseconds, the {@link quartiles} of its
 *   files' values and how many files have one
 */
export function binValues(times, values, indices, width) {
  const bins = new Map()
  for (const index of indices) {
    if (values[index] == null) continue
    const start = Math.floor(times[index] / width) * width
    if (!bins.has(start)) bins.set(start, [])
    bins.get(start).push(values[index])
  }
  return [...bins.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([start, inBin]) => ({ start, quartiles: quartiles(inBin), n: inBin.length }))
}

/**
 * A bin in words, for its hover.
 *
 * @param {number} start - in milliseconds
 * @param {'hour'|'day'} unit
 * @returns {string} e.g. `2026-10-19` or `2026-10-19 08:00 to 09:00`
 */
export function binLabel(start, unit) {
  const from = new Date(start).toISOString()
  if (unit === 'day') return from.slice(0, 10)
  const to = new Date(start + BIN_MS.hour).toISOString()
  return `${from.slice(0, 10)} ${from.slice(11, 16)} to ${to.slice(11, 16)}`
}

/** A number as a panel's hover writes it: ppm to two decimals, counts whole
 * where they are. */
const formatValue = (value, unit) =>
  unit === 'ppm' ? value.toFixed(2) : `${Number(value.toFixed(2))}`

/**
 * The query of a trend over the last `days` days.
 *
 * @param {string} instrument - the instrument's name
 * @param {'+'|'-'} polarity
 * @param {number} days
 * @param {Date} [now]
 * @returns {object} the route's query parameters
 */
export function trendQuery(instrument, polarity, days, now = new Date()) {
  const start = new Date(now.getTime() - days * 86_400_000)
  return {
    instrument,
    polarity,
    datetime_min: start.toISOString(),
    datetime_max: now.toISOString()
  }
}

/**
 * The export's URL for a trend's query: a plain link, which the browser
 * downloads with the session's cookie.
 *
 * @param {object} query - from {@link trendQuery}
 * @returns {string}
 */
export function trendExportUrl(query) {
  const params = new URLSearchParams(query)
  return `${runtime.api_path}/api/calibration/trend/export?${params}`
}

/**
 * The collections the files were fitted with, one per collection, its
 * versions together, in the order of their first file, each with its colour.
 *
 * @param {object} trend - the route's `data`
 * @param {boolean} dark
 * @returns {object[]} `{ id, label, color, versions }`, `versions` the
 *   indices of its entries in `trend.collections`
 */
export function collectionGroups(trend, dark = false) {
  const palette = COLLECTION_COLORS[dark ? 'dark' : 'light']
  const groups = []
  for (const [index, version] of (trend?.collections ?? []).entries()) {
    let group = groups.find((g) => g.id === version.id)
    if (!group) {
      const position = groups.length
      group = {
        id: version.id,
        label: version.name ?? UNNAMED_COLLECTION,
        color: position < palette.length ? palette[position] : OTHER_COLOR[dark ? 'dark' : 'light'],
        other: position >= palette.length,
        versions: []
      }
      groups.push(group)
    }
    group.versions.push(index)
  }
  return groups
}

/**
 * A change of collection in words.
 *
 * @param {object} change - one of the trend's `changes`
 * @param {object} trend - the route's `data`
 * @returns {string} e.g. `Nitrate set edited`, `Bromide set first used`
 */
export function changeLabel(change, trend) {
  const name = trend?.collections?.[change.collection]?.name ?? UNNAMED_COLLECTION
  return change.kind === 'edited' ? `${name} edited` : `${name} first used`
}

/**
 * A line's panel title.
 *
 * @param {object} calibrant - one of the trend's `calibrants`
 * @returns {string} its ion and exact m/z, e.g. `NO3- 61.988`
 */
export function calibrantTitle(calibrant) {
  const mz = calibrant.target_mz.toFixed(3)
  return calibrant.ion ? `${calibrant.ion} ${mz}` : `m/z ${mz}`
}

/**
 * The panels of the figure, top to bottom: what each plots, per file, or per
 * bin where the route served the window in bins.
 *
 * @param {object} trend - the route's `data`
 * @returns {object[]} `{ key, title, unit, format, zeroline }` and, per file,
 *   `values` (null where a file has none) and `kept`, whether a point is
 *   drawn filled (null for the panels that are always filled); or, per bin
 *   of a binned trend, `quartiles` (`[q1, median, q3]`, null where a bin has
 *   none) and, for a line, how many of the bin's files list it (`listed`)
 *   and kept it (`keptIn`). `format` is the hover's number format (Plotly's,
 *   after `%{y`). The counts panel adds the lines matched, per file or per
 *   bin, as `candidates`.
 */
export function trendPanels(trend) {
  const binned = Boolean(trend?.binned)
  const rows = (binned ? trend?.bins : trend?.files) ?? []
  const pick = (field) => rows.map((row) => row[field] ?? null)
  const series = (field) => (binned ? { quartiles: pick(field) } : { values: pick(field) })
  return [
    {
      key: 'offset',
      title: 'Acquisition axis offset (signed mean of the lines kept)',
      unit: 'ppm',
      format: ':.2f',
      ...series('axis_offset_ppm'),
      kept: null,
      zeroline: true
    },
    {
      key: 'residual',
      title: 'Mean residual after the fit',
      unit: 'ppm',
      format: ':.2f',
      ...series('post_fit_ppm'),
      kept: null,
      zeroline: false
    },
    {
      key: 'counts',
      title: binned
        ? 'Lines kept (solid) of the lines matched (dashed)'
        : 'Lines kept (filled) of the lines matched (open)',
      unit: 'lines',
      // A count: whole lines, as the matched points beside them read.
      format: '',
      ...series('n_points'),
      candidates: pick('n_candidates'),
      kept: null,
      zeroline: false
    },
    ...(trend?.calibrants ?? []).map((calibrant) => ({
      key: `line-${calibrant.ion}-${calibrant.target_mz}`,
      title: `${calibrantTitle(calibrant)}: residual after the fit`,
      unit: 'ppm',
      format: ':.2f',
      ...(binned
        ? {
            quartiles: calibrant.residual_ppm,
            listed: calibrant.n_listed,
            keptIn: calibrant.n_kept,
            kept: null
          }
        : { values: calibrant.residual_ppm, kept: calibrant.retained }),
      zeroline: true
    }))
  ]
}

/**
 * The height the figure takes for its panels, in pixels.
 *
 * @param {number} panels - how many
 * @returns {number}
 */
export const figureHeight = (panels) =>
  MARGIN.t + MARGIN.b + panels * PANEL_HEIGHT + Math.max(0, panels - 1) * PANEL_GAP

/** A y axis' key in a layout and in a trace, for panel `i` from the top. */
const axisKey = (i) => (i === 0 ? 'yaxis' : `yaxis${i + 1}`)
const axisRef = (i) => (i === 0 ? 'y' : `y${i + 1}`)

const FILLED = 'circle'
const OPEN = 'circle-open'

/**
 * A bin's median as a line, over a band from its first quartile to its
 * third, in one collection's colour. Both break where two bins are more than
 * {@link MAX_GAP_BINS} widths apart; each run of bins between is a polygon
 * of the band, which a null closes.
 *
 * @param {{ start: number, quartiles: number[], text: string }[]} bins -
 *   oldest first
 * @param {number} width - a bin's width, in milliseconds
 * @param {object} group - the collection's group ({@link collectionGroups})
 * @param {string} yaxis - the panel's axis
 * @param {object} [options]
 * @param {boolean} [options.markers] - a dot on each median, which a bin
 *   alone between two gaps needs to show where no points are drawn
 * @param {string} [options.dash] - the median's dash; a dashed one gets no
 *   band
 * @returns {object[]} the band's trace, unless dashed, then the median's
 */
function summaryTraces(bins, width, group, yaxis, { markers = false, dash = 'solid' } = {}) {
  const runs = []
  for (const bin of bins) {
    const run = runs[runs.length - 1]
    if (run && bin.start - run[run.length - 1].start <= MAX_GAP_BINS * width) run.push(bin)
    else runs.push([bin])
  }
  const middle = (bin) => plotTime(new Date(bin.start + width / 2).toISOString())
  const band = { x: [], y: [] }
  const median = { x: [], y: [], text: [] }
  runs.forEach((run, i) => {
    if (i > 0) {
      band.x.push(null)
      band.y.push(null)
      median.x.push(null)
      median.y.push(null)
      median.text.push(null)
    }
    for (const bin of run) {
      band.x.push(middle(bin))
      band.y.push(bin.quartiles[2])
      median.x.push(middle(bin))
      median.y.push(bin.quartiles[1])
      median.text.push(bin.text)
    }
    for (const bin of [...run].reverse()) {
      band.x.push(middle(bin))
      band.y.push(bin.quartiles[0])
    }
  })
  const traces = []
  if (dash === 'solid') {
    traces.push({
      type: 'scattergl',
      mode: 'lines',
      name: `${group.label}: middle half`,
      xaxis: 'x',
      yaxis,
      x: band.x,
      y: band.y,
      fill: 'toself',
      fillcolor: `${group.color}${BAND_ALPHA}`,
      line: { width: 0, color: group.color },
      hoverinfo: 'skip'
    })
  }
  traces.push({
    type: 'scattergl',
    mode: markers ? 'lines+markers' : 'lines',
    name: `${group.label}: median`,
    xaxis: 'x',
    yaxis,
    x: median.x,
    y: median.y,
    text: median.text,
    line: { width: 2, color: group.color, dash },
    marker: { size: 4, color: group.color },
    hovertemplate: '%{text}<extra></extra>'
  })
  return traces
}

/**
 * A bin's hover: when, its median and middle half, and of how many files.
 *
 * @param {number} start - the bin's start, in milliseconds
 * @param {'hour'|'day'} unit
 * @param {number[]} q - its quartiles
 * @param {object} panel - from {@link trendPanels}
 * @param {string} files - how many files it sums up, in words
 * @param {object} group - the collection's group
 * @returns {string}
 */
const binText = (start, unit, q, panel, files, group) =>
  `${binLabel(start, unit)} UTC<br>median ${formatValue(q[1], panel.unit)} ${panel.unit}, ` +
  `middle half ${formatValue(q[0], panel.unit)} to ${formatValue(q[2], panel.unit)}<br>` +
  `${files}<br>${group.label}`

const filesIn = (n) => `${n} file${n === 1 ? '' : 's'}`

/**
 * The Plotly figure of a trend: its traces and its layout.
 *
 * One time axis for every panel, at the bottom. Each panel has an axis of
 * its own, so no two measures share a scale. A trace per panel and
 * collection, in the collection's colour; a file whose record names none is
 * drawn in the "other" colour. A failed calibration is a cross at zero on
 * the counts panel. The legend is the dialog's (`groups`): it names the
 * collections and the changes, which a legend inside the figure would crowd.
 *
 * A panel of more than {@link DENSE_POINTS} points draws the lines kept as
 * small translucent points, under each bin's median and middle half; the
 * lines left out stay open and opaque, and the failed calibrations crosses:
 * they are the few. A binned trend draws each panel's medians and middle
 * halves alone, a cross for each bin with a failed calibration, and the
 * lines matched as a dashed median.
 *
 * @param {object} trend - the route's `data`
 * @param {object} [options]
 * @param {boolean} [options.dark] - the dark theme's steps
 * @returns {{ data: object[], layout: object, height: number, groups: object[],
 *   summary: boolean, points: boolean }} `groups` from {@link collectionGroups};
 *   `summary` whether any panel draws medians, `points` whether any draws
 *   files one by one
 */
export function trendFigure(trend, { dark = false } = {}) {
  const theme = dark ? 'dark' : 'light'
  const panels = trendPanels(trend)
  const groups = collectionGroups(trend, dark)
  const none = {
    id: null,
    label: 'No collection on record',
    color: OTHER_COLOR[theme],
    versions: []
  }
  const unit = trend?.bin === 'day' ? 'day' : 'hour'
  const width = BIN_MS[unit]
  const height = figureHeight(panels.length)
  const plotHeight = height - MARGIN.t - MARGIN.b
  const fraction = (px) => px / plotHeight
  const counts = panels.findIndex((panel) => panel.key === 'counts')

  const drawn = trend?.binned
    ? binnedTraces(trend, panels, groups, none, { unit, width, counts, theme })
    : fileTraces(trend, panels, groups, none, { width, counts, theme })

  const layout = {
    height,
    margin: MARGIN,
    showlegend: false,
    hovermode: 'closest',
    xaxis: {
      type: 'date',
      anchor: axisRef(panels.length - 1),
      showgrid: true,
      gridcolor: '#80808033',
      automargin: true
    },
    annotations: [],
    shapes: []
  }
  panels.forEach((panel, i) => {
    const top = 1 - fraction(i * (PANEL_HEIGHT + PANEL_GAP))
    const bottom = top - fraction(PANEL_HEIGHT)
    layout[axisKey(i)] = {
      domain: [Math.max(0, bottom), Math.min(1, top)],
      title: { text: panel.unit, standoff: 4 },
      showgrid: true,
      gridcolor: '#80808033',
      zeroline: panel.zeroline,
      zerolinecolor: '#80808099',
      ...(panel.key === 'counts' ? { rangemode: 'tozero' } : {}),
      automargin: true
    }
    layout.annotations.push({
      text: panel.title,
      xref: 'paper',
      yref: 'paper',
      x: 0,
      y: Math.min(1, top),
      xanchor: 'left',
      yanchor: 'bottom',
      showarrow: false,
      font: { size: 12 }
    })
  })
  // Each change of collection: a dashed line through every panel, numbered
  // above the first; the dialog lists what each number is.
  for (const [n, change] of (trend?.changes ?? []).entries()) {
    layout.shapes.push({
      type: 'line',
      xref: 'x',
      yref: 'paper',
      x0: plotTime(change.datetime_utc),
      x1: plotTime(change.datetime_utc),
      y0: 0,
      y1: 1,
      line: { color: MARK_COLOR[theme], width: 1, dash: 'dash' }
    })
    layout.annotations.push({
      text: `${n + 1}`,
      xref: 'x',
      yref: 'paper',
      x: plotTime(change.datetime_utc),
      y: 1,
      yanchor: 'bottom',
      yshift: 22,
      showarrow: false,
      font: { size: 12 },
      hovertext: changeLabel(change, trend)
    })
  }
  return {
    data: drawn.traces,
    layout,
    height,
    groups,
    summary: drawn.summary,
    points: drawn.points
  }
}

/** A cross at zero on the counts panel for each failed calibration. */
function failedTrace(x, text, counts, theme) {
  return {
    type: 'scattergl',
    mode: 'markers',
    name: 'Calibration failed',
    xaxis: 'x',
    yaxis: axisRef(counts),
    x,
    y: x.map(() => 0),
    text,
    marker: { color: MARK_COLOR[theme], size: 8, symbol: 'x' },
    hovertemplate: '%{text}<extra></extra>'
  }
}

/** The traces of a trend served file by file ({@link trendFigure}). */
function fileTraces(trend, panels, groups, none, { width, counts, theme }) {
  const files = trend?.files ?? []
  const groupOfVersion = new Map()
  for (const group of groups) {
    for (const version of group.versions) groupOfVersion.set(version, group)
  }
  const groupOf = (file) => groupOfVersion.get(file.collection) ?? none
  const times = files.map((file) => plotTime(file.datetime_utc))
  const ms = files.map((file) => new Date(file.datetime_utc).getTime())
  const unit = width === BIN_MS.day ? 'day' : 'hour'
  const traces = []
  const summaries = []
  let summary = false
  let points = false

  panels.forEach((panel, i) => {
    const yaxis = axisRef(i)
    const dense = panel.values.filter((value) => value != null).length > DENSE_POINTS
    for (const group of [...groups, none]) {
      const indices = files
        .map((file, index) => index)
        .filter((index) => groupOf(files[index]) === group && panel.values[index] != null)
      if (indices.length === 0) continue
      points = true
      const leftOut = (index) => Boolean(panel.kept) && panel.kept[index] === false
      const point = (index) => ({
        x: times[index],
        y: panel.values[index],
        open: leftOut(index),
        text:
          `${files[index].filename}<br>${group.label}` +
          (leftOut(index) ? '<br>left out by the fit' : '')
      })
      const drawnPoints = indices.map(point)
      traces.push({
        type: 'scattergl',
        mode: 'markers',
        name: group.label,
        xaxis: 'x',
        yaxis,
        x: drawnPoints.map((p) => p.x),
        y: drawnPoints.map((p) => p.y),
        text: drawnPoints.map((p) => p.text),
        // Dense, the lines kept turn small and translucent; the few left
        // out stay as they are.
        marker: dense
          ? {
              color: group.color,
              size: drawnPoints.map((p) => (p.open ? 6 : DENSE_SIZE)),
              opacity: drawnPoints.map((p) => (p.open ? 1 : DENSE_OPACITY)),
              symbol: drawnPoints.map((p) => (p.open ? OPEN : FILLED)),
              line: { width: drawnPoints.map((p) => (p.open ? 1.5 : 0)), color: group.color }
            }
          : {
              color: group.color,
              size: 6,
              symbol: drawnPoints.map((p) => (p.open ? OPEN : FILLED)),
              line: { width: 1.5, color: group.color }
            },
        hovertemplate: `%{x|%Y-%m-%d %H:%M} UTC<br>%{y${panel.format}} ${panel.unit}<br>%{text}<extra></extra>`
      })
      if (panel.candidates) {
        const matched = indices.filter((index) => panel.candidates[index] != null)
        if (matched.length) {
          traces.push({
            type: 'scattergl',
            mode: 'markers',
            name: group.label,
            xaxis: 'x',
            yaxis,
            x: matched.map((index) => times[index]),
            y: matched.map((index) => panel.candidates[index]),
            text: matched.map((index) => files[index].filename),
            marker: {
              color: group.color,
              size: dense ? 5 : 8,
              symbol: OPEN,
              ...(dense ? { opacity: DENSE_OPACITY } : {}),
              line: { width: dense ? 1 : 1.5, color: group.color }
            },
            hovertemplate: `%{x|%Y-%m-%d %H:%M} UTC<br>%{y} lines matched<br>%{text}<extra></extra>`
          })
        }
      }
      if (dense) {
        summary = true
        const bins = binValues(ms, panel.values, indices, width).map((bin) => ({
          ...bin,
          text: binText(bin.start, unit, bin.quartiles, panel, filesIn(bin.n), group)
        }))
        // Over every collection's points, which WebGL draws in the order
        // of the traces.
        summaries.push(...summaryTraces(bins, width, group, yaxis))
      }
    }
  })
  traces.push(...summaries)

  const failed = files
    .map((file, index) => index)
    .filter((index) => files[index].status === 'failed')
  if (failed.length) {
    traces.push(
      failedTrace(
        failed.map((index) => times[index]),
        failed.map(
          (index) =>
            `${times[index].slice(0, 16)} UTC<br>calibration failed<br>${files[index].filename}`
        ),
        counts,
        theme
      )
    )
  }
  return { traces, summary, points }
}

/** The traces of a trend served in bins ({@link trendFigure}). */
function binnedTraces(trend, panels, groups, none, { unit, width, counts, theme }) {
  const bins = trend?.bins ?? []
  const byId = new Map(groups.map((group) => [group.id, group]))
  const groupOf = (bin) =>
    bin.collection_id == null ? none : (byId.get(bin.collection_id) ?? none)
  const starts = bins.map((bin) => new Date(bin.start_utc).getTime())
  const traces = []
  let summary = false

  panels.forEach((panel, i) => {
    const yaxis = axisRef(i)
    for (const group of [...groups, none]) {
      const indices = bins
        .map((bin, index) => index)
        .filter((index) => groupOf(bins[index]) === group)
      const summed = (series, label) =>
        indices
          .filter((index) => series[index] != null)
          .map((index) => ({
            start: starts[index],
            quartiles: series[index],
            text: binText(starts[index], unit, series[index], panel, label(index), group)
          }))
      const values = summed(panel.quartiles, (index) =>
        panel.listed
          ? `${filesIn(panel.listed[index])} list it, the fit kept it in ${panel.keptIn[index]}`
          : filesIn(bins[index].n_files - bins[index].n_failed)
      )
      if (values.length) {
        summary = true
        traces.push(...summaryTraces(values, width, group, yaxis, { markers: true }))
      }
      if (panel.candidates) {
        const matched = summed(
          panel.candidates,
          (index) => `lines matched, ${filesIn(bins[index].n_files - bins[index].n_failed)}`
        )
        if (matched.length) {
          traces.push(
            ...summaryTraces(matched, width, group, yaxis, { markers: true, dash: 'dash' })
          )
        }
      }
    }
  })

  // A cross for each bin of failed calibrations, those of a day together.
  const failed = new Map()
  bins.forEach((bin, index) => {
    if (bin.n_failed > 0) failed.set(starts[index], (failed.get(starts[index]) ?? 0) + bin.n_failed)
  })
  if (failed.size) {
    const at = [...failed.keys()].sort((a, b) => a - b)
    traces.push(
      failedTrace(
        at.map((start) => plotTime(new Date(start + width / 2).toISOString())),
        at.map(
          (start) =>
            `${binLabel(start, unit)} UTC<br>` +
            `${failed.get(start)} calibration${failed.get(start) === 1 ? '' : 's'} failed`
        ),
        counts,
        theme
      )
    )
  }
  return { traces, summary, points: false }
}
