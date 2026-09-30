import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

// The chip for the Batch peaks ledger's selection - the species the Assignments
// batch chart plots. It names what is selected, takes the selection away when
// removed, and stays out of the way outside Assignments mode, where the
// selection drives nothing on screen.

let app
vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))
// A stand-in for PrimeVue's Chip: the spec is about what the filter passes it.
vi.mock('primevue/chip', () => ({
  default: {
    props: ['label', 'icon', 'removable'],
    emits: ['remove'],
    template: `<div class="chip">{{ label }}<button class="remove" @click="$emit('remove')" /></div>`
  }
}))

const FilterBatchPeak = (await import('@/lib/toolbars/ToolbarAppFilters/FilterBatchPeak.vue'))
  .default

function peak(batch_peak_id, consensus_formula = null, mz = 100) {
  return { batch_peak_id, consensus_formula, mz }
}

function makeApp(selected, mode = 'assignments') {
  const state = reactive({
    ui: { matchMode: { mode } },
    data: {
      batchPeak: {
        selected,
        unfocus: vi.fn(() => {
          state.data.batchPeak.selected = []
        })
      }
    }
  })
  return state
}

// The tooltip's text, kept on the element so a test can read what it would say.
const tooltip = {
  mounted: (el, { value }) => (el.dataset.tooltip = value),
  updated: (el, { value }) => (el.dataset.tooltip = value)
}

function mountChip() {
  const registered = []
  const wrapper = mount(FilterBatchPeak, {
    global: {
      provide: { 'register-filter': (filter) => registered.push(filter) },
      directives: { tooltip }
    }
  })
  return { wrapper, filter: registered[0] }
}

describe('FilterBatchPeak', () => {
  it('names a single selected peak by its formula', () => {
    app = makeApp([peak('bp-1', 'C6H6O', 94.0419)])
    const { wrapper, filter } = mountChip()
    expect(filter.active.value).toBe(true)
    expect(wrapper.find('.chip').text()).toBe('C6H6O')
    expect(wrapper.find('.chip').attributes('data-tooltip')).toContain('C6H6O')
  })

  it('names a peak with no formula by its m/z, as the chart legend does', () => {
    app = makeApp([peak('bp-1', null, 123.45678)])
    const { wrapper } = mountChip()
    expect(wrapper.find('.chip').text()).toBe('m/z 123.4568')
  })

  it('counts several selected peaks and lists them in the tooltip', () => {
    app = makeApp([peak('bp-1', 'C6H6O'), peak('bp-2', 'C7H8O'), peak('bp-3', null, 150)])
    const { wrapper } = mountChip()
    expect(wrapper.find('.chip').text()).toBe('3 Batch peaks')
    const text = wrapper.find('.chip').attributes('data-tooltip')
    expect(text).toContain('C6H6O')
    expect(text).toContain('C7H8O')
    expect(text).toContain('m/z 150.0000')
    expect(text).not.toContain('more')
  })

  it('lists only the first few of a long selection and counts the rest', () => {
    app = makeApp(Array.from({ length: 14 }, (_, i) => peak(`bp-${i}`, `C${i + 1}H4`)))
    const { wrapper } = mountChip()
    const text = wrapper.find('.chip').attributes('data-tooltip')
    expect(text).toContain('C10H4')
    expect(text).not.toContain('C11H4')
    expect(text).toContain('+4 more')
  })

  it('clears the ledger selection when removed, and when all filters are cleared', async () => {
    app = makeApp([peak('bp-1', 'C6H6O')])
    const { wrapper, filter } = mountChip()
    await wrapper.find('.remove').trigger('click')
    expect(app.data.batchPeak.unfocus).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.chip').exists()).toBe(false)
    expect(filter.active.value).toBe(false)

    app.data.batchPeak.selected = [peak('bp-2', 'C7H8O')]
    filter.clear()
    expect(app.data.batchPeak.unfocus).toHaveBeenCalledTimes(2)
  })

  it('shows nothing when no peak is selected', () => {
    app = makeApp([])
    const { wrapper, filter } = mountChip()
    expect(filter.active.value).toBe(false)
    expect(wrapper.find('.chip').exists()).toBe(false)
  })

  it('leaves a selection alone in targets mode, where nothing plots it', () => {
    app = makeApp([peak('bp-1', 'C6H6O')], 'targets')
    const { wrapper, filter } = mountChip()
    expect(filter.active.value).toBe(false)
    expect(wrapper.find('.chip').exists()).toBe(false)
    filter.clear()
    expect(app.data.batchPeak.unfocus).not.toHaveBeenCalled()
  })
})
