import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The switch bar is what made the old window-derived table heights wrong: it
// sits above the panes and nothing subtracted it. Now the bar is simply the
// first row of a flex column, so what matters is that the column is there in
// both flag states - with the bar when peak assignment is on, without it when
// it is off. The mode itself lives in the app.ui.matchMode store (mocked here;
// its flag-off pinning to targets is covered by the store's own spec).

let flagOn = true
let initialMode = 'targets'
let focusedSample = null

vi.mock('@/lib/features', () => ({
  get peakAssignmentEnabled() {
    return flagOn
  }
}))

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
      match: {
        collection: { focused: null },
        ion: { focused: null, unfocus: vi.fn() },
        visualized: { clear: vi.fn() }
      }
    },
    ui: {
      help: helpStub,
      matchMode: {
        mode: initialMode,
        options: [
          { label: 'Targets', value: 'targets' },
          { label: 'Assignments', value: 'assignments' }
        ]
      }
    }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

// Stubbed at the module boundary: the panes themselves are covered elsewhere,
// and compiling them here would drag in the whole store and api tree.
const paneStub = (name) => ({ default: { name, template: `<div class="${name}" />` } })
// The run bar rides in the switch bar rather than being a pane, but it is
// stubbed for the same reason: it reads the assignment-run store, which this
// file's app facade does not carry. Its own behaviour is covered in
// assignmentRunBar.spec.js; what matters here is where it renders.
vi.mock('@/lib/panes/PaneBrowserMatch/AssignmentRunBar.vue', () => paneStub('AssignmentRunBar'))
// The batch-level counterpart, in the same corner of the bar: it reaches the
// HTTP client through its compute store, which this file has no runtime for.
vi.mock('@/lib/panes/PaneBrowserMatch/BatchPeakComputeBar.vue', () =>
  paneStub('BatchPeakComputeBar')
)
vi.mock('@/lib/panes/PaneBrowserMatch/MatchCollectionTable.vue', () =>
  paneStub('MatchCollectionTable')
)
vi.mock('@/lib/panes/PaneBrowserMatch/MatchIonTable.vue', () => paneStub('MatchIonTable'))
vi.mock('@/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue', () =>
  paneStub('PaneBrowserAssignment')
)
vi.mock('@/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue', () =>
  paneStub('PaneBrowserBatchPeaks')
)

const { default: PaneBrowserMatch } =
  await import('@/lib/panes/PaneBrowserMatch/PaneBrowserMatch.vue')

function mountSwitch() {
  return mount(PaneBrowserMatch, {
    global: {
      stubs: { SelectButton: true },
      directives: { tooltip: {}, help: {} }
    }
  })
}

describe('PaneBrowserMatch', () => {
  beforeEach(() => {
    flagOn = true
    initialMode = 'targets'
    focusedSample = null
  })

  it('stacks the switch bar above the pane in one flex column', () => {
    const wrapper = mountSwitch()
    const column = wrapper.find('.browser-switch')

    expect(column.exists()).toBe(true)
    // Order is the whole mechanism: the bar takes its natural height as the
    // first row and the pane takes what is left. A pane rendered above the bar,
    // or no pane at all, is the layout this change exists to prevent.
    expect([...column.element.children].map((el) => el.className)).toEqual([
      'switch-bar',
      'MatchCollectionTable'
    ])
  })

  it('keeps the column without the bar when peak assignment is off', () => {
    flagOn = false
    const wrapper = mountSwitch()

    // No bar to subtract, so the pane gets the whole column - the legacy
    // layout, reached by the same rules rather than by a second code path.
    // (With the flag off the matchMode store pins the mode to targets; that
    // pinning is the store spec's contract, mirrored by the mock here.)
    expect(wrapper.find('.switch-bar').exists()).toBe(false)
    expect(wrapper.find('.browser-switch').exists()).toBe(true)
    expect(wrapper.find('.MatchCollectionTable').exists()).toBe(true)
    expect(wrapper.find('.PaneBrowserBatchPeaks').exists()).toBe(false)
  })

  // The ledger's own action - the run selector and Assign-peaks for a sample,
  // Compute-batch-peaks for the batch - sits in the bar rather than in the
  // ledger's header. That only keeps the flag-off layout if they are INSIDE the
  // flag-gated bar: a row of their own beside it would survive the v-if above
  // and put assignment controls in the legacy view.
  it('keeps both action bars inside the flag-gated switch bar', () => {
    flagOn = false
    initialMode = 'assignments'

    expect(mountSwitch().find('.BatchPeakComputeBar').exists()).toBe(false)

    focusedSample = { sample_item_id: 1 }
    expect(mountSwitch().find('.AssignmentRunBar').exists()).toBe(false)
  })

  it('offers an action bar only in the assignments paradigm', () => {
    const targets = mountSwitch()
    expect(targets.find('.AssignmentRunBar').exists()).toBe(false)
    expect(targets.find('.BatchPeakComputeBar').exists()).toBe(false)

    initialMode = 'assignments'
    const wrapper = mountSwitch()
    const bar = wrapper.find('.BatchPeakComputeBar')

    expect(bar.exists()).toBe(true)
    // One row, not two: the bar is a child of the switch bar, so the column
    // still has exactly the bar and the pane in it and the pane keeps the rest
    // of the height (`.browser-switch > :not(.switch-bar)` takes what is left).
    expect(wrapper.find('.switch-bar').element.contains(bar.element)).toBe(true)
    expect([...wrapper.find('.browser-switch').element.children].map((el) => el.className)).toEqual(
      ['switch-bar', 'PaneBrowserBatchPeaks']
    )
  })

  // The two bars swap on the same condition as the two ledgers, so the bar
  // always offers the action that fills the table underneath it - and never
  // both at once, which would put two launch buttons in one row.
  it('swaps the batch action for the sample one when a sample is focused', () => {
    initialMode = 'assignments'
    focusedSample = { sample_item_id: 1 }
    const wrapper = mountSwitch()

    expect(wrapper.find('.AssignmentRunBar').exists()).toBe(true)
    expect(wrapper.find('.BatchPeakComputeBar').exists()).toBe(false)
    expect([...wrapper.find('.browser-switch').element.children].map((el) => el.className)).toEqual(
      ['switch-bar', 'PaneBrowserAssignment']
    )
  })

  it('swaps to the assignment panes when the store mode says so', () => {
    initialMode = 'assignments'
    const wrapper = mountSwitch()

    expect(wrapper.find('.PaneBrowserBatchPeaks').exists()).toBe(true)
    expect(wrapper.find('.MatchCollectionTable').exists()).toBe(false)
  })

  it('provides no table height for the panes to consume', () => {
    const wrapper = mountSwitch()

    // The panes measure their own container now; a height provided here would
    // be the window-derived value coming back.
    expect(wrapper.vm.$.provides?.['match-table-height']).toBeUndefined()
  })
})
