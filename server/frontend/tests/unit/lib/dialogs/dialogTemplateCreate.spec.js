import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// A template made from another keeps the other's labels and the registry
// fields it selects, but for a retired field: the server adds no retired
// field to a new template, and would refuse the whole copy for one.

const mocks = vi.hoisted(() => ({ app: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

import DialogTemplateCreate from '@/lib/dialogs/DialogTemplateCreate.vue'

const STUBS = {
  Dialog: { props: ['visible'], template: '<div v-if="visible"><slot /></div>' },
  FloatLabel: { template: '<div><slot /></div>' },
  InputText: true,
  Button: {
    props: ['label'],
    emits: ['click'],
    template: `<button @click="$emit('click')">{{ label }}</button>`
  }
}

const original = (extra = {}) => ({
  attribute_template_id: 't1',
  name: 'Campaign form',
  type: 'sample_item',
  template: [{ label: 'Stage', required: false, value: '' }],
  fields: ['site', 'old_inlet', 'environment'],
  ...extra
})

const create = async (props) => {
  const wrapper = mount(DialogTemplateCreate, {
    props: { visible: false, ...props },
    global: { stubs: STUBS }
  })
  await wrapper.setProps({ visible: true })
  await wrapper
    .findAll('button')
    .find((b) => b.text() === 'Create')
    .trigger('click')
  await flushPromises()
  return mocks.app.data.template.create.mock.calls[0][0]
}

beforeEach(() => {
  mocks.app = {
    data: {
      template: { create: vi.fn(async () => ({})) },
      metadataField: {
        list: [
          { key: 'site', retired: false },
          { key: 'old_inlet', retired: true },
          { key: 'environment', retired: false }
        ]
      }
    }
  }
})

describe('DialogTemplateCreate', () => {
  it('copies the fields the template selects, in order, but a retired one', async () => {
    const body = await create({ original: original() })
    expect(body.fields).toEqual(['site', 'environment'])
    expect(body.template.map((f) => f.label)).toEqual(['Stage'])
    expect(body.name).toBe('Campaign form (modified)')
  })

  it('copies a template of labels alone with no fields', async () => {
    const { fields, ...labelsOnly } = original()
    expect(fields).toHaveLength(3)
    const body = await create({ original: labelsOnly })
    expect(body.fields).toEqual([])
  })
})
