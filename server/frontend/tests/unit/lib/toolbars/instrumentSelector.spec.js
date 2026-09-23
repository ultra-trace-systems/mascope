import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

// The selector used to offer only the instruments themselves, and the store
// held a single selection it refused to leave empty - so once an instrument
// was picked there was no way back to seeing all of them. "All instruments"
// is that empty state, offered as an option and mapped back to unfocus().

const mocks = vi.hoisted(() => ({ app: null }))

vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

import InstrumentSelector from '@/lib/toolbars/InstrumentSelector.vue'

const Select = {
  name: 'Select',
  props: { modelValue: null, options: null, optionLabel: null, inputId: null },
  emits: ['update:modelValue'],
  template: '<div><slot name="value" :value="modelValue" /></div>'
}

const mountSelector = () =>
  mount(InstrumentSelector, {
    global: {
      stubs: { Select },
      directives: { tooltip: {} }
    }
  })

const select = (wrapper) => wrapper.findComponent({ name: 'Select' })

describe('InstrumentSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.app = reactive({
      data: {
        instrument: {
          list: [{ instrument: 'Orbi-1' }, { instrument: 'Tof-2' }],
          focused: { instrument: 'Orbi-1' },
          focus: vi.fn(),
          unfocus: vi.fn()
        }
      },
      ui: { help: { bottom_end: () => ({}) } }
    })
  })

  it('offers every instrument, and all of them together at the top', () => {
    const options = select(mountSelector()).props('options')

    expect(options).toHaveLength(3)
    expect(options[0].instrument).toBe(null)
    expect(options.slice(1).map((o) => o.instrument)).toEqual(['Orbi-1', 'Tof-2'])
  })

  it('unfocuses the store when all of them are chosen', async () => {
    const wrapper = mountSelector()

    await select(wrapper).vm.$emit('update:modelValue', { instrument: null, all: true })

    expect(mocks.app.data.instrument.unfocus).toHaveBeenCalled()
    expect(mocks.app.data.instrument.focus).not.toHaveBeenCalled()
  })

  it('focuses the store on a real instrument', async () => {
    const wrapper = mountSelector()

    await select(wrapper).vm.$emit('update:modelValue', { instrument: 'Tof-2' })

    expect(mocks.app.data.instrument.focus).toHaveBeenCalledWith({ instrument: 'Tof-2' })
    expect(mocks.app.data.instrument.unfocus).not.toHaveBeenCalled()
  })

  // The store has no record for "all", so the Select needs something to hold
  // and to render, or an unfocused store would show a blank box.
  it('shows the focused instrument, and says so when there is none', async () => {
    const wrapper = mountSelector()
    expect(wrapper.text()).toContain('Orbi-1')

    mocks.app.data.instrument.focused = null
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('All instruments')
    expect(select(wrapper).props('modelValue').instrument).toBe(null)
  })
})
