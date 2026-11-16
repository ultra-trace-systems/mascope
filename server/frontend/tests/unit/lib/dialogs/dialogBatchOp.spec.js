import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import PrimeVue from 'primevue/config'

// The batch dialog's chemistry context (step 3.6): a batch names the context
// its samples were drawn from, which a peak assignment run on Auto takes
// before its dataset's. Pinned: the dialog opens on the stored one, a change
// to it alone is a change worth saving, and the save sends it.

const batch = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }))

vi.mock('@/api', () => ({
  api: { http: { get: vi.fn(() => Promise.resolve({ target_collections: [] })) } }
}))
vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      batch: { focused: null, ...batch },
      dataset: { focusedId: 'ds-1' },
      target: { collection: { list: [] } }
    },
    ui: { help: { set: () => {} } }
  })
}))
vi.mock('@/lib/dialogs/ChemistryContextSelect.vue', () => ({
  default: {
    name: 'ChemistryContextSelect',
    props: ['modelValue', 'id', 'owner', 'disabled'],
    emits: ['update:modelValue'],
    template: '<div />'
  }
}))
vi.mock('@/lib/dialogs/DialogBatchOp/PaneSelectTargets.vue', () => ({
  default: { name: 'PaneSelectTargets', template: '<div />' }
}))

const DialogBatchOp = (await import('@/lib/dialogs/DialogBatchOp/DialogBatchOp.vue')).default

const PASSTHROUGH = [
  'Dialog',
  'FloatLabel',
  'InputText',
  'TextArea',
  'Textarea',
  'Button',
  'Panel',
  'Tabs',
  'TabList',
  'Tab',
  'TabPanels',
  'TabPanel'
]
const stubs = Object.fromEntries(
  PASSTHROUGH.map((name) => [
    name,
    {
      name,
      props: ['modelValue', 'visible', 'label', 'disabled', 'value'],
      emits: ['update:modelValue', 'update:visible', 'click'],
      template: '<div @click="$emit(\'click\')"><slot /></div>'
    }
  ])
)

const BATCH = {
  sample_batch_id: 'sb-1',
  sample_batch_name: 'Chamber run',
  sample_batch_description: '',
  sample_batch_type: 'ANALYSIS',
  polarity: '+-',
  chemistry_context: 'chamber',
  sample_batch_utc_created: '2026-11-16T00:00:00Z'
}

async function open(action, props = {}) {
  const wrapper = mount(DialogBatchOp, {
    // Bound as the app binds it, with v-model: the dialog clears its action
    // before it reads it, and only a bound model keeps the value until the
    // parent's update lands.
    props: { action: null, 'onUpdate:action': () => {}, ...props },
    global: { plugins: [PrimeVue], stubs }
  })
  await wrapper.setProps({ action })
  await flushPromises()
  return wrapper
}

const contextSelect = (wrapper) => wrapper.findComponent({ name: 'ChemistryContextSelect' })
const saveButton = (wrapper) =>
  wrapper.findAllComponents({ name: 'Button' }).find((c) => c.props('label') === 'Save')

describe('DialogBatchOp chemistry context', () => {
  beforeEach(() => {
    batch.create.mockReset()
    batch.update.mockReset().mockResolvedValue({})
  })

  it('opens on the context the batch names, and saves a change to it alone', async () => {
    const wrapper = await open('update', { batch: BATCH })

    expect(contextSelect(wrapper).props('modelValue')).toBe('chamber')
    expect(saveButton(wrapper).props('disabled')).toBe(true)

    await contextSelect(wrapper).vm.$emit('update:modelValue', 'ambient-air')
    expect(saveButton(wrapper).props('disabled')).toBe(false)
    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(batch.update).toHaveBeenCalledWith(
      expect.objectContaining({ sample_batch_id: 'sb-1', chemistry_context: 'ambient-air' })
    )
  })

  it('creates a batch with the context it names, none by default', async () => {
    const wrapper = await open('create')
    expect(contextSelect(wrapper).props('modelValue')).toBe(null)
    await wrapper
      .findAllComponents({ name: 'InputText' })[0]
      .vm.$emit('update:modelValue', 'New batch')
    await contextSelect(wrapper).vm.$emit('update:modelValue', 'chamber')
    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(batch.create).toHaveBeenCalledWith(
      expect.objectContaining({ sample_batch_name: 'New batch', chemistry_context: 'chamber' })
    )
  })
})
