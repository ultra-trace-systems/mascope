import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// The chemistry context a dataset or a batch names for its samples (step 3.6).
// What is pinned: the choices are the contexts the server offers a run config,
// "Not named" writes null, and a stored name the server no longer offers is
// still shown rather than read as none.

const PRESETS = {
  profiles: [],
  contexts: [
    { name: 'ambient-air', label: 'Ambient air', description: 'Outdoor air.' },
    { name: 'chamber', label: 'Chamber', description: 'A smog chamber.' },
    { name: 'none', label: 'None', description: 'No matrix prior.' }
  ]
}

const get = vi.fn(() =>
  Promise.resolve({ data: { data: { params: { peak_assignment_presets: PRESETS } } } })
)
vi.mock('@/api', () => ({ api: { http: { get: (...args) => get(...args) } } }))

const ChemistryContextSelect = (await import('@/lib/dialogs/ChemistryContextSelect.vue')).default

const stubs = {
  Select: {
    name: 'Select',
    props: ['modelValue', 'options', 'inputId', 'disabled'],
    emits: ['update:modelValue'],
    template:
      '<select :id="inputId" :disabled="disabled"><option v-for="o in options" :key="String(o.value)">{{ o.label }}</option></select>'
  },
  FloatLabel: { template: '<div><slot /></div>' }
}

async function mountSelect(props = {}) {
  const wrapper = mount(ChemistryContextSelect, {
    props: { id: 'ctx', ...props },
    global: { stubs, directives: { tooltip: {} } }
  })
  await flushPromises()
  return wrapper
}

const options = (wrapper) => wrapper.findComponent({ name: 'Select' }).props('options')

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('ChemistryContextSelect', () => {
  it("offers 'Not named' and every context the server serves", async () => {
    const wrapper = await mountSelect()
    expect(options(wrapper).map((option) => [option.value, option.label])).toEqual([
      [null, 'Not named'],
      ['ambient-air', 'Ambient air'],
      ['chamber', 'Chamber'],
      ['none', 'No context']
    ])
  })

  it('writes the chosen name, and null for none', async () => {
    const wrapper = await mountSelect({ modelValue: 'chamber' })
    const select = wrapper.findComponent({ name: 'Select' })
    expect(select.props('modelValue')).toBe('chamber')
    await select.vm.$emit('update:modelValue', null)
    expect(wrapper.emitted('update:modelValue').at(-1)).toEqual([null])
  })

  it('still shows a stored name the server no longer offers', async () => {
    const wrapper = await mountSelect({ modelValue: 'retired-context' })
    expect(options(wrapper).at(-1)).toMatchObject({
      value: 'retired-context',
      label: 'retired-context'
    })
  })

  it("says a batch's context comes before its dataset's", async () => {
    expect((await mountSelect({ owner: 'batch' })).text()).toContain("before the dataset's")
    expect((await mountSelect({ owner: 'dataset' })).text()).toContain(
      'unless their batch names its own'
    )
  })
})
