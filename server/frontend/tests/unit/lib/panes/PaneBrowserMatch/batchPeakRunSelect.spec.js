import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

let app
vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('@/lib/base', () => ({
  BaseRunProvenance: { props: ['run', 'compact'], template: '<span class="provenance" />' }
}))

const { default: BatchPeakRunSelect } =
  await import('@/lib/panes/PaneBrowserMatch/BatchPeakRunSelect.vue')

const helpStub = {
  docUrl: (path = '') => `/docs/${path}`,
  bottom: () => ({})
}

const run = (id, action, over = {}) => ({
  batch_peak_run_id: id,
  action,
  engine: 'mascope',
  engine_version: '1.0.0',
  status: 'completed',
  current: false,
  ...over
})
const RUNS = [
  run('r3', 'search_untargeted', { current: true }),
  run('r2', 'rebuild'),
  run('r1', 'fold')
]

const focus = vi.fn()
const unfocus = vi.fn()

function makeApp(list, focused = list[0] ?? null) {
  return reactive({
    data: { batchPeakRun: { list, focused, focus, unfocus } },
    ui: { help: helpStub }
  })
}

const SelectStub = {
  props: ['modelValue', 'options', 'optionLabel', 'placeholder'],
  emits: ['update:modelValue'],
  template:
    '<div class="select"><span class="value">{{ modelValue?._label ?? modelValue?.batch_peak_run_id }}</span><button v-for="o in options" :key="o.batch_peak_run_id" class="option" @click="$emit(\'update:modelValue\', o)">{{ o._label }}</button></div>'
}

function mountSelect(list, focused) {
  app = makeApp(list, focused)
  return mount(BatchPeakRunSelect, { global: { stubs: { Select: SelectStub } } })
}

describe('BatchPeakRunSelect', () => {
  it('lists the runs newest first with an ordinal, the action and the state', () => {
    const wrapper = mountSelect(RUNS)
    const labels = wrapper.findAll('.option').map((o) => o.text())
    expect(labels).toEqual([
      '#3 · Untargeted search · current',
      '#2 · Rebuild · completed',
      '#1 · Folded samples · completed'
    ])
  })

  it('marks a running run as such', () => {
    const wrapper = mountSelect([run('r4', 'rebuild', { status: 'running' }), ...RUNS])
    expect(wrapper.findAll('.option')[0].text()).toBe('#4 · Rebuild · running…')
  })

  it('focuses the run the user picks', async () => {
    const wrapper = mountSelect(RUNS)
    await wrapper.findAll('.option')[2].trigger('click')
    expect(focus).toHaveBeenCalledWith(expect.objectContaining({ batch_peak_run_id: 'r1' }))
  })

  it('renders nothing for a batch without a ledger', () => {
    const wrapper = mountSelect([], null)
    expect(wrapper.find('.select').exists()).toBe(false)
  })
})
