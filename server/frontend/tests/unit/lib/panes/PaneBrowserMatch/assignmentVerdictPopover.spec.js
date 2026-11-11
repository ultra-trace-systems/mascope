import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const verify = vi.fn(() => Promise.resolve({}))
let app
vi.mock('@/stores', () => ({ useApp: () => app }))
// The structure picker is stubbed below: it shows what the form hands it and
// answers with the claim a test sets here (BaseClaimStructure.spec.js covers it).
const claimStub = vi.hoisted(() => ({ claimed_inchikey: null, claimed_name: null, ready: true }))
vi.mock('@/lib/base', () => ({
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
    props: { record: Object, inherited: Boolean },
    template: '<span class="verdict-badge" :data-inherited="String(!!inherited)" />'
  }
}))
vi.mock('@/lib/permissions', () => ({
  canEditWorkspace: (workspace) => workspace?.my_role !== 'guest'
}))

const { default: AssignmentVerdictPopover } =
  await import('@/lib/panes/PaneBrowserMatch/AssignmentVerdictPopover.vue')

const M0 = {
  peak_assignment_id: 'pa-1',
  sample_peak_id: 'p-1',
  sample_peak_mz: 181.0707,
  assigned_formula: 'C6H12O6',
  role: 'M0'
}
const CHILD = {
  ...M0,
  peak_assignment_id: 'pa-1-c0',
  sample_peak_id: 'p-2',
  sample_peak_mz: 182.0741,
  role: 'iso_child',
  owner_peak_assignment_id: 'pa-1'
}
const BARE = {
  peak_assignment_id: 'pa-9',
  sample_peak_id: 'p-9',
  sample_peak_mz: 250.1,
  assigned_formula: null
}

function makeApp({ role = 'editor' } = {}) {
  return {
    auth: { user: { id: 1 } },
    data: {
      workspace: { focused: { my_role: role } },
      peakAssignment: {
        peak: { m0Of: (row) => (row?.role === 'iso_child' ? M0 : row) },
        verification: { verify }
      }
    }
  }
}

const STUBS = {
  Button: {
    props: ['label', 'disabled', 'loading'],
    emits: ['click'],
    template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>'
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

function mountPopover(props) {
  return mount(AssignmentVerdictPopover, {
    props,
    global: { stubs: STUBS, directives: { tooltip: TOOLTIP } }
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  app = makeApp()
  Object.assign(claimStub, {
    claimed_inchikey: null,
    claimed_name: null,
    ready: true,
    problem: null
  })
})

const GLUCOSE = 'WQZGKKKJIJFFOK-GASJEMHNSA-N'
const LISTED = {
  ...M0,
  reference_listing: { name: 'D-glucose', source: 'sugars', inchikey: GLUCOSE, tags: [], total: 1 }
}

describe('AssignmentVerdictPopover', () => {
  it('records a verdict on the row and closes', async () => {
    const wrapper = mountPopover({ row: M0, record: null, overlay: null })
    await wrapper.find('.note').setValue('looks right')
    await wrapper.find('button:nth-of-type(2)').trigger('click') // Reject

    expect(verify).toHaveBeenCalledWith({
      peak_assignment_id: 'pa-1',
      verdict: 'rejected',
      evidence_level: null,
      note: 'looks right'
    })
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('done')).toHaveLength(1)
  })

  it('judges an isotopologue on its M0 and says so', async () => {
    const wrapper = mountPopover({ row: CHILD, record: null, overlay: null })
    expect(wrapper.text()).toContain('judged on its M0')
    expect(wrapper.find('.formula').text()).toBe('C6H12O6')

    await wrapper.find('button:nth-of-type(3)').trigger('click') // Unsure
    expect(verify).toHaveBeenCalledWith(expect.objectContaining({ peak_assignment_id: 'pa-1' }))
  })

  it('will not confirm without an evidence level', async () => {
    const wrapper = mountPopover({ row: M0, record: null, overlay: null })
    const confirm = wrapper.find('button:nth-of-type(1)')
    expect(confirm.attributes('disabled')).toBeDefined()
    await wrapper.find('.evidence').setValue('msms')
    expect(wrapper.find('button:nth-of-type(1)').attributes('disabled')).toBeUndefined()
    await wrapper.find('button:nth-of-type(1)').trigger('click')
    expect(verify).toHaveBeenCalledWith(
      expect.objectContaining({ verdict: 'confirmed', evidence_level: 'msms' })
    )
  })

  it('offers the structure the row is listed as, and records the claim with the confirmation', async () => {
    const wrapper = mountPopover({ row: LISTED, record: null, overlay: null })
    expect(wrapper.find('.claim-structure').exists()).toBe(false)
    Object.assign(claimStub, { claimed_inchikey: GLUCOSE, claimed_name: 'D-glucose' })
    await wrapper.find('.evidence').setValue('reference_standard')

    const picker = wrapper.find('.claim-structure')
    expect(JSON.parse(picker.attributes('data-candidates'))).toEqual([
      { inchikey: GLUCOSE, name: 'D-glucose', label: 'sugars' }
    ])
    expect(picker.attributes('data-evidence')).toBe('reference_standard')
    await wrapper.find('button:nth-of-type(1)').trigger('click')
    expect(verify).toHaveBeenCalledWith({
      peak_assignment_id: 'pa-1',
      verdict: 'confirmed',
      evidence_level: 'reference_standard',
      note: null,
      claimed_inchikey: GLUCOSE,
      claimed_name: 'D-glucose'
    })
  })

  it('will not confirm while the structure the evidence needs is missing', async () => {
    claimStub.ready = false
    const wrapper = mountPopover({ row: LISTED, record: null, overlay: null })
    await wrapper.find('.evidence').setValue('msms')
    expect(wrapper.find('button:nth-of-type(1)').attributes('disabled')).toBeDefined()
    await wrapper.find('button:nth-of-type(1)').trigger('click')
    expect(verify).not.toHaveBeenCalled()
  })

  it('starts a changed confirmation from the structure it names', async () => {
    const record = {
      verdict: 'confirmed',
      evidence_level: 'msms',
      claimed_inchikey: GLUCOSE,
      claimed_name: 'D-glucose'
    }
    const wrapper = mountPopover({ row: LISTED, record, overlay: null })
    expect(wrapper.find('.claim-structure').attributes('data-initial')).toBe(GLUCOSE)
  })

  it('says why Confirm waits, in the words the structure picker gives', async () => {
    Object.assign(claimStub, { ready: false, problem: 'Not an InChIKey' })
    const wrapper = mountPopover({ row: LISTED, record: null, overlay: null })
    await wrapper.find('.evidence').setValue('pattern')
    expect(wrapper.find('button:nth-of-type(1)').attributes('data-tooltip')).toBe('Not an InChIKey')
  })

  it('sends no structure with a rejection', async () => {
    Object.assign(claimStub, { claimed_inchikey: GLUCOSE, claimed_name: 'D-glucose' })
    const wrapper = mountPopover({ row: LISTED, record: null, overlay: null })
    await wrapper.find('.evidence').setValue('msms')
    await wrapper.find('button:nth-of-type(2)').trigger('click') // Reject
    expect(verify.mock.calls[0][0]).not.toHaveProperty('claimed_inchikey')
  })

  it('shows the batch-level verdict reaching the row as inherited, with the form still open', () => {
    const wrapper = mountPopover({ row: M0, record: null, overlay: { verdict: 'confirmed' } })
    expect(wrapper.find('.verdict-badge').attributes('data-inherited')).toBe('true')
    expect(wrapper.text()).toContain('batch-level, until this sample has one of its own')
    expect(wrapper.findAll('button')).toHaveLength(3)
  })

  it('has nothing to judge on a formula-less row', () => {
    const wrapper = mountPopover({ row: BARE, record: null, overlay: null })
    expect(wrapper.text()).toContain('Nothing to judge')
    expect(wrapper.findAll('button')).toHaveLength(0)
  })

  it('withholds the form from a guest, and after a 403', async () => {
    app = makeApp({ role: 'guest' })
    let wrapper = mountPopover({ row: M0, record: null, overlay: null })
    expect(wrapper.text()).toContain('needs the editor role')

    app = makeApp()
    verify.mockRejectedValueOnce({ response: { status: 403 } })
    wrapper = mountPopover({ row: M0, record: null, overlay: null })
    await wrapper.find('button:nth-of-type(2)').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('needs the editor role')
    expect(wrapper.emitted('done')).toBeUndefined()
  })
})
