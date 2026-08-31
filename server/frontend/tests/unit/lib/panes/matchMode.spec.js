import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// One switch, two consumers. The browser shell owns the only Targets/Assignments
// control; the Batch tab has none and follows the same `app.ui.matchMode`. These
// tests mount both against ONE store instance, which is the whole point: they
// used to hold a ref each, and the batch tab's was unpersisted, so the browser
// could sit in Assignments while the chart plotted Targets.

const { flags, hold } = vi.hoisted(() => ({
  flags: { peakAssignment: true },
  hold: { makeApp: null }
}))

// A getter so each test can flip the peak-centric assignment flag.
vi.mock('@/lib/features', () => ({
  get peakAssignmentEnabled() {
    return flags.peakAssignment
  }
}))

// Each component calls useApp() itself and gets its OWN facade object, exactly
// as the real aggregate does - so what the panes share is the Pinia store
// instance, not a test fixture handed to both.
vi.mock('@/stores', () => ({ useApp: () => hold.makeApp() }))

// The browser's four panes and the two charts are heavy and irrelevant here -
// only which one rendered matters. (Inlined rather than built by a helper: a
// vi.mock factory is hoisted above every top-level binding.)
//
// The run bar shares the switch bar with the control under test but reads the
// assignment-run store, which this file's deliberately minimal app facade does
// not carry; stubbed for the same reason the panes are.
vi.mock('@/lib/panes/PaneBrowserMatch/AssignmentRunBar.vue', () => ({
  default: { template: '<div class="assignment-run-bar" />' }
}))
vi.mock('@/lib/panes/PaneBrowserMatch/BatchPeakComputeBar.vue', () => ({
  default: { template: '<div class="assignment-run-bar" />' }
}))
vi.mock('@/lib/panes/PaneBrowserMatch/MatchCollectionTable.vue', () => ({
  default: { template: '<div class="match-collection-table" />' }
}))
vi.mock('@/lib/panes/PaneBrowserMatch/MatchIonTable.vue', () => ({
  default: { template: '<div class="match-ion-table" />' }
}))
vi.mock('@/lib/panes/PaneBrowserMatch/PaneBrowserAssignment.vue', () => ({
  default: { template: '<div class="pane-browser-assignment" />' }
}))
vi.mock('@/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue', () => ({
  default: { template: '<div class="pane-browser-batch-peaks" />' }
}))
vi.mock('@/lib/charts', () => ({
  ChartBatchOverview: { template: '<div class="chart-batch-overview" />' },
  ChartBatchAssignments: { template: '<div class="chart-batch-assignments" />' }
}))

import { useMatchMode } from '@/stores/ui/matchMode'
import PaneBrowserMatch from '@/lib/panes/PaneBrowserMatch/PaneBrowserMatch.vue'
import PaneTabBatch from '@/lib/panes/PaneTabBatch.vue'

const MODE_KEY = 'mascope.browserMatch.mode'

// Renders one button per option and reports clicks the way SelectButton does,
// so a test can work the switch instead of assigning the store behind its back.
const SelectButtonStub = {
  props: ['modelValue', 'options'],
  emits: ['update:modelValue'],
  template: `
    <div class="select-button">
      <button
        v-for="option in options"
        :key="option.value"
        class="option"
        :class="{ active: option.value === modelValue }"
        @click="$emit('update:modelValue', option.value)"
      >{{ option.label }}</button>
    </div>`
}

const GLOBAL = {
  stubs: { SelectButton: SelectButtonStub },
  directives: { help: {}, tooltip: {} }
}

function makeApp() {
  return {
    data: reactive({
      match: {
        collection: { focused: null },
        ion: { focused: null, unfocus: vi.fn() },
        visualized: { clear: vi.fn() }
      },
      sample: { focused: null }
    }),
    ui: {
      split: { bottom: 50 },
      help: { docUrl: (path = '') => `/docs/${path}` },
      matchMode: useMatchMode()
    }
  }
}

hold.makeApp = makeApp

const mountBrowser = () => mount(PaneBrowserMatch, { global: GLOBAL })
const mountBatch = () => mount(PaneTabBatch, { global: GLOBAL })

describe('the single Targets/Assignments switch', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    flags.peakAssignment = true
  })

  it('is rendered by the browser and by nothing else', () => {
    expect(mountBrowser().findAll('.select-button')).toHaveLength(1)
    expect(mountBatch().findAll('.select-button')).toHaveLength(0)
  })

  it('starts on Targets, with both consumers on the targeted view', () => {
    const browser = mountBrowser()
    const batch = mountBatch()

    expect(browser.find('.match-collection-table').exists()).toBe(true)
    expect(browser.find('.pane-browser-batch-peaks').exists()).toBe(false)
    expect(batch.find('.chart-batch-overview').exists()).toBe(true)
  })

  it('moves the browser panes and the batch chart together when flipped', async () => {
    const browser = mountBrowser()
    const batch = mountBatch()

    await browser.findAll('.option')[1].trigger('click')

    // Browser: no sample focused, so the batch-peaks ledger is the assignments
    // pane - and the chart it drives is now the one on screen.
    expect(browser.find('.pane-browser-batch-peaks').exists()).toBe(true)
    expect(browser.find('.match-collection-table').exists()).toBe(false)
    expect(batch.find('.chart-batch-assignments').exists()).toBe(true)
    expect(batch.find('.chart-batch-overview').exists()).toBe(false)

    await browser.findAll('.option')[0].trigger('click')

    expect(browser.find('.match-collection-table').exists()).toBe(true)
    expect(batch.find('.chart-batch-overview').exists()).toBe(true)
  })

  it('marks the active option, so the switch reads the stored choice back', () => {
    localStorage.setItem(MODE_KEY, 'assignments')
    setActivePinia(createPinia())

    const active = mountBrowser()
      .findAll('.option')
      .filter((o) => o.classes('active'))

    expect(active).toHaveLength(1)
    expect(active[0].text()).toBe('Assignments')
  })

  it('brings the batch chart back in the stored mode on a fresh load', () => {
    // The batch tab's own ref was never persisted, so a reload always put the
    // chart back on Targets however the browser came back. (It did not reset on
    // a tab switch: Dashboard's <Tabs> is not lazy, so the panel stays mounted
    // and is merely v-show hidden.)
    localStorage.setItem(MODE_KEY, 'assignments')
    setActivePinia(createPinia())

    expect(mountBatch().find('.chart-batch-assignments').exists()).toBe(true)
  })

  describe('with peak-centric assignment off', () => {
    beforeEach(() => {
      flags.peakAssignment = false
      localStorage.setItem(MODE_KEY, 'assignments')
      setActivePinia(createPinia())
    })

    it('hides the switch entirely', () => {
      expect(mountBrowser().find('.select-button').exists()).toBe(false)
    })

    it('pins both consumers to the targeted view despite the stored choice', () => {
      expect(mountBrowser().find('.match-collection-table').exists()).toBe(true)
      expect(mountBatch().find('.chart-batch-overview').exists()).toBe(true)
    })
  })
})
