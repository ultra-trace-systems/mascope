import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import { useAssignmentLauncher } from '@/lib/panes/PaneBrowserMatch/stores'

// Which run the assignment ledger is showing, and the way to start another one.
// Both used to sit in the ledger's own panel header; they moved into the
// browser's switch bar, one row up, where they belong to the paradigm rather
// than to whichever of its two ledgers is on screen.

let runList
let runError
let focusedSample

const runFocus = vi.fn()
const runUnfocus = vi.fn()

const helpStub = {
  set: vi.fn(),
  docUrl: (path = '') => `/docs/${path}`,
  directive: () => ({}),
  right: () => ({}),
  left: () => ({}),
  top: () => ({}),
  bottom: () => ({}),
  bottom_end: () => ({})
}

function makeApp() {
  return {
    data: {
      sample: { focused: focusedSample },
      peakAssignment: {
        run: {
          list: runList,
          error: runError,
          focused: runList[0] ?? null,
          focus: runFocus,
          unfocus: runUnfocus
        }
      }
    },
    ui: { help: helpStub }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

vi.mock('@/lib/base', async () => ({
  // Real: the point of the provenance tests below is that the bar hands this
  // component each run, which a stub could not tell us.
  BaseRunProvenance: (await vi.importActual('@/lib/base/BaseRunProvenance.vue')).default
}))

const GLOBAL_STUBS = {
  // No declared props, so `label` falls through as a DOM attribute and the
  // `[label="Assign peaks"]` selector matches - the same shape the ledger's
  // spec uses, so the two halves of the "never two buttons" rule are counted
  // the same way.
  Button: { template: '<button><slot /></button>' },
  // Renders both slots the run selector fills, so what a user sees closed and
  // what they see in the open list are both under test - and reports a choice
  // through v-model the way PrimeVue does, so the binding is exercised rather
  // than bypassed.
  Select: {
    props: ['options', 'modelValue'],
    emits: ['update:modelValue'],
    template:
      '<div class="select">' +
      '<span class="select-value"><slot name="value" :value="modelValue" placeholder="Select run" /></span>' +
      '<button v-for="o in options" :key="o.peak_assignment_run_id" class="select-option" ' +
      '@click="$emit(\'update:modelValue\', o)">' +
      '<slot name="option" :option="o" /></button>' +
      '</div>'
  }
}

const { default: AssignmentRunBar } =
  await import('@/lib/panes/PaneBrowserMatch/AssignmentRunBar.vue')

async function mountBar() {
  const wrapper = mount(AssignmentRunBar, {
    global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

const SAMPLE = { sample_item_id: 'si-1', sample_item_name: 'Sample 1' }

const IMPORTED = {
  peak_assignment_run_id: 'run-2',
  engine: 'peaky',
  engine_version: '0.6.0',
  status: 'completed',
  tier_bands: { assigned: 0.6, candidate: 0.3 },
  calibration: { method: 'offset-aware' }
}
const IN_APP = {
  peak_assignment_run_id: 'run-1',
  engine: 'mascope',
  engine_version: '0.2.0',
  status: 'completed',
  tier_bands: { assigned: 0.8, candidate: 0.5 },
  calibration: null
}

beforeEach(() => {
  setActivePinia(createPinia())
  runList = []
  runError = null
  focusedSample = SAMPLE
})
afterEach(() => vi.clearAllMocks())

// The run selector is where a reader learns which engine produced the ledger
// they are looking at. It matters here rather than only in the badge's own test
// because the bar auto-shows the newest completed run whatever produced it: an
// imported run is what a user sees by default, without ever opening the list.
describe('AssignmentRunBar run provenance', () => {
  const runSelect = (wrapper) => wrapper.find('.select')

  it('names the producing engine on the selected run, not only in the open list', async () => {
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountBar()

    expect(runSelect(wrapper).find('.select-value').text()).toContain('peaky')
  })

  it('keeps the run label in the closed selector', async () => {
    // The #value slot is handed the raw v-model value - the record off the run
    // store - not the matched option, so a label carried only on the option
    // copy renders blank here while every other assertion still passes.
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountBar()
    const closed = runSelect(wrapper).find('.select-value').text()

    expect(closed).toContain('#2')
    expect(closed).toContain('completed')
  })

  it('carries each run its own provenance in the list', async () => {
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountBar()
    const options = runSelect(wrapper).findAll('.select-option')

    expect(options).toHaveLength(2)
    // Newest first, so the import is #2 and the in-app run is #1.
    expect(options[0].text()).toContain('#2')
    expect(options[0].text()).toContain('peaky 0.6.0')
    expect(options[0].text()).toContain('calibration')
    expect(options[1].text()).toContain('Mascope 0.2.0')
    // An in-app run has no disclosure to make: its calibration is the sample's.
    expect(options[1].text()).not.toContain('calibration')
  })

  it('still marks an in-flight import as in progress', async () => {
    runList = [{ ...IMPORTED, status: 'importing' }]
    const wrapper = await mountBar()

    expect(runSelect(wrapper).find('.select-option').text()).toContain('importing…')
  })

  it('reports the choice back to the run store', async () => {
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountBar()

    // Through the control, not through the computed: the selector's v-model is
    // the half that would silently stop working if the writable computed were
    // ever bound one-way.
    await runSelect(wrapper).findAll('.select-option')[1].trigger('click')

    expect(runFocus).toHaveBeenCalledTimes(1)
    expect(runFocus.mock.calls[0][0].peak_assignment_run_id).toBe('run-1')
  })
})

// The bar renders in both assignment views - the batch-peak ledger and the
// per-sample one - so what it offers has to be right in a state the ledger's
// own spec never sees: no focused sample, and so no runs at all.
describe('AssignmentRunBar states', () => {
  const assignButton = (wrapper) => wrapper.find('[label="Assign peaks"]')

  it('collapses to nothing at batch level', async () => {
    // The run store only loads for a focused sample, so at batch level there is
    // nothing to select and nothing to add to. A dead "Select run" box over the
    // batch-peak ledger would be worse than an empty bar - and so would an empty
    // wrapper, which grows to fill the bar and would push the paradigm switch
    // off centre.
    focusedSample = null
    const wrapper = await mountBar()

    expect(wrapper.find('.run-bar').exists()).toBe(false)
    // Nothing rendered at all, comments aside - not an empty div.
    expect(wrapper.element.children).toHaveLength(0)
  })

  // Not the same state, and the one the previous test would pass in by
  // accident: the sample is gone but the run list has not caught up yet. The
  // selector is still worth showing; the launch button has nothing to launch
  // for, so it is offered but disabled.
  it('disables the launch button when the run list outlives its sample', async () => {
    focusedSample = null
    runList = [IN_APP]
    const wrapper = await mountBar()

    expect(wrapper.find('.select').exists()).toBe(true)
    expect(assignButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('offers the selector and the launch button once a run exists', async () => {
    runList = [IN_APP]
    const wrapper = await mountBar()

    expect(wrapper.find('.select').exists()).toBe(true)
    expect(assignButton(wrapper).exists()).toBe(true)
    expect(assignButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  // The ledger's other half of the "never two Assign peaks buttons at once"
  // rule: with no runs the ledger's empty state carries the only call to
  // action, so the bar must not offer a second one a row above it.
  it('leaves the call to action to the ledger when there are no runs', async () => {
    const wrapper = await mountBar()

    expect(assignButton(wrapper).exists()).toBe(false)
  })

  // ...but a run list that failed to load gets the load error, not the empty
  // state, so there is no button down there to defer to. A failed load must not
  // also cost the user the way to start a run.
  it('keeps the launch button when the run list failed to load', async () => {
    runError = new Error('nope')
    const wrapper = await mountBar()

    expect(wrapper.find('.select').exists()).toBe(false)
    expect(assignButton(wrapper).exists()).toBe(true)
  })

  it('opens the dialog the ledger owns', async () => {
    runList = [IN_APP]
    const wrapper = await mountBar()

    await assignButton(wrapper).trigger('click')

    // The bar holds no dialog of its own: the launch, its configuration and the
    // refusal it may come back with all stay beside the ledger they are about.
    expect(useAssignmentLauncher().configVisible).toBe(true)
  })
})
