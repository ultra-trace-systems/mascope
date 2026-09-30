import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

import PaneIonizationMode from '@/lib/panes/PaneIonizationMode.vue'

// A FloatLabel rests its label inside the field until something is chosen, and
// a Select draws its placeholder in that same spot - so a Select that has both
// reads as two texts on top of each other. MultiSelect is not affected: it
// lifts its label, which is why the mechanisms field keeps its placeholder.
//
// happy-dom computes none of that positioning, so this pins the prop that
// causes it rather than the overlap itself.

vi.mock('@/stores', () => ({
  useApp: () => ({
    auth: { user: { role_id: 100 } },
    data: {
      instrument: { list: [] },
      target: { collection: { list: [] } },
      ionization: {
        mechanism: { list: [] },
        mode: { list: [], create: vi.fn(), update: vi.fn(), delete: vi.fn() }
      }
    }
  })
}))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

const recording = (name) => ({
  props: ['modelValue', 'options', 'placeholder', 'id', 'showClear', 'disabled'],
  template: `<div class="${name}" :data-id="id" :data-placeholder="placeholder"></div>`
})

const stubs = {
  FloatLabel: { template: '<div class="float-label"><slot /></div>' },
  Select: recording('select'),
  MultiSelect: recording('multiselect'),
  InputText: recording('inputtext'),
  Button: true,
  DataTable: true,
  Column: true,
  IconField: true,
  InputIcon: true,
  Fieldset: { template: '<div><slot /></div>' }
}

const pane = () => mount(PaneIonizationMode, { global: { stubs, directives: { tooltip: {} } } })

describe('PaneIonizationMode add form', () => {
  it('gives no Select inside a FloatLabel a placeholder to collide with', () => {
    const wrapped = pane().findAll('.float-label .select')
    expect(wrapped.length).toBeGreaterThan(0)
    expect(
      wrapped.map((select) => [select.attributes('data-id'), select.attributes('data-placeholder')])
    ).toEqual(wrapped.map((select) => [select.attributes('data-id'), undefined]))
  })

  it('still offers the instrument field, clearable back to every instrument', () => {
    const instrument = pane().find('.float-label .select[data-id="add-instrument"]')
    expect(instrument.exists()).toBe(true)
  })
})
