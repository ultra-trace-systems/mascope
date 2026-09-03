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
const curate = vi.fn(() => Promise.resolve(null))
const loadAltScores = vi.fn(() => Promise.resolve([]))

let focusedPeak
let focusedAssignment
let ledger
let verdictRecord
// The isotopologue family of the focused row, when a test needs one, and the
// one detail record the inspector fetches (for the focused assignment only).
let familyRows
let detailRecord
// The focused sample's run record; `{ engine: 'batch' }` is a derived ledger.
let runRecord
// The on-demand measurement of the finder's formula-only shortlist: null until
// it lands, and `scoringNow` stands in for the request still being in flight.
let altScoreRecords
let scoringNow

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
          run: runRecord,
          // Stands in for the store's family resolution over whatever `ledger`
          // holds. The rule itself is pinned against the real implementation in
          // stores/data/modules/peakAssignment/assignment.spec.js; what matters
          // here is that the inspector asks for it and uses the answer.
          m0Of: (row) =>
            row?.role === 'iso_child' ? (ledger.get(row.owner_peak_assignment_id) ?? row) : row,
          loadDetail: () => Promise.resolve(),
          // The scores are keyed by assignment id like the detail is, and the
          // pane must not read another row's measurement onto this one.
          altScoresOf: (id) =>
            id != null && id === focusedAssignment?.peak_assignment_id ? altScoreRecords : null,
          altScoresPending: () => scoringNow,
          loadAltScores,
          curate
        },
        verification: { forAssignment: () => verdictRecord, verify },
        anchorContext: { overlayFor: () => anchorVerdictRecord }
      }
    },
    ui: { help: helpStub }
  }
}

// The batch-level verdict reaching the focused peak; null unless a test sets one.
let anchorVerdictRecord = null

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

// The batch curation a derived row's "use this" goes through.
const batchCurate = vi.fn()
const batchRelease = vi.fn()
vi.mock('@/lib/panes/PanePeakAssign/stores/batchPeakCuration.js', () => ({
  useBatchPeakCuration: () => ({ curating: false, curate: batchCurate, release: batchRelease })
}))

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
  runRecord = null
  detailRecord = null
  altScoreRecords = null
  scoringNow = false
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
  // M0's p_correct - and never into a child's, which stays calibrated on its
  // own evidence. The inspector renders that isotopologue's own P(correct) directly
  // above this badge, so claiming the boost is in it would be false.
  it('does not claim the boost is in the P(correct) of the isotopologue itself', async () => {
    focusIsotopologue(3)
    const wrapper = await mountPane()

    expect(wrapper.vm.corroborationTooltip).toBe(
      'The M0 of this isotopologue family was seen via 3 adducts. ' +
        "Independent corroborating evidence for the formula, folded into the M0's " +
        "P(correct) - not into this isotopologue's, which is calibrated on its own."
    )
  })

  // An imported ledger is not bound by the in-app engine's winner-only rule, so an
  // isotopologue that carries its own count keeps it - and it is not "via M0".
  it('prefers the count on the isotopologue itself over the family one', async () => {
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
      alternatives: [{ assigned_formula: 'C6H12O6', ion_formula: 'C6H12NaO6+', fit_score: 0.7 }]
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

// Manual curation: committing a close alternative from the card. The control is
// per-alternative because the index it sends only means anything against the
// list this card is showing.
describe('PanePeakAssign manual curation', () => {
  const ALTERNATIVES = [
    // A scored runner-up, carrying the adduct it was scored under.
    {
      assigned_formula: 'C7H16O5',
      ionization_mechanism_id: 'im-h',
      fit_score: 0.71,
      mz_error_ppm: 4.2,
      source: 'database'
    },
    // A runner-up that names no formula at all - there is nothing to commit.
    { ion_formula: 'C3H7+', plausibility: 0.6, source: 'untargeted' },
    // Written before alternatives carried the mechanism: the target ion it was
    // scored against still resolves to one, so this is committable too.
    { assigned_formula: 'C9H8', target_ion_id: 'ti-9', fit_score: 0.6, source: 'database' },
    // The untargeted finder's formula-only shortlist: a composition and a
    // plausibility and nothing else, so there is no adduct to assign it under.
    { assigned_formula: 'C4H8N2O3', plausibility: 0.44, source: 'untargeted' }
  ]

  // The controls that would actually commit something, and the ones that are
  // there only to say why they cannot.
  const useButtons = (wrapper) => wrapper.findAll('.alt .alt-use:not(.blocked)')
  const blockedButtons = (wrapper) => wrapper.findAll('.alt .alt-use.blocked')

  beforeEach(() => {
    focusedAssignment = { ...assignment({ formula: 'C6H12O6', tier: 'assigned' }) }
    detailRecord = { ...focusedAssignment, alternatives: ALTERNATIVES }
  })

  it('offers the action only on candidates that name a formula and an adduct', async () => {
    const wrapper = await mountPane()

    expect(wrapper.findAll('.alt')).toHaveLength(4)
    expect(useButtons(wrapper)).toHaveLength(2)
    // The shortlist entry gets a control that explains itself; the entry with
    // no formula at all gets none, because there is nothing to explain.
    expect(blockedButtons(wrapper)).toHaveLength(1)
  })

  // A verification's identity is peak + formula + mechanism, so the server
  // refuses an adductless formula with a 422. Offering the button and letting
  // the click fail would be the card promising something it cannot do.
  it('will not commit a formula with no adduct, and says where one comes from', async () => {
    const wrapper = await mountPane()
    const blocked = blockedButtons(wrapper)[0]

    expect(blocked.attributes('disabled')).toBeDefined()
    await blocked.trigger('click')
    expect(curate).not.toHaveBeenCalled()

    // The reason rides on the row's own tooltip as well as the control: the
    // control only fades in on hover and is disabled, so a tooltip bound to it
    // alone would seldom be reachable.
    const reason = wrapper.vm.altTooltip(ALTERNATIVES[3])
    expect(reason).toContain('Not assignable to this peak')
    expect(reason).toContain('Re-search the peak')
    expect(wrapper.vm.altTooltip(ALTERNATIVES[0])).not.toContain('Not assignable')
  })

  it('accepts either the recorded mechanism or the target ion as the adduct', async () => {
    const wrapper = await mountPane()

    expect(
      wrapper.vm.canPromote({ assigned_formula: 'C9H8', ionization_mechanism_id: 'im-h' })
    ).toBe(true)
    expect(wrapper.vm.canPromote({ assigned_formula: 'C9H8', target_ion_id: 'ti-9' })).toBe(true)
    expect(wrapper.vm.canPromote({ assigned_formula: 'C9H8', plausibility: 0.5 })).toBe(false)
    expect(wrapper.vm.canPromote({ ion_formula: 'C3H7+', ionization_mechanism_id: 'im-h' })).toBe(
      false
    )
  })

  it('commits the candidate at its own index, guarded by the formula shown', async () => {
    const wrapper = await mountPane()

    // The second committable candidate is the third alternative: the index sent
    // is the row's own position in the list the card is showing.
    await useButtons(wrapper)[1].trigger('click')

    expect(curate).toHaveBeenCalledTimes(1)
    expect(curate).toHaveBeenCalledWith('pa-1', {
      action: 'promote_alternative',
      alternative_index: 2,
      expected_formula: 'C9H8'
    })
  })

  // The guard is the point: without it, a click on the card another curator has
  // already changed underneath would commit whatever now sits at that position.
  it('sends the index of the row clicked, not of the promotable ones', async () => {
    const wrapper = await mountPane()

    await useButtons(wrapper)[0].trigger('click')

    expect(curate.mock.calls[0][1]).toMatchObject({
      alternative_index: 0,
      expected_formula: 'C7H16O5'
    })
  })

  it('hides the control when the write comes back 403, and says why', async () => {
    curate.mockRejectedValueOnce(Object.assign(new Error('no'), { response: { status: 403 } }))
    const wrapper = await mountPane()

    await useButtons(wrapper)[0].trigger('click')
    await wrapper.vm.$nextTick()

    expect(useButtons(wrapper)).toHaveLength(0)
    // Including the one that only explains itself: without the right to change
    // an assignment, why a particular candidate cannot be committed is moot.
    expect(blockedButtons(wrapper)).toHaveLength(0)
    expect(wrapper.text()).toContain('Editor access is required to change an assignment')
  })

  // Any other failure has already been toasted by the http layer; the control
  // stays, because the user may well be able to retry.
  it('keeps the control after a failure that is not a refusal', async () => {
    curate.mockRejectedValueOnce(new Error('503 Service Unavailable'))
    const wrapper = await mountPane()

    await useButtons(wrapper)[0].trigger('click')
    await wrapper.vm.$nextTick()

    expect(useButtons(wrapper)).toHaveLength(2)
  })

  it('says a curated row was decided by hand, and how to undo it', async () => {
    focusedAssignment = { ...focusedAssignment, source: 'manual' }
    detailRecord = {
      ...focusedAssignment,
      alternatives: ALTERNATIVES,
      provenance: { manual: { action: 'promote_alternative', previous_formula: 'C6H12O6' } }
    }
    const wrapper = await mountPane()

    const note = wrapper.find('.manual-note').text()
    expect(note).toContain('Assigned by hand')
    expect(note).toContain('C6H12O6')
    expect(note).toContain('use this')
  })

  /**
   * The manual block of a row curated away from C6H12O6, archiving one demoted
   * satellite per entry in `demoted` - keyed, as the server keys the restore,
   * on the compound the satellite was taken under.
   */
  function override(demoted = []) {
    return {
      manual: {
        action: 'promote_alternative',
        previous_formula: 'C6H12O6',
        previous: { assigned_formula: 'C6H12O6', ionization_mechanism_id: 'im-h' },
        demoted: demoted.map((entry, i) => ({
          peak_assignment_id: `pa-c${i}`,
          owner_formula: 'C6H12O6',
          owner_ionization_mechanism_id: 'im-h',
          ...entry
        }))
      }
    }
  }

  // The undo is not only about this row: the override unassigned the replaced
  // compound's isotopologues too, and committing that compound again brings
  // them back. A person who is told only about the first alternative would not
  // expect two other peaks to change.
  it('says the undo brings the replaced compound isotopologues back', async () => {
    focusedAssignment = { ...focusedAssignment, source: 'manual' }
    detailRecord = {
      ...focusedAssignment,
      alternatives: ALTERNATIVES,
      provenance: override([{}, {}])
    }
    const wrapper = await mountPane()

    const note = wrapper.find('.manual-note').text()
    expect(note).toContain('puts back the 2 isotopologue satellites unassigned with it')
    // The restore skips a satellite someone has curated since, so the note must
    // not promise all of them come back.
    expect(note).toContain('except any of them assigned by hand since')
  })

  it('promises no restore for an override that unassigned nobody', async () => {
    focusedAssignment = { ...focusedAssignment, source: 'manual' }
    detailRecord = {
      ...focusedAssignment,
      alternatives: ALTERNATIVES,
      provenance: override()
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').text()).not.toContain('isotopologue')
  })

  it('counts one restored satellite in the singular', async () => {
    focusedAssignment = { ...focusedAssignment, source: 'manual' }
    detailRecord = { ...focusedAssignment, provenance: override([{}]) }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').text()).toContain('the 1 isotopologue satellite ')
  })

  // Curating one row twice carries the first override's archive forward, so the
  // archive can hold satellites taken under a compound the undo would not
  // commit. Those come back with THEIR compound, not with this one - counting
  // them here would promise peaks the click does not touch.
  it('counts only the satellites the first alternative would bring back', async () => {
    focusedAssignment = { ...focusedAssignment, source: 'manual' }
    detailRecord = {
      ...focusedAssignment,
      provenance: override([
        {},
        { owner_formula: 'C5H10O5' },
        { owner_ionization_mechanism_id: 'im-na' }
      ])
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').text()).toContain('the 1 isotopologue satellite ')
  })

  // `source` rides on the slim ledger row while provenance waits on the detail
  // fetch, so the note must stand on its own until the formula arrives.
  it('says so before the detail carrying the replaced formula lands', async () => {
    focusedAssignment = { ...focusedAssignment, source: 'manual' }
    detailRecord = null
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').exists()).toBe(true)
    expect(wrapper.find('.manual-note').text()).toContain('Assigned by hand')
  })

  it('leaves an engine-assigned row unmarked', async () => {
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').exists()).toBe(false)
  })
})

// The backend marks a stripped satellite 'manual' too, so the ledger's source
// filter shows the whole footprint of an override rather than only the row that
// gained a formula. Such a row had nothing assigned to it: it was cleared
// because its M0 was reassigned under it. Read as an override it claimed a
// person had picked this peak's (absent) formula "in place of" the compound it
// had actually belonged to, which inverts the relationship.
describe('PanePeakAssign a satellite stripped by an override', () => {
  const DEMOTED = {
    ...assignment({ formula: null }),
    peak_assignment_id: 'pa-c1',
    source: 'manual',
    tier: 'unassigned',
    role: 'unassigned'
  }
  const PROVENANCE = {
    manual: {
      action: 'demote_satellite',
      reason: 'owner_overridden',
      previous_formula: 'C6H12O6',
      previous_owner_formula: 'C6H12O6'
    }
  }

  beforeEach(() => {
    focusedAssignment = DEMOTED
    detailRecord = { ...DEMOTED, provenance: PROVENANCE }
  })

  it('does not read as a formula somebody chose', async () => {
    const wrapper = await mountPane()
    const note = wrapper.find('.manual-note').text()

    expect(note).not.toContain('Assigned by hand')
    expect(note).not.toContain('in place of')
  })

  it('says what happened to the row and names the compound it belonged to', async () => {
    const wrapper = await mountPane()
    const note = wrapper.find('.manual-note').text()

    expect(note).toContain('Unassigned by hand')
    expect(note).toContain('this peak was an isotopologue of C6H12O6')
    expect(note).toContain('Assigning C6H12O6 there again restores this row')
  })

  // A satellite carries its M0's formula verbatim, so the engine writes both
  // keys with the same value; an imported run may carry only one of them.
  it('falls back to the formula the row itself held', async () => {
    detailRecord = {
      ...DEMOTED,
      provenance: { manual: { action: 'demote_satellite', previous_formula: 'C6H12O6' } }
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').text()).toContain('isotopologue of C6H12O6')
  })

  // `source` is on the slim row and the action is not, so the note has to pick
  // its side before the detail lands. Curating a peak always puts a formula on
  // it, which makes a formula-less 'manual' row a demotion.
  it('reads as a demotion before the detail arrives, without naming a compound', async () => {
    detailRecord = null
    const wrapper = await mountPane()
    const note = wrapper.find('.manual-note').text()

    expect(note).toContain(
      'Unassigned by hand when the compound this peak was an isotopologue of was replaced'
    )
    expect(note).not.toContain('Assigned by hand')
  })

  // Curating a demoted row assigns it in its own right - it is no longer part
  // of anyone's family - so the override note is the true one again.
  it('reads as an override once someone assigns the row by hand', async () => {
    focusedAssignment = { ...DEMOTED, assigned_formula: 'C4H8N2O3', tier: 'candidate' }
    detailRecord = {
      ...focusedAssignment,
      provenance: { manual: { action: 'set_assignment' } }
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').text()).toContain('Assigned by hand')
  })
})

// Two surfaces describe a row an override stripped: the tier chip and this
// note. BaseTierTag.vue marks such a row with an eraser and deliberately not
// with the hand - the hand says "a person chose this", which is the one claim a
// stripped row cannot make - and the two sit on the same screen, so they have
// to agree about the glyph AND about how loudly it is drawn (the classes are
// what the recessive treatment keys on).
describe('PanePeakAssign manual note marks', () => {
  it('marks a row somebody chose with the hand, at full strength', async () => {
    focusedAssignment = {
      ...assignment({ formula: 'C6H12O6', tier: 'assigned' }),
      source: 'manual'
    }
    detailRecord = {
      ...focusedAssignment,
      provenance: { manual: { action: 'promote_alternative', previous_formula: 'C5H10O5' } }
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note .manual-icon').classes()).toContain('ph-hand-pointing')
    expect(wrapper.find('.manual-note .ph-eraser').exists()).toBe(false)
  })

  it('marks a row the override stripped with the eraser, recessive', async () => {
    focusedAssignment = {
      ...assignment({ formula: null }),
      peak_assignment_id: 'pa-c1',
      source: 'manual'
    }
    detailRecord = {
      ...focusedAssignment,
      provenance: { manual: { action: 'demote_satellite', previous_owner_formula: 'C6H12O6' } }
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note .demoted-icon').classes()).toContain('ph-eraser')
    expect(wrapper.find('.manual-note .ph-hand-pointing').exists()).toBe(false)
  })
})

// A winner can be committed with no ionization mechanism at all: the untargeted
// stage writes one when the finder echoes a notation the mechanism map does not
// hold, and an imported run reaches the same state trivially, since an imported
// row may only ever have carried a formula. Override such a row and the winner
// it displaced - archived, and pushed to the head of the alternatives as the
// undo entry - is refused by the same 422 that refuses any adductless
// candidate. So the undo is not merely inconvenient here, it does not exist:
// re-searching assigns the formula under a real adduct, which is a new
// assignment, and the satellites this override unassigned are restored by
// compound AND mechanism, so they stay unassigned.
describe('PanePeakAssign an override whose previous winner named no adduct', () => {
  // `_previous_winner` drops the keys it has no value for, so an adductless
  // winner is archived without `ionization_mechanism_id` at all.
  const PREVIOUS = {
    assigned_formula: 'C6H12O6',
    ion_formula: 'C6H13O6+',
    fit_score: 0.55,
    tier: 'candidate',
    source: 'untargeted'
  }
  // An ordinary formula-only shortlist entry, further down the same list: it is
  // blocked for the same reason and re-searching really is its route.
  const SHORTLIST = { assigned_formula: 'C9H8', plausibility: 0.5, source: 'untargeted' }
  const CURATED = { ...assignment({ formula: 'C4H8N2O3', tier: 'candidate' }), source: 'manual' }

  /** The manual block of an override that displaced `previous`. */
  function override(demoted = [], previous = PREVIOUS) {
    return {
      manual: {
        action: 'promote_alternative',
        previous_formula: previous.assigned_formula,
        previous,
        demoted: demoted.map((entry, i) => ({
          peak_assignment_id: `pa-c${i}`,
          owner_formula: previous.assigned_formula,
          owner_ionization_mechanism_id: previous.ionization_mechanism_id ?? null,
          ...entry
        }))
      }
    }
  }

  const useButtons = (wrapper) => wrapper.findAll('.alt .alt-use:not(.blocked)')
  const blockedButtons = (wrapper) => wrapper.findAll('.alt .alt-use.blocked')

  beforeEach(() => {
    focusedAssignment = CURATED
    detailRecord = {
      ...CURATED,
      alternatives: [PREVIOUS, SHORTLIST],
      provenance: override([{}])
    }
  })

  it('offers no working undo - the archived winner is as unassignable as any', async () => {
    const wrapper = await mountPane()

    expect(useButtons(wrapper)).toHaveLength(0)
    expect(blockedButtons(wrapper)).toHaveLength(2)

    await blockedButtons(wrapper)[0].trigger('click')
    expect(curate).not.toHaveBeenCalled()
  })

  // The undo entry is not a candidate the finder listed, and re-searching does
  // not put its family back - so it says what it cannot do and what re-search
  // gives instead, rather than wearing the ordinary shortlist wording.
  it('says why the undo cannot be done here, and what re-search gives instead', async () => {
    const wrapper = await mountPane()
    const reason = wrapper.vm.altTooltip(PREVIOUS, 0)

    expect(reason).toContain('Cannot be undone here')
    expect(reason).toContain('The assignment this replaced named no adduct')
    expect(reason).toContain('that is a new assignment')
    expect(reason).toContain('stay cleared')
    expect(reason).not.toContain('Not assignable to this peak')
  })

  it('still points a shortlist candidate on the same list at the search', async () => {
    const wrapper = await mountPane()
    const reason = wrapper.vm.altTooltip(SHORTLIST, 1)

    expect(reason).toContain('Not assignable to this peak')
    expect(reason).toContain('Re-search the peak to look wider than this shortlist')
    expect(reason).not.toContain('Cannot be undone here')
  })

  // The undo wording is earned by being the archived winner, not by sitting
  // first: on a row nobody has curated, the head is just the best runner-up.
  it('reads the head of an engine row as a shortlist candidate', async () => {
    focusedAssignment = assignment({ formula: 'C4H8N2O3', tier: 'candidate' })
    detailRecord = { ...focusedAssignment, alternatives: [PREVIOUS, SHORTLIST] }
    const wrapper = await mountPane()

    expect(wrapper.vm.altTooltip(PREVIOUS, 0)).toContain('Not assignable to this peak')
  })

  it('does not promise an undo the card cannot perform', async () => {
    const wrapper = await mountPane()
    const note = wrapper.find('.manual-note').text()

    expect(note).toContain('in place of C6H12O6')
    expect(note).toContain('cannot be put back by hand')
    expect(note).toContain('the 1 isotopologue satellite unassigned with it stays unassigned')
    expect(note).not.toContain('on it to undo')
  })

  it('keeps the undo wording when the archived winner did name an adduct', async () => {
    const previous = { ...PREVIOUS, ionization_mechanism_id: 'im-h' }
    detailRecord = {
      ...CURATED,
      alternatives: [previous, SHORTLIST],
      provenance: override([{}], previous)
    }
    const wrapper = await mountPane()
    const note = wrapper.find('.manual-note').text()

    expect(note).toContain('on it to undo')
    expect(note).toContain('puts back the 1 isotopologue satellite')
    expect(note).not.toContain('cannot be put back')
  })

  // Nothing archived at all: an override written before the archive existed, or
  // the detail fetch still in flight. Guessing "unassignable" there would put a
  // refusal on the common case, so the wording stays the ordinary one.
  // The measurement can find an adduct for the displaced winner as readily as
  // for any other formula, and letting it enable the control would be the card
  // contradicting its own note: the satellites this override cleared are put
  // back by compound AND adduct, and were archived with none, so an adduct
  // found now can never match. The undo stays refused, and stays explained.
  it('will not let a measurement turn the undo entry into a working control', async () => {
    focusedAssignment = CURATED
    detailRecord = {
      ...CURATED,
      alternatives: [PREVIOUS, SHORTLIST],
      provenance: override([{}])
    }
    altScoreRecords = [
      {
        alternative_index: 0,
        assigned_formula: PREVIOUS.assigned_formula,
        plausibility: 0.9,
        adducts_tried: 3,
        adducts_matched: 1,
        fit_score: 0.93,
        mz_error_ppm: 0.2,
        ionization_mechanism_id: 'im-nh4',
        ionization_mechanism: '[M+NH4]+',
        isotope_label: 'M0'
      }
    ]
    const wrapper = await mountPane()

    expect(useButtons(wrapper)).toHaveLength(0)
    expect(wrapper.vm.alternatives[0].scored).toBeNull()
    expect(wrapper.vm.altTooltip(wrapper.vm.alternatives[0], 0)).toContain('Cannot be undone here')
    // The note and the control still agree with each other.
    expect(wrapper.find('.manual-note').text()).toContain('cannot be put back by hand')
  })

  // The same measurement on an ordinary shortlist entry lower down is used
  // normally: it is the undo, not the measurement, that is impossible.
  it('still uses a measurement on an ordinary entry of the same list', async () => {
    focusedAssignment = CURATED
    detailRecord = {
      ...CURATED,
      alternatives: [PREVIOUS, SHORTLIST],
      provenance: override([{}])
    }
    altScoreRecords = [
      {
        alternative_index: 1,
        assigned_formula: SHORTLIST.assigned_formula,
        plausibility: 0.5,
        adducts_tried: 3,
        adducts_matched: 1,
        fit_score: 0.71,
        mz_error_ppm: -0.3,
        abundance_error: 0.01,
        ionization_mechanism_id: 'im-h',
        ionization_mechanism: '[M+H]+',
        ion_formula: 'C9H9+',
        isotope_label: 'M0'
      }
    ]
    const wrapper = await mountPane()

    expect(useButtons(wrapper)).toHaveLength(1)
    await useButtons(wrapper)[0].trigger('click')
    expect(curate).toHaveBeenCalledWith('pa-1', {
      action: 'set_assignment',
      assigned_formula: 'C9H8',
      ionization_mechanism_id: 'im-h',
      ion_formula: 'C9H9+',
      isotope_label: 'M0',
      fit_score: 0.71,
      mz_error_ppm: -0.3,
      abundance_error: 0.01
    })
  })

  it('assumes the ordinary undo when nothing was archived', async () => {
    detailRecord = {
      ...CURATED,
      provenance: { manual: { action: 'promote_alternative', previous_formula: 'C6H12O6' } }
    }
    const wrapper = await mountPane()

    expect(wrapper.find('.manual-note').text()).toContain('on it to undo')
  })
})

// The untargeted finder's shortlist reaches a row as formulas and chemical
// plausibilities only: the run does not measure them, because doing it for
// every peak of a sample is one isotope-envelope match per candidate. For a
// single peak it is cheap, so the inspector asks the server to measure them
// when it loads a row that has any - and an entry that comes back with an
// adduct is assignable, where before it was permanently dead weight.
describe('PanePeakAssign scoring the formula-only shortlist', () => {
  // One entry the run scored and two it did not, so the measurement's effect is
  // visible against a control that needed none.
  const SCORED_RIVAL = {
    assigned_formula: 'C7H16O5',
    ionization_mechanism_id: 'im-h',
    fit_score: 0.8,
    mz_error_ppm: 0.4,
    plausibility: 0.7,
    source: 'untargeted'
  }
  const SHORTLIST_A = { assigned_formula: 'C4H8N2O3', plausibility: 0.44, source: 'untargeted' }
  const SHORTLIST_B = { assigned_formula: 'C9H8', plausibility: 0.5, source: 'untargeted' }

  /** What the server returns for a formula it could place on the peak. */
  const measured = (formula, extra = {}) => ({
    alternative_index: 1,
    assigned_formula: formula,
    plausibility: 0.44,
    adducts_tried: 3,
    adducts_matched: 1,
    fit_score: 0.72,
    mz_error_ppm: -0.9,
    abundance_error: 0.05,
    evidence: 0.32,
    ionization_mechanism_id: 'im-nh4',
    ionization_mechanism: '[M+NH4]+',
    ion_formula: 'C4H12N3O3+',
    isotope_label: 'M0',
    ...extra
  })

  const useButtons = (wrapper) => wrapper.findAll('.alt .alt-use:not(.blocked)')
  const blockedButtons = (wrapper) => wrapper.findAll('.alt .alt-use.blocked')

  beforeEach(() => {
    focusedAssignment = { ...assignment({ formula: 'C6H12O6', tier: 'assigned' }) }
    detailRecord = {
      ...focusedAssignment,
      alternatives: [SCORED_RIVAL, SHORTLIST_A, SHORTLIST_B]
    }
  })

  it('asks for a measurement only when the row has something to measure', async () => {
    await mountPane()
    expect(loadAltScores).toHaveBeenCalledTimes(1)

    // A row whose every alternative already carries an adduct has nothing to
    // gain, and the call loads peaks and builds isotope envelopes - so it is
    // not made at all rather than made and discarded.
    loadAltScores.mockClear()
    detailRecord = { ...focusedAssignment, alternatives: [SCORED_RIVAL] }
    await mountPane()
    expect(loadAltScores).not.toHaveBeenCalled()
  })

  it('shows the measured fit and mass error on an entry that arrived with neither', async () => {
    altScoreRecords = [measured('C4H8N2O3')]
    const wrapper = await mountPane()
    const rows = wrapper.findAll('.alt')

    expect(rows[1].find('.s').text()).toContain('fit 72%')
    expect(rows[1].find('.s').text()).toContain(`${num.mzError.format(-0.9)} ppm`)
    // Untouched: the run measured this one, and its own numbers stand.
    expect(rows[0].find('.s').text()).toContain('fit 80%')
    // Not measured, so it still reads as the plausibility it arrived with.
    expect(rows[2].find('.s').text()).toContain('plaus')
  })

  it('names the adduct the measurement found, which the entry never carried', async () => {
    altScoreRecords = [measured('C4H8N2O3')]
    const wrapper = await mountPane()

    expect(wrapper.vm.altTooltip(wrapper.vm.alternatives[1], 1)).toContain('adduct: [M+NH4]+')
    // The rival came with its own ion formula and needs no such line.
    expect(wrapper.vm.altTooltip(wrapper.vm.alternatives[0], 0)).not.toContain('adduct:')
  })

  it('says it is measuring rather than reporting no fit while the call is out', async () => {
    scoringNow = true
    const wrapper = await mountPane()

    expect(wrapper.findAll('.alt')[1].find('.s').text()).toContain('measuring')
    expect(wrapper.vm.altTooltip(wrapper.vm.alternatives[1], 1)).toContain('measuring')
    // The control is still disabled, but for a reason that will pass.
    expect(blockedButtons(wrapper)).toHaveLength(2)
    expect(wrapper.vm.noAdductHint(wrapper.vm.alternatives[1], 1)).toContain('One moment')
  })

  // A measured entry names both halves of an assignment, so the control the
  // card used to disable permanently now works.
  it('turns a measured entry into one that can be committed', async () => {
    altScoreRecords = [measured('C4H8N2O3')]
    const wrapper = await mountPane()

    expect(useButtons(wrapper)).toHaveLength(2)
    expect(blockedButtons(wrapper)).toHaveLength(1)
  })

  // The numbers are session data - measured per request, never written onto the
  // run's rows - so they are declared to the server rather than promoted out of
  // a stored list the server would read them from.
  it('commits a measured entry as a composition search, not as a stored runner-up', async () => {
    altScoreRecords = [measured('C4H8N2O3')]
    const wrapper = await mountPane()

    await useButtons(wrapper)[1].trigger('click')

    expect(curate).toHaveBeenCalledWith('pa-1', {
      action: 'set_assignment',
      assigned_formula: 'C4H8N2O3',
      ionization_mechanism_id: 'im-nh4',
      ion_formula: 'C4H12N3O3+',
      isotope_label: 'M0',
      fit_score: 0.72,
      mz_error_ppm: -0.9,
      abundance_error: 0.05
    })
  })

  // An entry the run scored still goes the other way: its numbers and its
  // adduct are on the stored row, so nothing about that request is the
  // client's word.
  it('still promotes a run-scored rival out of the stored list', async () => {
    altScoreRecords = [measured('C4H8N2O3')]
    const wrapper = await mountPane()

    await useButtons(wrapper)[0].trigger('click')

    expect(curate).toHaveBeenCalledWith('pa-1', {
      action: 'promote_alternative',
      alternative_index: 0,
      expected_formula: 'C7H16O5'
    })
  })

  // Measured and placed on this peak by nothing: the honest answer is the
  // server's own, which can tell this apart from a sample with no adducts at
  // all and from a formula that will not make an ion.
  it('gives the measurement its own reason when no adduct reaches the peak', async () => {
    altScoreRecords = [
      {
        alternative_index: 1,
        assigned_formula: 'C4H8N2O3',
        plausibility: 0.44,
        adducts_tried: 3,
        blocked_reason:
          "None of this sample's 3 adducts put this formula on this peak within the " +
          "matcher's mass tolerance, so there is no measured fit to show and nothing " +
          'to assign it under.'
      }
    ]
    const wrapper = await mountPane()
    const reason = wrapper.vm.noAdductHint(wrapper.vm.alternatives[1], 1)

    expect(reason).toContain('None of this sample')
    expect(reason).toContain('Re-search the peak to look wider than this shortlist')
    expect(blockedButtons(wrapper)).toHaveLength(2)
  })

  // The rendered list is filtered and the server indexes the stored one, so
  // position is not a safe join between them.
  it('matches a measurement to its entry by formula, not by position', async () => {
    altScoreRecords = [measured('C9H8', { alternative_index: 0 })]
    const wrapper = await mountPane()

    expect(wrapper.vm.alternatives[1].scored).toBeNull()
    expect(wrapper.vm.alternatives[2].scored).toMatchObject({ assigned_formula: 'C9H8' })
    expect(wrapper.findAll('.alt')[2].find('.s').text()).toContain('fit 72%')
  })
})

describe('PanePeakAssign batch-level verdict overlay', () => {
  const ANCHOR_VERDICT = {
    batch_peak_verification_id: 'bv1',
    batch_peak_id: 'bp-1',
    sample_peak_id: 'p-1',
    assigned_formula: 'C10H12',
    ionization_mechanism_id: null,
    verdict: 'confirmed',
    evidence_level: 'pattern',
    verified_by: 3,
    verified_utc: '2026-09-04T10:00:00Z'
  }

  afterEach(() => {
    anchorVerdictRecord = null
  })

  it('shows a batch-level verdict as borrowed when the peak has none of its own', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
    anchorVerdictRecord = ANCHOR_VERDICT
    const wrapper = await mountPane()

    const pill = wrapper.find('.anchor-verdict')
    expect(pill.exists()).toBe(true)
    expect(pill.text()).toContain('Confirmed at batch level')
    expect(pill.text()).toContain('records a per-sample exception')
    expect(wrapper.vm.anchorVerdictTooltip).toContain('Batch-level verdict on C10H12, by user #3')
    // The form still offers the exception.
    expect(wrapper.vm.showVerifyForm).toBe(true)
    expect(wrapper.text()).toContain('Confirm')
  })

  it("lets the peak's own verdict win, and hands the disagreement to its badge", async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
    verdictRecord = VERDICT
    anchorVerdictRecord = { ...ANCHOR_VERDICT, verdict: 'rejected' }
    const wrapper = await mountPane()

    expect(wrapper.find('.anchor-verdict').exists()).toBe(false)
    expect(wrapper.find('.verdict-badge').exists()).toBe(true)
    expect(wrapper.vm.anchorConflict).toEqual(anchorVerdictRecord)
  })

  it('names no disagreement when the two agree', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
    verdictRecord = VERDICT
    anchorVerdictRecord = ANCHOR_VERDICT
    const wrapper = await mountPane()

    expect(wrapper.vm.anchorConflict).toBeNull()
  })

  it('shows nothing borrowed when no batch-level verdict reaches the peak', async () => {
    focusedAssignment = assignment({ formula: 'C10H12', tier: 'assigned' })
    const wrapper = await mountPane()

    expect(wrapper.find('.anchor-verdict').exists()).toBe(false)
  })
})

describe('PanePeakAssign batch curation on a derived row', () => {
  // What member_detail serves for a derived row: the anchor's other identity,
  // naming its registry index.
  const DERIVED_ALTERNATIVES = [
    {
      assigned_formula: 'C7H14O7',
      ion_formula: 'C7H15O7+',
      ionization_mechanism_id: 'm1',
      source: 'batch',
      evidence_share: 0.2,
      n_members: 1,
      candidate: 1
    }
  ]

  beforeEach(() => {
    runRecord = { engine: 'batch' }
    focusedAssignment = {
      ...assignment({ formula: 'C6H12O6', tier: 'assigned' }),
      batch_peak_id: 'bp-1'
    }
    detailRecord = {
      ...focusedAssignment,
      alternatives: DERIVED_ALTERNATIVES,
      provenance: { batch_peak: { consensus_formula: 'C6H12O6' } }
    }
    batchCurate.mockReset()
    batchRelease.mockReset()
    batchCurate.mockResolvedValue(null)
    batchRelease.mockResolvedValue(null)
  })

  const settle = async (wrapper) => {
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
  }

  it('offers use this on a derived row and pins the identity on the batch peak', async () => {
    const wrapper = await mountPane()

    const buttons = wrapper.findAll('.alt .alt-use:not(.blocked)')
    expect(buttons).toHaveLength(1)
    expect(wrapper.text()).toContain('pins the identity on the batch peak')

    await buttons[0].trigger('click')
    await settle(wrapper)

    // The guard is the consensus the card shows, and the candidate is the
    // registry index the alternative carries.
    expect(batchCurate).toHaveBeenCalledWith({
      batch_peak_id: 'bp-1',
      candidate: 1,
      expected_formula: 'C6H12O6'
    })
  })

  it('shows the batch curation note and releases it from there', async () => {
    detailRecord.provenance.manual = {
      action: 'promote_identity',
      candidate: 1,
      formula: 'C7H14O7',
      previous: { consensus_formula: 'C6H12O6' }
    }
    const wrapper = await mountPane()

    const note = wrapper.find('.manual-note')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('Curated by hand for the whole batch')
    expect(note.text()).toContain('in place of C6H12O6')

    await note.find('.release-link').trigger('click')
    await settle(wrapper)
    expect(batchRelease).toHaveBeenCalledWith({ batch_peak_id: 'bp-1' })
  })

  it('reads a pin on a run-backed row as nothing of its own', async () => {
    runRecord = { engine: 'mascope' }
    detailRecord.provenance.manual = { action: 'promote_identity', formula: 'C7H14O7' }
    const wrapper = await mountPane()

    expect(wrapper.text()).not.toContain('Curated by hand for the whole batch')
  })
})
