import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

import { num } from '@/lib/formatters'

// The inspector card for a peak with no committed formula. Two states reach it:
// a ledger row of tier `unassigned` (a real assignment row, just formula-less),
// and a focused peak with no assignment row at all (no run yet). Both used to
// read "Unassigned" over an empty evidence grid, saying nothing about which
// peak they were - and the first one offered a verdict form on nothing.

const PEAK = { peak_id: 'p-1', mz: 200.12345, height: 12345, area: 999 }

let focusedPeak
let focusedAssignment
let verdictRecord

const helpStub = {
  set: vi.fn(),
  docUrl: (path = '') => `/docs/${path}`,
  right: () => ({}),
  left: () => ({}),
  top: () => ({}),
  bottom: () => ({})
}

function makeApp() {
  return {
    data: {
      sample: { focusedId: 'si-1' },
      peak: { list: [PEAK], focused: focusedPeak, focus: vi.fn() },
      peakAssignment: {
        peak: {
          forPeak: () => focusedAssignment,
          detailOf: () => null,
          familyOf: () => (focusedAssignment ? [focusedAssignment] : []),
          loadDetail: () => Promise.resolve()
        },
        verification: { forAssignment: () => verdictRecord, verify: vi.fn() }
      }
    },
    ui: { help: helpStub }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

// Stubbed rather than auto-stubbed: the badge renders nothing of its own for a
// null record, which would make "no badge" pass even with the block still there.
vi.mock('@/lib/base', () => ({
  BaseTierTag: { props: ['tier'], template: '<span class="tier-tag" />' },
  BaseVerdictBadge: { props: ['record', 'compact'], template: '<span class="verdict-badge" />' }
}))

const GLOBAL_STUBS = {
  Button: { props: ['label'], template: '<button><slot />{{ label }}</button>' },
  Select: true,
  InputText: true
}

const { default: PanePeakAssign } = await import('@/lib/panes/PanePeakAssign/PanePeakAssign.vue')

async function mountPane() {
  const wrapper = mount(PanePeakAssign, {
    global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

/** A ledger row as the inspector sees it. */
function assignment({ formula = null, tier = 'unassigned', mz = 200.12345, intensity = 12345 }) {
  return {
    peak_assignment_id: 'pa-1',
    sample_peak_id: 'p-1',
    sample_peak_mz: mz,
    sample_peak_intensity: intensity,
    assigned_formula: formula,
    tier,
    role: formula ? 'M0' : 'unassigned',
    fit_score: formula ? 0.9 : null
  }
}

const VERDICT = {
  verdict: 'confirmed',
  evidence_level: 'msms',
  verified_by: 1,
  verified_utc: '2026-08-29T10:00:00Z'
}

const mz = (value) => `m/z ${num.mz.format(value)}`
const intensity = (value) => `intensity ${num.peakIntensity.format(value)}`

beforeEach(() => {
  focusedPeak = PEAK
  focusedAssignment = null
  verdictRecord = null
})
afterEach(() => vi.clearAllMocks())

describe('PanePeakAssign unassigned card', () => {
  it('names the peak on the fallback card, which has no assignment row at all', async () => {
    const wrapper = await mountPane()
    const text = wrapper.find('.insp-sub').text()

    expect(wrapper.text()).toContain('Unassigned')
    expect(text).toContain(mz(PEAK.mz))
    expect(text).toContain(intensity(PEAK.height))
  })

  it('names the peak on an unassigned ledger row, which carries its own values', async () => {
    focusedAssignment = assignment({ formula: null, mz: 314.15926, intensity: 4200 })
    const wrapper = await mountPane()
    const text = wrapper.find('.insp-sub').text()

    expect(text).toContain(mz(314.15926))
    expect(text).toContain(intensity(4200))
  })

  it('leaves an assigned card alone - its formula already names it', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'identified' })
    const wrapper = await mountPane()

    expect(wrapper.text()).toContain('C10H12')
    expect(wrapper.text()).not.toContain(mz(200.12345))
    expect(wrapper.text()).not.toContain(intensity(12345))
  })

  it('shows what it has when the peak carries no intensity', async () => {
    focusedPeak = { peak_id: 'p-1', mz: 200.12345, height: null }
    const wrapper = await mountPane()

    expect(wrapper.find('.insp-sub').text()).toBe(mz(200.12345))
  })
})

// A verdict on a formula-less row is a judgment about nothing: kept among the
// hand labels, with no evidence for the confidence calibration to learn from.
describe('PanePeakAssign verification gating', () => {
  it('offers no verdict form on a formula-less row', async () => {
    focusedAssignment = assignment({ formula: null })
    const wrapper = await mountPane()

    expect(wrapper.find('.verify').exists()).toBe(false)
    expect(wrapper.vm.showVerifyForm).toBe(false)
    expect(wrapper.text()).not.toContain('Verification')
    expect(wrapper.text()).not.toContain('Confirm')
  })

  it('hides a verdict an earlier run left on a formula-less row', async () => {
    focusedAssignment = assignment({ formula: null })
    verdictRecord = VERDICT
    const wrapper = await mountPane()

    expect(wrapper.find('.verdict-badge').exists()).toBe(false)
  })

  it('treats an empty formula as no formula', async () => {
    focusedAssignment = assignment({ formula: '', tier: 'below_assignability' })
    const wrapper = await mountPane()

    expect(wrapper.find('.verify').exists()).toBe(false)
  })

  it('still verifies a real assignment', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'identified' })
    const wrapper = await mountPane()

    expect(wrapper.find('.verify').exists()).toBe(true)
    expect(wrapper.vm.showVerifyForm).toBe(true)
    expect(wrapper.text()).toContain('Confirm')
  })

  it('shows the badge for a verified assignment', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'identified' })
    verdictRecord = VERDICT
    const wrapper = await mountPane()

    expect(wrapper.find('.verdict-badge').exists()).toBe(true)
  })
})
