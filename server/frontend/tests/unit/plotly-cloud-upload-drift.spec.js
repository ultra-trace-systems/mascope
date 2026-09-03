import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// plotly.js 4 flipped `showSendToCloud` from false to true and set
// `plotlyServerURL` to cloud.plotly.com, so its modebar now offers a one-click
// upload of the plot - and its data - to a third party unless the chart says
// otherwise. Every chart here draws customer measurements, so that default has
// to be turned off rather than inherited. `displaylogo: false` and the
// `modeBarButtonsToRemove` list next to it do not cover this button.
//
// Read from source rather than mounted: the point is that the option is
// written down at the one place every chart is configured, which is what a
// future plotly upgrade could quietly undo.

const CHARTS = join(import.meta.dirname, '..', '..', 'src', 'lib', 'charts')
const BASE = join(CHARTS, 'BaseChartPlotly.vue')

/** Every file under src/lib/charts that could configure a plot. */
function chartSources(dir) {
  const found = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) found.push(...chartSources(path))
    else if (/\.(vue|js)$/.test(entry.name)) found.push(path)
  }
  return found
}

describe('plotly cloud upload', () => {
  it('is disabled where every chart takes its config', () => {
    expect(readFileSync(BASE, 'utf8')).toMatch(/showSendToCloud:\s*false/)
  })

  it('is never switched back on by an individual chart', () => {
    const offenders = []
    for (const path of chartSources(CHARTS)) {
      const source = readFileSync(path, 'utf8')
      for (const [, value] of source.matchAll(/showSendToCloud:\s*([\w.]+)/g)) {
        if (value !== 'false') offenders.push(`${path}: ${value}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('names no plotly server to upload to', () => {
    const offenders = chartSources(CHARTS).filter((path) =>
      /plotlyServerURL/.test(readFileSync(path, 'utf8'))
    )
    expect(offenders).toEqual([])
  })
})
