import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'

import ToolbarDrawMode from '@/lib/toolbars/ToolbarDrawMode.vue'

/**
 * The toolbar's only logic is the label -> plotly mode string mapping, which is
 * exactly what a typo would break and what both batch charts feed straight into
 * a trace's `mode`.
 */
const mountToolbar = (modelValue = 'markers') =>
  mount(ToolbarDrawMode, {
    props: { modelValue },
    global: { plugins: [PrimeVue] }
  })

describe('ToolbarDrawMode', () => {
  it('offers Markers / Lines / Both, in that order', () => {
    const labels = mountToolbar()
      .findAll('.p-togglebutton-label')
      .map((node) => node.text())
    expect(labels).toEqual(['Markers', 'Lines', 'Both'])
  })

  it('emits the plotly mode string behind each clicked label', async () => {
    const toolbar = mountToolbar('markers')
    const options = () => toolbar.findAll('.p-togglebutton')

    // Each click is followed by the prop write a real v-model parent makes, so
    // the next option is genuinely inactive when it is clicked -- otherwise
    // only the first click would land and the rest would be re-clicks.
    await options()[1].trigger('click') // Lines
    await toolbar.setProps({ modelValue: 'lines' })
    await options()[2].trigger('click') // Both
    await toolbar.setProps({ modelValue: 'lines+markers' })
    await options()[0].trigger('click') // Markers

    expect(toolbar.emitted('update:modelValue')).toEqual([
      ['lines'],
      ['lines+markers'],
      ['markers']
    ])
  })

  it('does not clear the selection when the active option is re-clicked', async () => {
    const toolbar = mountToolbar('lines')
    // 'lines' is the active option; SelectButton is mounted with allowEmpty
    // false, so re-clicking it must not emit an empty value.
    await toolbar.findAll('.p-togglebutton')[1].trigger('click')
    expect(toolbar.emitted('update:modelValue')).toBeUndefined()
  })
})
