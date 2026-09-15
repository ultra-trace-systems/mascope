import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

import PrimeVue from 'primevue/config'

// The rename request is the whole subject here: the backend refuses a name the
// workspace already uses with a 409, and the dialog used to fire the request
// without awaiting it - so it closed as if the rename had gone through.
const dataset = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  delete: vi.fn(),
  lazyFocus: vi.fn()
}))

vi.mock('@/stores', () => ({
  useApp: () => ({
    data: {
      dataset: { focused: null, ...dataset }
    }
  })
}))

import DialogDatasetOp from '@/lib/dialogs/DialogDatasetOp.vue'

// Only the dialog's own logic is under test; PrimeVue's pieces are reduced to
// passthroughs that still render their default slot and forward clicks.
const PRIMEVUE = ['Dialog', 'FloatLabel', 'InputText', 'Textarea', 'Button']
const stubs = Object.fromEntries(
  PRIMEVUE.map((name) => [
    name,
    {
      name,
      props: ['modelValue', 'visible', 'label', 'disabled'],
      emits: ['update:modelValue', 'update:visible'],
      template: '<div><slot /></div>'
    }
  ])
)

const DATASET = {
  dataset_id: 'ds-1',
  dataset_name: 'Winter run',
  dataset_description: 'Original description'
}

async function openEditDialog() {
  const wrapper = mount(DialogDatasetOp, {
    props: { action: null, dataset: DATASET },
    global: { plugins: [PrimeVue], stubs }
  })
  // visible (and init()) are driven by the action watcher, so set it after mount.
  await wrapper.setProps({ action: 'edit' })
  await flushPromises()
  return wrapper
}

const saveButton = (wrapper) =>
  wrapper.findAllComponents({ name: 'Button' }).find((c) => c.props('label') === 'Save')

// The dialog closes by writing null to its `action` model.
const closed = (wrapper) =>
  (wrapper.emitted('update:action') ?? []).some(([value]) => value === null)

describe('DialogDatasetOp', () => {
  beforeEach(() => {
    dataset.update.mockReset()
  })

  it('stays open when the rename is refused', async () => {
    dataset.update.mockRejectedValue(new Error('A dataset named X already exists'))
    const wrapper = await openEditDialog()

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(dataset.update).toHaveBeenCalledOnce()
    expect(closed(wrapper), 'dialog closed on a failed rename').toBe(false)
  })

  it('closes once the rename succeeds', async () => {
    dataset.update.mockResolvedValue({ data: DATASET })
    const wrapper = await openEditDialog()

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(dataset.update).toHaveBeenCalledOnce()
    expect(closed(wrapper)).toBe(true)
  })
})
