import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { ref } from 'vue'

// The per-sample launcher's job after the assign endpoint became synchronous:
// a run that is refused (409) or a sample that cannot be assigned (422) arrives
// as a rejection, and must land as a readable reason in the pane rather than an
// uncaught promise behind a dialog that never closes.

const assign = vi.fn()

const SAMPLE = { sample_item_id: 'si-1', sample_item_name: 'Sample 1' }

let focusedSampleId
let runList

// Minimal help-mode facade: the pane registers help cards through these calls;
// the tests only need them to resolve.
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
      sample: { focused: SAMPLE, focusedId: focusedSampleId.value },
      peak: { list: [], focused: null, focus: vi.fn() },
      ionization: { mechanism: { list: [] } },
      peakAssignment: {
        run: {
          list: runList,
          focused: runList[0] ?? null,
          focus: vi.fn(),
          unfocus: vi.fn(),
          assign
        },
        peak: {
          list: [],
          pending: false,
          tierCounts: {},
          childrenOf: () => [],
          forPeak: () => null
        },
        verification: { forAssignment: () => null }
      }
    },
    ui: { tab: { active: 'sample' }, help: helpStub }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

vi.mock('@/lib/base', async () => ({
  BaseTabbedPanel: { template: '<div><slot name="menu" /><slot /></div>' },
  BaseLoadError: true,
  BaseTierTag: true,
  BaseVerdictBadge: true,
  // Real: the point of the run-provenance test below is that the pane hands
  // this component each run, which a stub could not tell us.
  BaseRunProvenance: (await vi.importActual('@/lib/base/BaseRunProvenance.vue')).default
}))

vi.mock('@/lib/dialogs', () => ({ PeakAssignConfigForm: true }))

const GLOBAL_STUBS = {
  Dialog: { template: '<div><slot /><slot name="footer" /></div>' },
  Message: { template: '<div class="pane-message"><slot /></div>' },
  Button: { template: '<button><slot /></button>' },
  // Renders both slots the run selector fills, so what a user sees closed and
  // what they see in the open list are both under test.
  Select: {
    props: ['options', 'modelValue'],
    template:
      '<div class="select">' +
      '<span class="select-value"><slot name="value" :value="modelValue" placeholder="Select run" /></span>' +
      '<span v-for="o in options" :key="o.peak_assignment_run_id" class="select-option">' +
      '<slot name="option" :option="o" /></span>' +
      '</div>'
  },
  DataTable: true,
  Column: true,
  ProgressSpinner: true,
  ToggleSwitch: true
}

/** An axios-shaped rejection carrying a server message. */
function apiError(status, message) {
  return { response: { status, data: { error: message } } }
}

const { default: PaneBrowserAssignment } =
  await import('@/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue')

// Imported statically (vi.mock is hoisted above it, so the stubs still apply):
// compiling this pane is the expensive part, and as a dynamic import inside the
// first test it lands on that one test's clock.
async function mountPane() {
  const wrapper = mount(PaneBrowserAssignment, {
    global: {
      stubs: GLOBAL_STUBS,
      directives: { tooltip: {}, help: {} },
      provide: { 'match-table-height': ref(300) }
    }
  })
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('PaneBrowserAssignment launcher', () => {
  beforeEach(() => {
    focusedSampleId = ref('si-1')
    runList = []
    assign.mockReset()
    assign.mockResolvedValue({ data: [{ peak_assignment_run_id: 'run-1' }] })
  })
  afterEach(() => vi.clearAllMocks())

  it('launches with the config and reports nothing when accepted', async () => {
    const wrapper = await mountPane()
    wrapper.vm.configVisible = true
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(assign).toHaveBeenCalledTimes(1)
    expect(assign.mock.calls[0][0]).toBe('si-1')
    expect(assign.mock.calls[0][1].run_untargeted).toBe(true)
    expect(wrapper.vm.launchError).toBeNull()
    expect(wrapper.vm.configVisible).toBe(false)
  })

  it('closes the dialog and shows the reason when the sample is ineligible', async () => {
    assign.mockRejectedValue(
      apiError(
        422,
        "Peak assignment is not possible for sample 'Sample 1': blank sample (no peaks)."
      )
    )
    const wrapper = await mountPane()
    wrapper.vm.configVisible = true
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.configVisible).toBe(false)
    expect(wrapper.vm.launchRefused).toBe(true)
    expect(wrapper.vm.launchError).toContain('blank sample')
    expect(wrapper.find('.pane-message').text()).toContain('blank sample')
  })

  it('shows the in-flight refusal when a run is already assigning the sample', async () => {
    assign.mockRejectedValue(
      apiError(409, "Peak assignment is already running for sample 'Sample 1'.")
    )
    const wrapper = await mountPane()

    await wrapper.vm.launch()

    expect(wrapper.vm.launchRefused).toBe(true)
    expect(wrapper.vm.launchError).toContain('already running')
    expect(wrapper.vm.submitting).toBe(false)
  })

  it('distinguishes a genuine failure from a refusal', async () => {
    assign.mockRejectedValue(apiError(500, 'Unexpected error (ref: abc12345).'))
    const wrapper = await mountPane()

    await wrapper.vm.launch()

    expect(wrapper.vm.launchRefused).toBe(false)
    expect(wrapper.vm.launchError).toContain('Unexpected error')
  })

  it('clears a previous refusal when the dialog is reopened', async () => {
    assign.mockRejectedValue(apiError(409, 'busy'))
    const wrapper = await mountPane()

    await wrapper.vm.launch()
    expect(wrapper.vm.launchError).toBe('busy')

    wrapper.vm.configVisible = true
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.launchError).toBeNull()
  })

  it('does nothing without a focused sample', async () => {
    focusedSampleId = ref(null)
    const wrapper = await mountPane()

    await wrapper.vm.launch()

    expect(assign).not.toHaveBeenCalled()
  })
})

// The run selector is where a reader learns which engine produced the ledger
// they are looking at. It matters here rather than only in the badge's own test
// because the pane auto-shows the newest completed run whatever produced it: an
// imported run is what a user sees by default, without ever opening the list.
describe('PaneBrowserAssignment run provenance', () => {
  beforeEach(() => {
    focusedSampleId = ref('si-1')
    assign.mockReset()
    assign.mockResolvedValue({ data: [{ peak_assignment_run_id: 'run-1' }] })
  })
  afterEach(() => vi.clearAllMocks())

  const IMPORTED = {
    peak_assignment_run_id: 'run-2',
    engine: 'peaky',
    engine_version: '0.6.0',
    status: 'completed',
    tier_bands: { identified: 0.6, candidate: 0.3 },
    calibration: { method: 'offset-aware' }
  }
  const IN_APP = {
    peak_assignment_run_id: 'run-1',
    engine: 'mascope',
    engine_version: '0.2.0',
    status: 'completed',
    tier_bands: { identified: 0.8, candidate: 0.5 },
    calibration: null
  }

  // The menu holds two Selects (runs, then the verdict filter) and the stub is
  // generic, so scope every query to the first one.
  const runSelect = (wrapper) => wrapper.findAll('.select')[0]

  it('names the producing engine on the selected run, not only in the open list', async () => {
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountPane()

    expect(runSelect(wrapper).find('.select-value').text()).toContain('peaky')
  })

  it('keeps the run label in the closed selector', async () => {
    // The #value slot is handed the raw v-model value - the record off the run
    // store - not the matched option, so a label carried only on the option
    // copy renders blank here while every other assertion still passes.
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountPane()
    const closed = runSelect(wrapper).find('.select-value').text()

    expect(closed).toContain('#2')
    expect(closed).toContain('completed')
  })

  it('carries each run its own provenance in the list', async () => {
    runList = [IMPORTED, IN_APP]
    const wrapper = await mountPane()
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
    const wrapper = await mountPane()

    expect(runSelect(wrapper).find('.select-option').text()).toContain('importing…')
  })
})
