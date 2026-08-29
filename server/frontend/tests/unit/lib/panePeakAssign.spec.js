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
// The isotopologue family of the focused row, when a test needs one, and the
// one detail record the inspector fetches (for the focused assignment only).
let familyRows
let detailRecord

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
          // Keyed by id rather than answering every caller: the inspector loads
          // detail for the focused assignment alone, so anything it reads off
          // another family member has to come from that member's slim row.
          detailOf: (id) =>
            id != null && id === focusedAssignment?.peak_assignment_id ? detailRecord : null,
          familyOf: () => familyRows ?? (focusedAssignment ? [focusedAssignment] : []),
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
  familyRows = null
  detailRecord = null
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
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
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
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
    const wrapper = await mountPane()

    expect(wrapper.find('.verify').exists()).toBe(true)
    expect(wrapper.vm.showVerifyForm).toBe(true)
    expect(wrapper.text()).toContain('Confirm')
  })

  it('shows the badge for a verified assignment', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
    verdictRecord = VERDICT
    const wrapper = await mountPane()

    expect(wrapper.find('.verdict-badge').exists()).toBe(true)
  })
})

// Adduct corroboration is written onto the M0 winner alone: a satellite is the
// same ion measured at another isotope, not a second sighting of the compound,
// so the backend leaves its provenance without one by construction. The badge
// still belongs on a focused satellite - the evidence is about the formula the
// whole family shares - it just has to say the count is the family's. The
// corroborating adducts are named only in the M0's provenance, and the
// inspector fetches detail for the focused assignment alone, so an inherited
// badge has the count and nothing else.
describe('PanePeakAssign adduct corroboration', () => {
  const M0 = {
    peak_assignment_id: 'pa-m0',
    sample_peak_id: 'p-m0',
    sample_peak_mz: 200.12345,
    sample_peak_intensity: 12345,
    assigned_formula: 'C10H12',
    tier: 'assigned',
    role: 'M0',
    fit_score: 0.9
  }
  const SATELLITE = {
    peak_assignment_id: 'pa-c1',
    sample_peak_id: 'p-1',
    owner_peak_assignment_id: 'pa-m0',
    sample_peak_mz: 201.12678,
    sample_peak_intensity: 1200,
    assigned_formula: 'C10H12',
    tier: 'assigned',
    role: 'iso_child',
    isotope_label: 'M+1',
    // What the backend actually sends for a satellite.
    corroboration_adducts: null,
    fit_score: 0.9
  }

  /** Focus the satellite of a family whose M0 carries `n` corroborating adducts. */
  function focusSatellite(n) {
    focusedAssignment = SATELLITE
    familyRows = [{ ...M0, corroboration_adducts: n }, SATELLITE]
  }

  const badge = (wrapper) => wrapper.find('.corroboration')

  it('shows the family corroboration on a focused satellite, marked inherited', async () => {
    focusSatellite(3)
    const wrapper = await mountPane()

    expect(badge(wrapper).exists()).toBe(true)
    expect(badge(wrapper).classes()).toContain('inherited')
    // The qualifier is on the badge's face, not only in the tooltip: the count is
    // the same number the M0 shows, and unqualified it would read as this peak
    // having been seen through three adducts itself.
    expect(badge(wrapper).text()).toContain('Supported by 3 adducts via M0')
  })

  // The engine folds the boost into the record carrying the corroboration - the
  // M0's p_correct - and never into a satellite's, which stays calibrated on its
  // own evidence. The inspector renders that satellite's own P(correct) directly
  // above this badge, so claiming the boost is in it would be false.
  it('does not claim the boost is in the satellite own P(correct)', async () => {
    focusSatellite(3)
    const wrapper = await mountPane()

    expect(wrapper.vm.corroborationTooltip).toBe(
      'The M0 of this isotopologue family was seen via 3 adducts. ' +
        "Independent corroborating evidence for the formula, folded into the M0's " +
        "P(correct) - not into this satellite's, which is calibrated on its own."
    )
  })

  // An imported ledger is not bound by the in-app engine's winner-only rule, so a
  // satellite that carries its own count keeps it - and it is not "via M0".
  it('prefers a satellite own count over the family one', async () => {
    focusedAssignment = { ...SATELLITE, corroboration_adducts: 2 }
    familyRows = [{ ...M0, corroboration_adducts: 5 }, focusedAssignment]
    const wrapper = await mountPane()

    expect(badge(wrapper).text()).toContain('Supported by 2 adducts')
    expect(badge(wrapper).text()).not.toContain('via M0')
    expect(badge(wrapper).classes()).not.toContain('inherited')
  })

  // The ledger resolves a parent's count as `corroboration_adducts ?? provenance
  // .corroboration.n_adducts`; the inspector's M0 lookup has to do the same, or
  // the two panes disagree about a family whose rows carry provenance inline.
  it('reads the family count off M0 provenance when the flat field is absent', async () => {
    focusedAssignment = SATELLITE
    familyRows = [{ ...M0, provenance: { corroboration: { n_adducts: 4 } } }, SATELLITE]
    const wrapper = await mountPane()

    expect(badge(wrapper).text()).toContain('Supported by 4 adducts via M0')
    expect(badge(wrapper).classes()).toContain('inherited')
  })

  it('says nothing when the family M0 was not corroborated', async () => {
    focusSatellite(null)
    const wrapper = await mountPane()

    expect(badge(wrapper).exists()).toBe(false)
  })

  // The badge is gated on more than one adduct: a lone sighting corroborates
  // nothing, and must not become "Supported by 1 adducts" on the satellites.
  it('does not spread a single-adduct M0 onto its satellites', async () => {
    focusSatellite(1)
    const wrapper = await mountPane()

    expect(badge(wrapper).exists()).toBe(false)
  })

  it('names the adducts on the M0 itself, and does not mark it inherited', async () => {
    focusedAssignment = { ...M0, corroboration_adducts: 2 }
    familyRows = [focusedAssignment, SATELLITE]
    detailRecord = {
      provenance: { corroboration: { n_adducts: 2, adducts: ['+H+', '+Na+'], boost: 0.4 } }
    }
    const wrapper = await mountPane()

    expect(badge(wrapper).text()).toContain('Supported by 2 adducts')
    expect(badge(wrapper).classes()).not.toContain('inherited')
    expect(wrapper.vm.corroborationTooltip).toContain('Seen via 2 adducts (+H+, +Na+)')
  })

  // The count is flattened onto every ledger row, so the M0's own badge is there
  // as soon as the row is, rather than popping in when the detail fetch lands.
  it('shows the M0 badge from the slim row before its detail arrives', async () => {
    focusedAssignment = { ...M0, corroboration_adducts: 2 }
    const wrapper = await mountPane()

    expect(badge(wrapper).text()).toContain('Supported by 2 adducts')
    expect(badge(wrapper).classes()).not.toContain('inherited')
    // No adduct names to give yet, so the tooltip promises none.
    expect(wrapper.vm.corroborationTooltip).toContain('Seen via 2 adducts.')
  })

  it('leaves a peak with no family and no corroboration alone', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
    const wrapper = await mountPane()

    expect(badge(wrapper).exists()).toBe(false)
  })
})
