import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'

let app
vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('@/lib/base', () => ({
  BaseVerdictBadge: {
    props: ['record', 'compact'],
    template: '<span class="verdict-badge">{{ record?.verdict }}</span>'
  }
}))
vi.mock('@/lib/permissions', () => ({
  canEditWorkspace: (workspace) => workspace?.my_role === 'editor'
}))

const { default: BatchPeakVerdictPopover } =
  await import('@/lib/panes/PaneBrowserMatch/BatchPeakVerdictPopover.vue')

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

function mountPopover(props = {}, options = {}) {
  app = makeApp(options)
  return mount(BatchPeakVerdictPopover, {
    props: { row: ROW, ...props },
    global: { stubs: STUBS, directives: { tooltip: {} } }
  })
}
const button = (wrapper, label) => wrapper.findAll('button.btn').find((b) => b.text() === label)

beforeEach(() => {
  verify.mockReset()
  retract.mockReset()
  ledgerLoad.mockReset()
  verify.mockResolvedValue(RECORD)
  retract.mockResolvedValue({})
})

describe('BatchPeakVerdictPopover', () => {
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
