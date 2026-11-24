import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import PrimeVue from 'primevue/config'

// The metadata field registry pane: the fields, the retired ones behind a
// switch, and for the global editor role or higher the writes - add, edit,
// retire and restore, delete in two clicks. What the server refuses is
// disabled (the type of a field in use, deleting the shipped field or one in
// use) and when it refuses anyway its reason is shown.

const store = vi.hoisted(() => ({ value: null }))
const auth = vi.hoisted(() => ({ value: { user: { role_id: 200 } } }))
vi.mock('@/stores/data/modules/metadataField', () => ({ useMetadataField: () => store.value }))
vi.mock('@/stores/auth', () => ({ useAuth: () => auth.value }))

import PaneMetadataFields from '@/lib/panes/PaneMetadataFields.vue'

const field = (extra = {}) => ({
  metadata_field_id: `id-${extra.key ?? 'site'}`,
  key: 'site',
  label: 'Site',
  type: 'text',
  unit: null,
  allowed_values: null,
  description: null,
  system: false,
  in_use: false,
  retired: false,
  ...extra
})

const shipped = () =>
  field({
    key: 'environment',
    label: 'Environment',
    type: 'tags',
    allowed_values: [],
    system: true
  })

const makeStore = (list) => {
  const fake = reactive({ list, pending: false, error: null })
  fake.load = vi.fn()
  fake.create = vi.fn(async () => ({}))
  fake.update = vi.fn(async () => null)
  fake.delete = vi.fn(async () => null)
  return fake
}

const STUBS = {
  BaseEmptyState: { props: ['title'], template: '<p class="empty">{{ title }}</p>' },
  BaseLoadError: { props: ['fallback'], template: '<p class="load-error">{{ fallback }}</p>' }
}

const mountPane = async () => {
  const wrapper = mount(PaneMetadataFields, {
    global: { plugins: [PrimeVue], stubs: STUBS, directives: { tooltip: {} } }
  })
  await flushPromises()
  return wrapper
}

const rows = (wrapper) => wrapper.findAll('tbody tr')
const labels = (wrapper) => rows(wrapper).map((row) => row.find('.label').text())
const button = (wrapper, label) => wrapper.find(`button[aria-label="${label}"]`)
const buttonByText = (wrapper, text) =>
  wrapper.findAll('button').find((b) => b.text().trim() === text)

beforeEach(() => {
  auth.value = { user: { role_id: 200 } }
  store.value = makeStore([
    shipped(),
    field(),
    field({ key: 'inlet', label: 'Inlet', type: 'enum', allowed_values: ['a', 'b'], in_use: true }),
    field({ key: 'old', label: 'Old campaign', retired: true })
  ])
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('PaneMetadataFields', () => {
  it('lists the active fields, with their key, type, values and marks', async () => {
    const wrapper = await mountPane()
    expect(labels(wrapper)).toEqual(['Environment', 'Site', 'Inlet'])
    const [environment, , inlet] = rows(wrapper)
    expect(environment.text()).toContain('environment')
    expect(environment.text()).toContain('Tags')
    expect(environment.text()).toContain('Any')
    expect(environment.text()).toContain('Mascope')
    expect(inlet.text()).toContain('One of a list')
    expect(inlet.text()).toContain('a, b')
    expect(inlet.text()).toContain('In use')
  })

  it('shows the retired fields when asked', async () => {
    const wrapper = await mountPane()
    expect(wrapper.text()).toContain('Show retired (1)')
    await wrapper.find('#metadata-fields-show-retired').setValue(true)
    expect(labels(wrapper)).toContain('Old campaign')
    expect(rows(wrapper).at(-1).text()).toContain('Retired')
  })

  it('offers no write to a guest', async () => {
    auth.value = { user: { role_id: 100 } }
    const wrapper = await mountPane()
    expect(labels(wrapper)).toEqual(['Environment', 'Site', 'Inlet'])
    expect(buttonByText(wrapper, 'Add field')).toBeUndefined()
    expect(button(wrapper, 'Edit Site').exists()).toBe(false)
    expect(button(wrapper, 'Delete Site').exists()).toBe(false)
  })

  it('keeps the shipped field and a field in use from deletion', async () => {
    const wrapper = await mountPane()
    expect(button(wrapper, 'Delete Environment').attributes('disabled')).toBeDefined()
    expect(button(wrapper, 'Delete Inlet').attributes('disabled')).toBeDefined()
    expect(button(wrapper, 'Delete Site').attributes('disabled')).toBeUndefined()
    // The shipped field is not retired either.
    expect(button(wrapper, 'Retire Environment').exists()).toBe(false)
  })

  it('deletes on the second click', async () => {
    const wrapper = await mountPane()
    await button(wrapper, 'Delete Site').trigger('click')
    expect(store.value.delete).not.toHaveBeenCalled()
    await button(wrapper, 'Confirm delete Site').trigger('click')
    await flushPromises()
    expect(store.value.delete).toHaveBeenCalledWith('id-site')
    expect(store.value.load).toHaveBeenCalled()
  })

  it('retires and restores a field', async () => {
    const wrapper = await mountPane()
    await button(wrapper, 'Retire Inlet').trigger('click')
    await flushPromises()
    expect(store.value.update).toHaveBeenCalledWith('id-inlet', { retired: true })

    await wrapper.find('#metadata-fields-show-retired').setValue(true)
    await button(wrapper, 'Restore Old campaign').trigger('click')
    await flushPromises()
    expect(store.value.update).toHaveBeenCalledWith('id-old', { retired: false })
  })

  it('adds a field, its key suggested from the label', async () => {
    const wrapper = await mountPane()
    await buttonByText(wrapper, 'Add field').trigger('click')
    await wrapper.find('#metadata-field-label').setValue('Inlet height')
    expect(wrapper.find('#metadata-field-key').element.value).toBe('inlet_height')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(store.value.create).toHaveBeenCalledWith({
      key: 'inlet_height',
      label: 'Inlet height',
      type: 'text',
      unit: null,
      description: null,
      allowed_values: null
    })
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('keeps a key typed by hand', async () => {
    const wrapper = await mountPane()
    await buttonByText(wrapper, 'Add field').trigger('click')
    await wrapper.find('#metadata-field-key').setValue('height')
    await wrapper.find('#metadata-field-label').setValue('Inlet height')
    expect(wrapper.find('#metadata-field-key').element.value).toBe('height')
  })

  it('holds back a form the server would refuse', async () => {
    const wrapper = await mountPane()
    await buttonByText(wrapper, 'Add field').trigger('click')
    await wrapper.find('#metadata-field-key').setValue('Bad key')
    await wrapper.find('#metadata-field-label').setValue('Site')
    expect(wrapper.find('form').text()).toContain('lower case letters')
    await wrapper.find('form').trigger('submit')
    expect(store.value.create).not.toHaveBeenCalled()
  })

  it('sends only the change, and fixes the key and the type of a field in use', async () => {
    const wrapper = await mountPane()
    await button(wrapper, 'Edit Inlet').trigger('click')
    expect(wrapper.find('#metadata-field-key').attributes('disabled')).toBeDefined()
    expect(wrapper.find('form').text()).toContain('In use, so its type is fixed.')
    await wrapper.find('#metadata-field-label').setValue('Inlet type')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(store.value.update).toHaveBeenCalledWith('id-inlet', { label: 'Inlet type' })
  })

  it('fixes the values of the shipped field', async () => {
    const wrapper = await mountPane()
    await button(wrapper, 'Edit Environment').trigger('click')
    expect(wrapper.find('#metadata-field-values').attributes('disabled')).toBeDefined()
    expect(wrapper.find('form').text()).toContain('Mascope ships this field.')
  })

  it("shows the server's reason when it refuses", async () => {
    store.value.create = vi.fn(async () => {
      throw {
        response: { data: { error: "A metadata field with the key 'site' already exists." } }
      }
    })
    const wrapper = await mountPane()
    await buttonByText(wrapper, 'Add field').trigger('click')
    await wrapper.find('#metadata-field-label').setValue('Site')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('form').text()).toContain('already exists')
    expect(wrapper.find('form').exists()).toBe(true)
  })

  it('shows a failed delete above the table', async () => {
    store.value.delete = vi.fn(async () => {
      throw { response: { data: { error: "'site' is in use. Retire it instead." } } }
    })
    const wrapper = await mountPane()
    await button(wrapper, 'Delete Site').trigger('click')
    await button(wrapper, 'Confirm delete Site').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Retire it instead.')
  })
})
