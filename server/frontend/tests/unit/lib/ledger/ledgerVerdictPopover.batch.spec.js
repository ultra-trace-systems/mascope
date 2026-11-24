import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'

let app
vi.mock('@/stores', () => ({ useApp: () => app }))
// The structure picker is stubbed below: it shows what the form hands it and
// answers with the claim a test sets here (BaseClaimStructure.spec.js covers it).
const claimStub = vi.hoisted(() => ({ claimed_inchikey: null, claimed_name: null, ready: true }))
// What the evidence showed is stubbed the same way: by default an MS/MS or
// orthogonal confirmation says it showed fragments or a separation (a 2b), and a
// standard names no independent line (BaseClaimEvidence.spec.js covers it).
const evidenceStub = vi.hoisted(() => ({
  details: { msms: 'fragments', orthogonal: 'separation' },
  line: null,
  ready: true,
  problem: null
}))
const windowStub = vi.hoisted(() => ({
  window_start_utc: '2026-11-01T08:00:00.000Z',
  window_end_utc: '2026-11-08T08:00:00.999Z',
  ready: true,
  problem: null
}))
vi.mock('@/lib/base', () => ({
  BaseClaimEvidence: {
    props: ['modelValue', 'evidence', 'initial'],
    emits: ['update:modelValue'],
    mounted() {
      this.$emit('update:modelValue', {
        evidence_detail: evidenceStub.details[this.evidence] ?? null,
        independent_line: this.evidence === 'reference_standard' ? evidenceStub.line : null,
        ready: evidenceStub.ready,
        problem: evidenceStub.problem
      })
    },
    template: '<div class="claim-evidence" :data-evidence="evidence" />'
  },
  BaseClaimWindow: {
    props: ['modelValue', 'initial', 'range'],
    emits: ['update:modelValue'],
    mounted() {
      this.$emit('update:modelValue', { ...windowStub })
    },
    template: '<div class="claim-window" :data-range="JSON.stringify(range)" />'
  },
  BaseClaimStructure: {
    props: ['modelValue', 'candidates', 'evidence', 'initial'],
    emits: ['update:modelValue'],
    mounted() {
      this.$emit('update:modelValue', { ...claimStub })
    },
    template:
      '<div class="claim-structure" :data-candidates="JSON.stringify(candidates)" :data-evidence="evidence" :data-initial="initial?.claimed_inchikey ?? \'\'" />'
  },
  BaseVerdictBadge: {
    props: ['record', 'compact'],
    template: '<span class="verdict-badge">{{ record?.verdict }}</span>'
  }
}))
vi.mock('@/lib/permissions', () => ({
  canEditWorkspace: (workspace) => workspace?.my_role === 'editor'
}))

// The ledger's one verdict popover, in the batch scope: the batch-level
// verdict on a batch peak's species (ledgerVerdictPopover.sample.spec.js has the
// sample scope).
const { default: LedgerVerdictPopover } = await import('@/lib/ledger/LedgerVerdictPopover.vue')

const ROW = {
  batch_peak_id: 'bp-1',
  consensus_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  mz: 181.0707,
  n_present: 3
}
const RECORD = {
  batch_peak_verification_id: 'v1',
  batch_peak_id: 'bp-1',
  assigned_formula: 'C6H12O6',
  ionization_mechanism_id: 'm1',
  verdict: 'confirmed',
  evidence_level: 'pattern',
  note: 'seen twice'
}

const verify = vi.fn()
const retract = vi.fn()
const ledgerLoad = vi.fn()

function makeApp({ role = 'editor' } = {}) {
  return reactive({
    auth: { user: { id: 7 } },
    data: {
      workspace: { focused: { workspace_id: 'ws-1', my_role: role } },
      batchPeak: { load: ledgerLoad },
      batchPeakVerification: { verify, retract }
    }
  })
}

const STUBS = {
  Button: {
    props: ['label', 'disabled', 'loading', 'icon'],
    template:
      '<button class="btn" :disabled="disabled" :data-loading="String(!!loading)">{{ label }}</button>'
  },
  Select: {
    props: ['modelValue', 'options'],
    emits: ['update:modelValue'],
    template:
      '<select class="evidence" @change="$emit(\'update:modelValue\', $event.target.value)"><option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option></select>'
  },
  InputText: {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<input class="note" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />'
  }
}

// Records the tooltip's text, so a test can read what a disabled button says.
const TOOLTIP = {
  mounted: (el, binding) => (el.dataset.tooltip = binding.value || ''),
  updated: (el, binding) => (el.dataset.tooltip = binding.value || '')
}

function mountPopover(props = {}, options = {}) {
  app = makeApp(options)
  return mount(LedgerVerdictPopover, {
    props: { scope: 'batch', row: ROW, ...props },
    global: { stubs: STUBS, directives: { tooltip: TOOLTIP } }
  })
}
const button = (wrapper, label) => wrapper.findAll('button.btn').find((b) => b.text() === label)

beforeEach(() => {
  verify.mockReset()
  retract.mockReset()
  ledgerLoad.mockReset()
  verify.mockResolvedValue(RECORD)
  retract.mockResolvedValue({})
  Object.assign(evidenceStub, {
    details: { msms: 'fragments', orthogonal: 'separation' },
    line: null,
    ready: true,
    problem: null
  })
  Object.assign(windowStub, { ready: true, problem: null })
  Object.assign(claimStub, {
    claimed_inchikey: null,
    claimed_name: null,
    ready: true,
    problem: null
  })
})

const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'

describe('LedgerVerdictPopover, batch scope', () => {
  it('names the claim and states the scope in words', () => {
    const wrapper = mountPopover()
    expect(wrapper.text()).toContain('C6H12O6')
    expect(wrapper.text()).toContain('3 samples')
    expect(wrapper.text()).toContain('One verdict per species at this batch peak')
    expect(wrapper.text()).toContain('per-sample verdicts always win')
  })

  it('confirms only with an evidence level, naming the formula judged', async () => {
    const wrapper = mountPopover()
    const confirm = button(wrapper, 'Confirm')
    expect(confirm.attributes('disabled')).toBeDefined()
    await wrapper.find('select.evidence').setValue('pattern')
    await flushPromises()
    expect(confirm.attributes('disabled')).toBeUndefined()
    await wrapper.find('input.note').setValue('seen twice')
    await confirm.trigger('click')
    await flushPromises()
    expect(verify).toHaveBeenCalledWith({
      batch_peak_id: 'bp-1',
      verdict: 'confirmed',
      evidence_level: 'pattern',
      note: 'seen twice',
      expected_formula: 'C6H12O6'
    })
    expect(wrapper.emitted('done')).toHaveLength(1)
  })

  it('offers the structure the consensus is listed as, and records the claim', async () => {
    const row = {
      ...ROW,
      reference_listing: { name: 'D-glucose', source: 'sugars', inchikey: GLUCOSE, total: 1 }
    }
    Object.assign(claimStub, { claimed_inchikey: GLUCOSE, claimed_name: 'D-glucose' })
    const wrapper = mountPopover({ row })
    await wrapper.find('select.evidence').setValue('msms')
    await flushPromises()
    expect(JSON.parse(wrapper.find('.claim-structure').attributes('data-candidates'))).toEqual([
      { inchikey: GLUCOSE, name: 'D-glucose', label: 'sugars' }
    ])
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(verify).toHaveBeenCalledWith({
      batch_peak_id: 'bp-1',
      verdict: 'confirmed',
      evidence_level: 'msms',
      note: null,
      expected_formula: 'C6H12O6',
      claimed_inchikey: GLUCOSE,
      claimed_name: 'D-glucose',
      evidence_detail: 'fragments'
    })
  })

  it("offers the structure the consensus's target declares, first", async () => {
    const row = {
      ...ROW,
      reference_listing: {
        name: null,
        source: null,
        inchikey: null,
        tags: [],
        total: 0,
        target: { label: 'target', name: 'glucose', inchikey: GLUCOSE, listed: false }
      }
    }
    const wrapper = mountPopover({ row })
    await wrapper.find('select.evidence').setValue('msms')
    await flushPromises()
    expect(JSON.parse(wrapper.find('.claim-structure').attributes('data-candidates'))).toEqual([
      { inchikey: GLUCOSE, name: 'glucose', label: 'target' }
    ])
  })

  it('will not confirm while the structure the evidence needs is missing', async () => {
    claimStub.ready = false
    const wrapper = mountPopover()
    await wrapper.find('select.evidence').setValue('orthogonal')
    await flushPromises()
    expect(button(wrapper, 'Confirm').attributes('disabled')).toBeDefined()
  })

  it('starts a changed confirmation from the structure it names, unless it is stale', async () => {
    const record = { ...RECORD, claimed_inchikey: GLUCOSE, claimed_name: 'D-glucose' }
    const live = mountPopover({ record })
    await flushPromises()
    expect(live.find('.claim-structure').attributes('data-initial')).toBe(GLUCOSE)
    const stale = mountPopover({ record, stale: true })
    await flushPromises()
    expect(stale.find('.claim-structure').attributes('data-initial')).toBe('')
  })

  it('says why Confirm waits, in the words the structure picker gives', async () => {
    Object.assign(claimStub, { ready: false, problem: 'Not an InChIKey' })
    const wrapper = mountPopover()
    await wrapper.find('select.evidence').setValue('visual')
    await flushPromises()
    expect(button(wrapper, 'Confirm').attributes('data-tooltip')).toBe('Not an InChIKey')
  })

  it('scopes a level-1 claim to a window, starting from the batch acquisitions', async () => {
    evidenceStub.line = 'msms'
    Object.assign(claimStub, { claimed_inchikey: GLUCOSE, claimed_name: 'D-glucose' })
    const wrapper = mountPopover()
    app.data.sample = {
      list: [{ datetime_utc: '2026-11-02T08:00:00Z' }, { datetime_utc: '2026-11-01T08:00:00Z' }]
    }
    await wrapper.find('select.evidence').setValue('reference_standard')
    await flushPromises()
    expect(JSON.parse(wrapper.find('.claim-window').attributes('data-range'))).toEqual({
      first: '2026-11-01T08:00:00Z',
      last: '2026-11-02T08:00:00Z'
    })
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(verify).toHaveBeenCalledWith({
      batch_peak_id: 'bp-1',
      verdict: 'confirmed',
      evidence_level: 'reference_standard',
      note: null,
      expected_formula: 'C6H12O6',
      claimed_inchikey: GLUCOSE,
      claimed_name: 'D-glucose',
      independent_line: 'msms',
      window_start_utc: '2026-11-01T08:00:00.000Z',
      window_end_utc: '2026-11-08T08:00:00.999Z'
    })
  })

  it('will not confirm a level-1 claim while its window is not given', async () => {
    evidenceStub.line = 'separation'
    Object.assign(windowStub, { ready: false, problem: 'Give the first and last' })
    const wrapper = mountPopover()
    await wrapper.find('select.evidence').setValue('reference_standard')
    await flushPromises()
    expect(button(wrapper, 'Confirm').attributes('disabled')).toBeDefined()
    expect(button(wrapper, 'Confirm').attributes('data-tooltip')).toBe('Give the first and last')
  })

  it('names no window below level 1', async () => {
    Object.assign(claimStub, { claimed_inchikey: GLUCOSE, claimed_name: 'D-glucose' })
    const wrapper = mountPopover()
    await wrapper.find('select.evidence').setValue('reference_standard')
    await flushPromises()
    expect(wrapper.find('.claim-window').exists()).toBe(false)
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(verify.mock.calls[0][0]).not.toHaveProperty('window_start_utc')
    expect(verify.mock.calls[0][0]).not.toHaveProperty('independent_line')
  })

  it('records a class claim by its name alone, with what the evidence showed', async () => {
    evidenceStub.details.msms = 'loss'
    Object.assign(claimStub, { claimed_inchikey: null, claimed_name: 'a carboxylic acid' })
    const wrapper = mountPopover()
    await wrapper.find('select.evidence').setValue('msms')
    await flushPromises()
    await button(wrapper, 'Confirm').trigger('click')
    await flushPromises()
    expect(verify).toHaveBeenCalledWith({
      batch_peak_id: 'bp-1',
      verdict: 'confirmed',
      evidence_level: 'msms',
      note: null,
      expected_formula: 'C6H12O6',
      claimed_name: 'a carboxylic acid',
      evidence_detail: 'loss'
    })
  })

  it('will not confirm until the evidence says what it showed, and asks no structure yet', async () => {
    Object.assign(evidenceStub, {
      details: {},
      ready: false,
      problem: 'Say what the MS/MS showed'
    })
    const wrapper = mountPopover()
    await wrapper.find('select.evidence').setValue('msms')
    await flushPromises()
    expect(wrapper.find('.claim-structure').exists()).toBe(false)
    expect(button(wrapper, 'Confirm').attributes('disabled')).toBeDefined()
    expect(button(wrapper, 'Confirm').attributes('data-tooltip')).toBe('Say what the MS/MS showed')
  })

  it('says a rejection on a standard refutes the live confirmation, and sends its basis', async () => {
    const wrapper = mountPopover({ record: { ...RECORD, verdict: 'confirmed' } })
    expect(wrapper.text()).not.toContain('refutes the confirmation')
    await wrapper.find('select.evidence').setValue('reference_standard')
    await flushPromises()
    expect(wrapper.text()).toContain('Rejecting on a reference standard refutes the confirmation')
    await button(wrapper, 'Reject').trigger('click')
    await flushPromises()
    const sent = verify.mock.calls[0][0]
    expect(sent).toMatchObject({ verdict: 'rejected', evidence_level: 'reference_standard' })
    // What the evidence showed and the line are a confirmation's alone.
    expect(sent).not.toHaveProperty('independent_line')
    expect(sent).not.toHaveProperty('evidence_detail')
  })

  it.each([
    ['a live confirmation', { verdict: 'confirmed' }, false, true],
    ['a stale confirmation', { verdict: 'confirmed' }, true, false],
    ['an unsure verdict', { verdict: 'unsure' }, false, false],
    ['a rejection', { verdict: 'rejected' }, false, false]
  ])(
    'promises a refutation on %s only where the server records one',
    async (_, verdict, stale, hint) => {
      const wrapper = mountPopover({
        record: { ...RECORD, ...verdict },
        stale,
        row: stale ? { ...ROW, consensus_formula: 'C7H14O7' } : ROW
      })
      await wrapper.find('select.evidence').setValue('reference_standard')
      await flushPromises()
      expect(wrapper.text().includes('refutes the confirmation')).toBe(hint)
      expect(button(wrapper, 'Reject').attributes('data-tooltip')).toBe(
        hint ? 'A standard that disagrees: refutes the confirmation' : ''
      )
    }
  )

  it('rejects without evidence, still naming the formula', async () => {
    const wrapper = mountPopover()
    await button(wrapper, 'Reject').trigger('click')
    await flushPromises()
    expect(verify).toHaveBeenCalledWith(
      expect.objectContaining({
        verdict: 'rejected',
        evidence_level: null,
        expected_formula: 'C6H12O6'
      })
    )
  })

  it('shows the live verdict and offers to retract it', async () => {
    const wrapper = mountPopover({ record: RECORD })
    expect(wrapper.find('.verdict-badge').text()).toBe('confirmed')
    await button(wrapper, 'Retract').trigger('click')
    await flushPromises()
    expect(retract).toHaveBeenCalledWith({ batch_peak_id: 'bp-1' })
    expect(wrapper.emitted('done')).toHaveLength(1)
  })

  it('offers no retract where nothing is recorded', () => {
    const wrapper = mountPopover()
    expect(button(wrapper, 'Retract')).toBeUndefined()
  })

  it('says what a stale verdict was about and what to do', () => {
    const wrapper = mountPopover({
      record: RECORD,
      stale: true,
      row: { ...ROW, consensus_formula: 'C7H14O7' }
    })
    expect(wrapper.find('.current').classes()).toContain('stale')
    expect(wrapper.text()).toContain(
      'Confirmed as C6H12O6 - the consensus is now C7H14O7. Re-judge or retract.'
    )
    // Re-judging names the formula the row claims now, not the one judged before.
    expect(button(wrapper, 'Unsure')).toBeDefined()
  })

  it('has nothing to judge on an unassigned batch peak', () => {
    const wrapper = mountPopover({ row: { ...ROW, consensus_formula: null } })
    expect(wrapper.text()).toContain('Nothing to judge yet')
    expect(button(wrapper, 'Confirm')).toBeUndefined()
  })

  it('withholds the form from a non-editor', () => {
    const wrapper = mountPopover({}, { role: 'guest' })
    expect(button(wrapper, 'Confirm')).toBeUndefined()
    expect(wrapper.text()).toContain('editor role')
  })

  it('reloads the ledger when the consensus moved under the user', async () => {
    verify.mockRejectedValue({ response: { status: 409 } })
    const wrapper = mountPopover()
    await button(wrapper, 'Unsure').trigger('click')
    await flushPromises()
    expect(ledgerLoad).toHaveBeenCalled()
    expect(wrapper.text()).toContain('The consensus changed since this row was read')
    expect(wrapper.emitted('done')).toBeUndefined()
  })

  it('reports a refused write as a role problem', async () => {
    verify.mockRejectedValue({ response: { status: 403 } })
    const wrapper = mountPopover()
    await button(wrapper, 'Unsure').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('editor role')
    expect(button(wrapper, 'Confirm')).toBeUndefined()
    expect(wrapper.emitted('done')).toBeUndefined()
  })
})
