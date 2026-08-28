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
      sample: { focused: null },
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
