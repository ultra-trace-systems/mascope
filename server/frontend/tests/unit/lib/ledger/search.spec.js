import { describe, it, expect } from 'vitest'

import {
  BATCH_SEARCH_PPM,
  matchesLedgerSearch,
  parseLedgerSearch,
  runSearchWindow
} from '@/lib/ledger/search.js'

// The ledger's search box: a formula, a listed name or an m/z, the m/z within
// the scope's window.

const ROW = { texts: ['C6H12O6', 'Glucose', null], mzs: [181.0707, 182.0741] }
const matches = (query, window = BATCH_SEARCH_PPM) =>
  matchesLedgerSearch(parseLedgerSearch(query), ROW, window)

describe('parseLedgerSearch', () => {
  it('reads a number as an m/z, anything else as text, and blank as nothing', () => {
    expect(parseLedgerSearch(' 181.0707 ')).toEqual({ kind: 'mz', mz: 181.0707 })
    expect(parseLedgerSearch('181')).toEqual({ kind: 'mz', mz: 181 })
    expect(parseLedgerSearch('.5')).toEqual({ kind: 'mz', mz: 0.5 })
    expect(parseLedgerSearch('C6H12O6')).toEqual({ kind: 'text', text: 'c6h12o6' })
    // Not a number a reader types for an m/z.
    expect(parseLedgerSearch('1e3').kind).toBe('text')
    expect(parseLedgerSearch('0').kind).toBe('text')
    expect(parseLedgerSearch('   ')).toEqual({ kind: 'none' })
    expect(parseLedgerSearch(null)).toEqual({ kind: 'none' })
  })
})

describe('matchesLedgerSearch', () => {
  it('matches a formula, ignoring case', () => {
    expect(matches('C6H12')).toBe(true)
    expect(matches('c6h12o6')).toBe(true)
    expect(matches('C7')).toBe(false)
  })

  it('matches a listed name', () => {
    expect(matches('gluc')).toBe(true)
    expect(matches('fructose')).toBe(false)
  })

  it('matches an m/z inside the window and not outside it', () => {
    // 181.0707 * 5e-6 = 0.000905
    expect(matches('181.0716')).toBe(true)
    expect(matches('181.0698')).toBe(true)
    expect(matches('181.0717')).toBe(false)
    expect(matches('181.0697')).toBe(false)
    // An isotope line's m/z finds the row it folds under.
    expect(matches('182.0741')).toBe(true)
    // An m/z is no text: the formula's digits do not match it.
    expect(matches('12')).toBe(false)
  })

  it("centres the window on the run's fitted centre", () => {
    // Measured 181.0707 for a query 2 ppm under it: off-centre by 2 ppm.
    const query = 181.0707 / (1 + 2e-6)
    const parsed = parseLedgerSearch(query.toFixed(6))
    expect(matchesLedgerSearch(parsed, ROW, { ppm: 0.5, centrePpm: 2 })).toBe(true)
    expect(matchesLedgerSearch(parsed, ROW, { ppm: 0.5, centrePpm: 0 })).toBe(false)
  })

  it('matches every row on an empty query', () => {
    expect(matches('')).toBe(true)
    expect(matchesLedgerSearch(parseLedgerSearch(''), { texts: [], mzs: [] }, 5)).toBe(true)
  })
})

describe('runSearchWindow', () => {
  it("is the run's mass gate: its cap in fitted widths, about its fitted centre", () => {
    const run = {
      sigma_ppm: 0.8,
      mu_ppm: -1.2,
      config: { measurement_model: { gate: { cap_z: 2.5 } } }
    }
    expect(runSearchWindow(run)).toEqual({ ppm: 2, centrePpm: -1.2 })
  })

  it('caps at three widths where the run recorded no cap', () => {
    expect(runSearchWindow({ sigma_ppm: 1, mu_ppm: null })).toEqual({ ppm: 3, centrePpm: 0 })
  })

  it("searches at the batch's window for a run that states no width", () => {
    expect(runSearchWindow({ sigma_ppm: null })).toEqual({ ppm: BATCH_SEARCH_PPM, centrePpm: 0 })
    expect(runSearchWindow(null)).toEqual({ ppm: BATCH_SEARCH_PPM, centrePpm: 0 })
  })
})
