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
  focusWhenPresent: vi.fn()
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
// The metadata section is stubbed the same way: its own spec covers it, and
// here it only has to hand the dialog a form.
const PRIMEVUE = ['Dialog', 'FloatLabel', 'InputText', 'Textarea', 'Button']
const stubs = Object.fromEntries(
  [...PRIMEVUE, 'DatasetMetadataSection'].map((name) => [
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
  dataset_description: 'Original description',
  license: 'CC-BY-4.0',
  authors: [{ name: 'Author One', orcid: null, affiliation: 'Lab A' }],
  keywords: ['aerosol'],
  related_identifiers: null,
  funding: null
}

const section = (wrapper) => wrapper.findComponent({ name: 'DatasetMetadataSection' })

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
    dataset.create.mockReset()
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

  // The new dataset reaches the list through a socket creation event, which
  // resolves no lazy focus, so the dialog has to ask for it to be opened once
  // it is there.
  it('opens the dataset it created', async () => {
    dataset.create.mockResolvedValue({ data: { dataset_id: 'ds-new' } })
    const wrapper = mount(DialogDatasetOp, {
      props: { action: null },
      global: { plugins: [PrimeVue], stubs }
    })
    await wrapper.setProps({ action: 'create' })
    await flushPromises()
    await wrapper
      .findAllComponents({ name: 'InputText' })[0]
      .vm.$emit('update:modelValue', 'Spring run')
    await flushPromises()

    const create = wrapper
      .findAllComponents({ name: 'Button' })
      .find((c) => c.props('label') === 'Create')
    await create.trigger('click')
    await flushPromises()

    expect(dataset.create).toHaveBeenCalledWith(
      expect.objectContaining({ dataset_name: 'Spring run' })
    )
    expect(dataset.focusWhenPresent).toHaveBeenCalledWith({ dataset_id: 'ds-new' })
    expect(closed(wrapper)).toBe(true)
  })

  it('saves the metadata as it stands, clearing what was emptied', async () => {
    dataset.update.mockResolvedValue({ data: DATASET })
    const wrapper = await openEditDialog()

    const form = section(wrapper).props('modelValue')
    expect(form.license).toBe('CC-BY-4.0')
    await section(wrapper).vm.$emit('update:modelValue', {
      ...form,
      license: '',
      keywords: [...form.keywords, 'CI-MS']
    })
    await saveButton(wrapper).trigger('click')
    await flushPromises()

    expect(dataset.update).toHaveBeenCalledWith({
      dataset_id: 'ds-1',
      dataset_name: 'Winter run',
      dataset_description: 'Original description',
      license: null,
      authors: [{ name: 'Author One', orcid: null, affiliation: 'Lab A' }],
      keywords: ['aerosol', 'CI-MS'],
      related_identifiers: null,
      funding: null
    })
  })

  it('leaves the name out when it saves an acquisition dataset', async () => {
    dataset.update.mockResolvedValue({ data: DATASET })
    const wrapper = mount(DialogDatasetOp, {
      props: { action: null, dataset: { ...DATASET, dataset_type: 'ACQUISITION' } },
      global: { plugins: [PrimeVue], stubs }
    })
    await wrapper.setProps({ action: 'edit' })
    await flushPromises()

    await saveButton(wrapper).trigger('click')
    await flushPromises()

    const sent = dataset.update.mock.calls[0][0]
    expect(sent).not.toHaveProperty('dataset_name')
    expect(sent.license).toBe('CC-BY-4.0')
    expect(closed(wrapper)).toBe(true)
  })

  it('holds the save back while an ORCID is malformed', async () => {
    const wrapper = await openEditDialog()
    const form = section(wrapper).props('modelValue')

    await section(wrapper).vm.$emit('update:modelValue', {
      ...form,
      authors: [{ name: 'Author One', orcid: '1234', affiliation: '' }]
    })
    expect(saveButton(wrapper).props('disabled')).toBe(true)
  })

  it('creates a dataset with the metadata filled in', async () => {
    dataset.create.mockResolvedValue({ data: { dataset_id: 'ds-new' } })
    const wrapper = mount(DialogDatasetOp, {
      props: { action: null },
      global: { plugins: [PrimeVue], stubs }
    })
    await wrapper.setProps({ action: 'create' })
    await flushPromises()
    await wrapper
      .findAllComponents({ name: 'InputText' })[0]
      .vm.$emit('update:modelValue', 'Spring run')
    await section(wrapper).vm.$emit('update:modelValue', {
      ...section(wrapper).props('modelValue'),
      license: 'CC0-1.0',
      funding: [{ funder: 'Funder A', award: '' }]
    })

    const create = wrapper
      .findAllComponents({ name: 'Button' })
      .find((c) => c.props('label') === 'Create')
    await create.trigger('click')
    await flushPromises()

    expect(dataset.create).toHaveBeenCalledWith(
      expect.objectContaining({
        dataset_name: 'Spring run',
        license: 'CC0-1.0',
        authors: null,
        funding: [{ funder: 'Funder A', award: null }]
      })
    )
  })
})
