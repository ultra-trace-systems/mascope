import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

const { default: BatchPeakContinuityPopover } =
  await import('@/lib/panes/PaneBrowserMatch/BatchPeakContinuityPopover.vue')

const pair = (grade, extra = {}) => ({
  kind: 'adduct',
  partner_batch_peak_id: `bp-${grade}`,
  partner_mz: 262.0569,
  grade,
  n: 41,
  ratio_median: 0.42,
  ratio_spread: 0.08,
  rho: 0.12,
  drift_log: 0.05,
  drift_fold: 1.05,
  co_variation: null,
  ...extra
})
const row = (extra = {}, detail = {}) => ({
  batch_peak_id: 'bp-1',
  consensus_formula: 'C6H12O6',
  n_present: 40,
  continuity_grade: 'red',
  continuity_stale: false,
  continuity: {
    eligible: true,
    reason: null,
    formula: 'C6H12O6',
    n_present_at_compute: 40,
    n_pairs: 2,
    warnings: [],
    pairs: [pair('red'), pair('stable')],
    ...detail
  },
  ...extra
})

const mountPopover = (props) => mount(BatchPeakContinuityPopover, { props })

describe('BatchPeakContinuityPopover', () => {
  it('lists every pair with its grade, samples, ratio, spread and drift', () => {
    const wrapper = mountPopover({ row: row() })
    const pairs = wrapper.findAll('[data-testid="continuity-pair"]')
    expect(pairs).toHaveLength(2)
    const cells = pairs[0].findAll('td').map((td) => td.text())
    expect(cells).toEqual([
      'Other channel 262.0569',
      'Red',
      '41',
      '0.42',
      '8%',
      'rises 1.05x, rho 0.12'
    ])
    expect(pairs[1].find('td.grade-cell').text()).toBe('Stable')
    expect(wrapper.find('.grade.red').text()).toBe('Red')
    expect(wrapper.find('.meaning').text()).toMatch(/^The ratio drifts .*share a trace\.$/)
    expect(wrapper.text()).toMatch(/no tier moves on it/)
    expect(wrapper.find('[data-testid="continuity-warning"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="continuity-partial"]').exists()).toBe(false)
  })

  it("names an isotope line's partner as its main peak", () => {
    const wrapper = mountPopover({
      row: row({}, { pairs: [pair('red', { kind: 'isotopologue' })], n_pairs: 1 }),
      isotopeLine: true
    })
    expect(wrapper.find('[data-testid="continuity-pair"] td').text()).toMatch(/^Main peak/)
  })

  it('heads the list with a stale and a partial warning', () => {
    const wrapper = mountPopover({
      row: row({ continuity_stale: true, n_present: 44 }, { warnings: ['partial'], n_pairs: 9 })
    })
    expect(wrapper.find('[data-testid="continuity-warning"]').text()).toMatch(
      /Graded at 40 samples - the batch peak is in 44 now/
    )
    expect(wrapper.find('[data-testid="continuity-partial"]').text()).toMatch(/^Partial/)
    expect(wrapper.text()).toContain('Showing 2 of 9 pairs')
  })

  it('says why a batch was not graded, with no pairs to list', () => {
    const wrapper = mountPopover({
      row: row({ continuity_grade: null }, { eligible: false, reason: 'gap', pairs: [] })
    })
    expect(wrapper.find('[data-testid="continuity-warning"]').text()).toMatch(/not time-ordered/)
    expect(wrapper.find('table').exists()).toBe(false)
    expect(wrapper.find('.grade').exists()).toBe(false)
  })
})
