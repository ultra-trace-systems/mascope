import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

import { num } from '@/lib/formatters'

// The inspector card for a peak with no committed formula. Two states reach it:
// a ledger row of tier `unassigned` (a real assignment row, just formula-less),
// and a focused peak with no assignment row at all (no run yet). Both used to
// read "Unassigned" over an empty evidence grid, saying nothing about which
// peak they were - and the first one offered a verdict form on nothing.

const PEAK = { peak_id: 'p-1', mz: 200.12345, height: 12345, area: 999 }

// Module-level so assertions see the same spy the component called: makeApp()
// runs afresh on every useApp() and would otherwise hand out a new one.
const verify = vi.fn(() => Promise.resolve(null))

let focusedPeak
let focusedAssignment
let ledger
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
      // `focused` is a getter over a ref so the pane's computeds actually
      // re-evaluate when a test moves the focus after mounting; a plain value
      // would be snapshotted at setup and no watcher would ever fire.
      peak: {
        list: [PEAK],
        get focused() {
          return focusedPeak.value
        },
        focus: vi.fn()
      },
      peakAssignment: {
        peak: {
          forPeak: () => focusedAssignment,
          // Keyed by id rather than answering every caller: the inspector loads
          // detail for the focused assignment alone, so anything it reads off
          // another family member has to come from that member's slim row.
          detailOf: (id) =>
            id != null && id === focusedAssignment?.peak_assignment_id ? detailRecord : null,
          familyOf: () => familyRows ?? (focusedAssignment ? [focusedAssignment] : []),
          // Stands in for the store's family resolution over whatever `ledger`
          // holds. The rule itself is pinned against the real implementation in
          // stores/data/modules/peakAssignment/assignment.spec.js; what matters
          // here is that the inspector asks for it and uses the answer.
          m0Of: (row) =>
            row?.role === 'iso_child' ? (ledger.get(row.owner_peak_assignment_id) ?? row) : row,
          loadDetail: () => Promise.resolve()
        },
        verification: { forAssignment: () => verdictRecord, verify }
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

/**
 * An isotopologue of `parent`, as the engine writes one: it carries
 * the M0's formula, tier and mechanism verbatim and differs only in which peak
 * it sits on. Registers the parent in `ledger` so `m0Of` can resolve it.
 */
function isotopologue(parent) {
  ledger.set(parent.peak_assignment_id, parent)
  return {
    ...parent,
    peak_assignment_id: 'pa-1-c0',
    sample_peak_id: 'p-2',
    sample_peak_mz: parent.sample_peak_mz + 1.00336,
    sample_peak_intensity: 60,
    role: 'iso_child',
    owner_peak_assignment_id: parent.peak_assignment_id,
    isotope_label: 'M+1'
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
  focusedPeak = ref(PEAK)
  focusedAssignment = null
  ledger = new Map()
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
    focusedPeak.value = { peak_id: 'p-1', mz: 200.12345, height: null }
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

// Adduct corroboration is written onto the M0 winner alone: an isotopologue is the
// same ion measured at another isotope, not a second sighting of the compound,
// so the backend leaves its provenance without one by construction. The badge
// still belongs on a focused isotopologue - the evidence is about the formula the
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
  const ISOTOPOLOGUE = {
    peak_assignment_id: 'pa-c1',
    sample_peak_id: 'p-1',
    owner_peak_assignment_id: 'pa-m0',
    sample_peak_mz: 201.12678,
    sample_peak_intensity: 1200,
    assigned_formula: 'C10H12',
    tier: 'assigned',
    role: 'iso_child',
    isotope_label: 'M+1',
    // What the backend actually sends for an isotopologue.
    corroboration_adducts: null,
    fit_score: 0.9
  }

  /** Focus the isotopologue of a family whose M0 carries `n` corroborating adducts. */
  function focusIsotopologue(n) {
    focusedAssignment = ISOTOPOLOGUE
    familyRows = [{ ...M0, corroboration_adducts: n }, ISOTOPOLOGUE]
  }

  const badge = (wrapper) => wrapper.find('.corroboration')

  it('shows the family corroboration on a focused isotopologue, marked inherited', async () => {
    focusIsotopologue(3)
    const wrapper = await mountPane()

    expect(badge(wrapper).exists()).toBe(true)
    expect(badge(wrapper).classes()).toContain('inherited')
    // The qualifier is on the badge's face, not only in the tooltip: the count is
    // the same number the M0 shows, and unqualified it would read as this peak
    // having been seen through three adducts itself.
    expect(badge(wrapper).text()).toContain('Supported by 3 adducts via M0')
  })

  // The engine folds the boost into the record carrying the corroboration - the
  // M0's p_correct - and never into an isotopologue's, which stays calibrated on its
  // own evidence. The inspector renders that isotopologue's own P(correct) directly
  // above this badge, so claiming the boost is in it would be false.
  it('does not claim the boost is in the isotopologue own P(correct)', async () => {
    focusIsotopologue(3)
    const wrapper = await mountPane()

    expect(wrapper.vm.corroborationTooltip).toBe(
      'The M0 of this isotopologue family was seen via 3 adducts. ' +
        "Independent corroborating evidence for the formula, folded into the M0's " +
        "P(correct) - not into this isotopologue's, which is calibrated on its own."
    )
  })

  // An imported ledger is not bound by the in-app engine's winner-only rule, so a
  // isotopologue that carries its own count keeps it - and it is not "via M0".
  it('prefers an isotopologue own count over the family one', async () => {
    focusedAssignment = { ...ISOTOPOLOGUE, corroboration_adducts: 2 }
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
    focusedAssignment = ISOTOPOLOGUE
    familyRows = [{ ...M0, provenance: { corroboration: { n_adducts: 4 } } }, ISOTOPOLOGUE]
    const wrapper = await mountPane()

    expect(badge(wrapper).text()).toContain('Supported by 4 adducts via M0')
    expect(badge(wrapper).classes()).toContain('inherited')
  })

  it('says nothing when the family M0 was not corroborated', async () => {
    focusIsotopologue(null)
    const wrapper = await mountPane()

    expect(badge(wrapper).exists()).toBe(false)
  })

  // The badge is gated on more than one adduct: a lone sighting corroborates
  // nothing, and must not become "Supported by 1 adducts" on the isotopologues.
  it('does not spread a single-adduct M0 onto its isotopologues', async () => {
    focusIsotopologue(1)
    const wrapper = await mountPane()

    expect(badge(wrapper).exists()).toBe(false)
  })

  it('names the adducts on the M0 itself, and does not mark it inherited', async () => {
    focusedAssignment = { ...M0, corroboration_adducts: 2 }
    familyRows = [focusedAssignment, ISOTOPOLOGUE]
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

// An M+1 is not a second finding to judge - it is the same compound seen through
// one heavy atom. Focusing one used to open an empty verify form beside a
// confirmed M0, and confirming there wrote a second label against the isotopologue
// peak. The card now reads and writes through the compound whichever family
// member is in view.
describe('PanePeakAssign verification on an isotopologue', () => {
  const M0 = assignment({ formula: 'C10H12', tier: 'assigned' })

  it('verifies the compound rather than refusing the isotopologue', async () => {
    focusedAssignment = isotopologue(M0)
    const wrapper = await mountPane()

    expect(wrapper.find('.verify').exists()).toBe(true)
    expect(wrapper.vm.verifyTarget).toBe(M0)
  })

  it('says which row the verdict is really about', async () => {
    focusedAssignment = isotopologue(M0)
    const wrapper = await mountPane()
    const note = wrapper.find('.verify-family')

    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('C10H12')

    // Nothing to explain when the compound's own peak is in view.
    focusedAssignment = M0
    const onM0 = await mountPane()
    expect(onM0.find('.verify-family').exists()).toBe(false)
  })

  it('records the verdict against the compound, not the isotopologue peak', async () => {
    focusedAssignment = isotopologue(M0)
    const wrapper = await mountPane()

    await wrapper.vm.submitVerdict('rejected')

    expect(verify).toHaveBeenCalledTimes(1)
    expect(verify.mock.calls[0][0]).toMatchObject({
      peak_assignment_id: M0.peak_assignment_id,
      verdict: 'rejected'
    })
  })

  // The isotopologue carries the M0's formula verbatim, so the formula gate never
  // had anything to say here. An isotopologue with no owner in the ledger stands for
  // itself and is judged on its own formula rather than becoming unverifiable.
  // The engine produces these routinely - `owner_peak_assignment_id` stays null
  // when the ion's M0 peak was won by another ion in that run - so this is an
  // ordinary row, not a corrupt one.
  it('still verifies an isotopologue that has no owner in the ledger', async () => {
    const orphan = { ...isotopologue(M0), owner_peak_assignment_id: null }
    focusedAssignment = orphan
    const wrapper = await mountPane()

    expect(wrapper.vm.verifyTarget).toBe(orphan)
    await wrapper.vm.submitVerdict('unsure')
    expect(verify.mock.calls[0][0].peak_assignment_id).toBe(orphan.peak_assignment_id)
  })

  // ...and it must not claim a scope it does not have. Such a verdict covers
  // that peak alone: there is no M0 to hang it on and no sibling to share it
  // with, so the family note would be a promise the write does not keep.
  it('claims no family for an isotopologue that stands for itself', async () => {
    focusedAssignment = { ...isotopologue(M0), owner_peak_assignment_id: null }
    const wrapper = await mountPane()

    expect(wrapper.vm.verifyingFamily).toBe(false)
    expect(wrapper.find('.verify-family').exists()).toBe(false)
  })

  // The form judges the compound, so stepping between members of one family is
  // still the same judgment. Keyed on the focused peak, the reset watcher threw
  // a half-written verdict away on a click inside the family table this very
  // card renders.
  it('keeps a half-written verdict while focus moves within the family', async () => {
    const child = isotopologue(M0)
    focusedAssignment = child
    focusedPeak.value = { ...PEAK, peak_id: child.sample_peak_id }
    const wrapper = await mountPane()

    wrapper.vm.evidenceLevel = 'msms'
    wrapper.vm.note = 'matches the standard'
    await wrapper.vm.$nextTick()

    // Step to the compound's own peak - a different peak, but the same compound
    // and so the same judgment. This is one click in the family table the card
    // itself renders.
    focusedAssignment = M0
    focusedPeak.value = { ...PEAK, peak_id: M0.sample_peak_id }
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.verifyTarget).toBe(M0)
    expect(wrapper.vm.evidenceLevel).toBe('msms')
    expect(wrapper.vm.note).toBe('matches the standard')
  })

  it('starts a fresh form on a different compound', async () => {
    focusedAssignment = M0
    const wrapper = await mountPane()

    wrapper.vm.evidenceLevel = 'msms'
    wrapper.vm.note = 'matches the standard'
    await wrapper.vm.$nextTick()

    focusedAssignment = {
      ...M0,
      peak_assignment_id: 'pa-2',
      sample_peak_id: 'p-9',
      assigned_formula: 'C6H6'
    }
    focusedPeak.value = { ...PEAK, peak_id: 'p-9' }
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.evidenceLevel).toBeNull()
    expect(wrapper.vm.note).toBe('')
  })
})

// The engine no longer writes the committed assignment into its own
// `alternatives`, but every run stored before it learned not to still carries
// it, and only re-running the sample rewrites those rows. So the card screens
// the list as well, rather than offering the analyst the peak's own answer as
// an alternative to itself.
describe('PanePeakAssign close alternatives', () => {
  const COMMITTED = {
    ...assignment({ formula: 'C6H12O6', tier: 'assigned' }),
    ion_formula: 'C6H13O6+'
  }
  const alts = (wrapper) => wrapper.findAll('.alt').map((node) => node.find('.f').text())

  beforeEach(() => {
    focusedAssignment = COMMITTED
  })

  it('drops the entry that restates the committed assignment', async () => {
    detailRecord = {
      alternatives: [
        { assigned_formula: 'C6H12O6', ion_formula: 'C6H13O6+', fit_score: 0.8 },
        { assigned_formula: 'C7H16O5', ion_formula: 'C7H17O5+', fit_score: 0.7 }
      ]
    }
    const wrapper = await mountPane()

    expect(alts(wrapper)).toEqual(['C7H16O5'])
    // The count in the label is the shown list, not the stored one.
    expect(wrapper.find('.alts-count').text()).toBe('1')
  })

  // The untargeted shortlist is formula-only, so a missing `ion_formula` is not
  // evidence of a different mechanism - it is the winner with less detail.
  it('drops a formula-only entry naming the committed formula', async () => {
    detailRecord = {
      alternatives: [
        { assigned_formula: 'C6H12O6', plausibility: 0.9 },
        { assigned_formula: 'C4H8N2O', plausibility: 0.6 }
      ]
    }
    const wrapper = await mountPane()

    expect(alts(wrapper)).toEqual(['C4H8N2O'])
  })

  // One formula seen through two adducts is a real second arrival, not the
  // winner repeated, and the analyst needs to see it.
  it('keeps the same formula reached through another adduct', async () => {
    detailRecord = {
      alternatives: [
        { assigned_formula: 'C6H12O6', ion_formula: 'C6H12NaO6+', fit_score: 0.7 }
      ]
    }
    const wrapper = await mountPane()

    expect(alts(wrapper)).toEqual(['C6H12O6'])
  })

  it('hides the section when the only alternative was the assignment itself', async () => {
    detailRecord = {
      alternatives: [{ assigned_formula: 'C6H12O6', ion_formula: 'C6H13O6+', fit_score: 0.8 }]
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.alts').exists()).toBe(false)
  })

  it('leaves the list alone when the peak has no committed formula', async () => {
    focusedAssignment = assignment({ tier: 'unassigned' })
    detailRecord = { alternatives: [{ assigned_formula: 'C6H12O6', plausibility: 0.9 }] }
    const wrapper = await mountPane()

    expect(alts(wrapper)).toEqual(['C6H12O6'])
  })

  it('screens the slim row fallback too, before the detail fetch lands', async () => {
    focusedAssignment = {
      ...COMMITTED,
      alternatives: [
        { assigned_formula: 'C6H12O6', ion_formula: 'C6H13O6+', fit_score: 0.8 },
        { assigned_formula: 'C7H16O5', ion_formula: 'C7H17O5+', fit_score: 0.7 }
      ]
    }
    const wrapper = await mountPane()

    expect(alts(wrapper)).toEqual(['C7H16O5'])
  })
})
