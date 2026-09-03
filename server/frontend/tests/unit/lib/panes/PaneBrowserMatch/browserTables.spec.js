import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// The match browser's tables used to be sized from the window: a height
// computed as ((windowHeight - 100) * split) / 100 - 50 was provided down as
// 'match-table-height'. Those constants predate the Targets/Assignments switch
// bar above the panes, so every table ran past the bottom of its pane - and the
// panel body has no overflow-y, so the excess was clipped rather than
// scrollable. They now take their height from the container instead, via
// PrimeVue's scrollHeight="flex".

const FRONTEND_DIR = join(import.meta.dirname, '..', '..', '..', '..', '..')
const PANE_DIR = join(FRONTEND_DIR, 'src/lib/panes/PaneBrowserMatch')

const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'Batch 1' }

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
      batch: { focused: BATCH, focusedId: BATCH.sample_batch_id },
      sample: {
        focused: null,
        focusedId: null,
        list: [],
        pending: false,
        focus: vi.fn(),
        unfocus: vi.fn()
      },
      workspace: { focused: null, list: [] },
      ionization: { mechanism: { list: [], focused: null, focus: vi.fn(), unfocus: vi.fn() } },
      batchPeak: {
        list: [],
        selected: [],
        tierCounts: {},
        pending: false,
        error: null,
        load: vi.fn()
      },
      batchPeakVerification: { list: [], forAnchor: () => null, isStale: () => false },
      match: {
        collection: {
          list: [],
          focused: null,
          focusedId: null,
          pending: false,
          error: null,
          load: vi.fn(),
          unfocus: vi.fn()
        },
        ion: {
          list: [],
          selected: [],
          focused: null,
          pending: false,
          error: null,
          load: vi.fn()
        },
        visualized: { ion: null, clear: vi.fn(), set: vi.fn() }
      }
    },
    ui: {
      help: helpStub,
      tab: { active: 'sample' },
      notification: { on: vi.fn() }
    },
    auth: { user: null }
  }
}

vi.mock('@/stores', () => ({ useApp: () => makeApp() }))

vi.mock('@/api', () => ({ api: { http: { post: vi.fn() } } }))

vi.mock('@/lib/base', () => ({
  BaseTabbedPanel: { template: '<div class="panel"><slot name="menu" /><slot /></div>' },
  BaseTierTag: true,
  BaseMatchTag: true,
  BaseCopyableField: true,
  BaseVerdictBadge: true
}))
vi.mock('@/lib/panes/PaneBrowserMatch/BatchPeakVerdictPopover.vue', () => ({
  default: { name: 'BatchPeakVerdictPopover', template: '<div class="verdict-popover-stub" />' }
}))

vi.mock('@/lib/panes/PaneBrowserMatch/stores', () => ({
  useCollectionContextMenu: () => ({
    entries: { value: [] },
    selection: null,
    dialog: { op: null },
    onClick: vi.fn()
  }),
  useIonContextMenu: () => ({ selection: null, onClick: vi.fn(), clear: vi.fn() }),
  useIonScroller: () => ({ bind: vi.fn(), scrollToIon: vi.fn() }),
  useIonTableCustomizer: () => ({ config: { columns: [] } })
}))

vi.mock('@/lib/panes/PaneBrowserSample/stores', () => ({
  useSampleScroller: () => ({ scrollToSample: vi.fn() })
}))

vi.mock('@/lib/dialogs', () => ({ PopoverTargetCompoundAdd: true }))

vi.mock('@/lib/panes/PaneBrowserMatch/MatchCollectionContextMenu.vue', () => ({
  default: { template: '<div />' }
}))

vi.mock('@/lib/panes/PaneBrowserMatch/MatchIonContextMenu.vue', () => ({
  default: { template: '<div />' }
}))

vi.mock('@/lib/panes/PaneBrowserMatch/MatchIonTableCustomizer.vue', () => ({
  default: { template: '<div />' }
}))

// Keeps the props so the test can read what the pane asked the table for. The
// types match PrimeVue's own, so a bare `scrollable` attribute casts to true
// here the way it does on the real DataTable.
const DataTableStub = {
  name: 'DataTable',
  props: {
    value: { type: Array, default: () => [] },
    dataKey: { type: String, default: null },
    scrollable: { type: Boolean, default: false },
    scrollHeight: { type: String, default: null },
    virtualScrollerOptions: { type: Object, default: null }
  },
  template: '<div class="datatable"><slot /></div>'
}

const GLOBAL = {
  stubs: {
    DataTable: DataTableStub,
    Column: { name: 'Column', template: '<div />' },
    Button: { template: '<button><slot /></button>' },
    InputText: true,
    Select: true
  },
  directives: { tooltip: {}, help: {} }
}

const { default: PaneBrowserBatchPeaks } =
  await import('@/lib/panes/PaneBrowserMatch/PaneBrowserBatchPeaks.vue')
const { default: MatchCollectionTable } =
  await import('@/lib/panes/PaneBrowserMatch/MatchCollectionTable.vue')
const { default: MatchIonTable } = await import('@/lib/panes/PaneBrowserMatch/MatchIonTable.vue')

async function mountPane(component) {
  // The batch-peak ledger reads the compute store for the refusal it renders,
  // so a pinia has to exist even though nothing here launches anything.
  setActivePinia(createPinia())
  const wrapper = mount(component, { global: GLOBAL })
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('match browser tables size themselves from their container', () => {
  it.each([
    ['batch peaks', PaneBrowserBatchPeaks],
    ['target collections', MatchCollectionTable],
    ['target ions', MatchIonTable]
  ])('%s asks for flex scrolling, not a pixel height', async (_name, component) => {
    const wrapper = await mountPane(component)
    const table = wrapper.findComponent(DataTableStub)

    expect(table.exists()).toBe(true)
    expect(table.props('scrollHeight')).toBe('flex')
    // scrollHeight="flex" is ignored unless the table is also scrollable:
    // PrimeVue only adds p-datatable-flex-scrollable when both are set.
    expect(table.props('scrollable')).toBe(true)
    // Virtual scrolling stays on - it is the flex mode that lets the scroller
    // re-measure when the splitter moves, since its height stays a percentage.
    expect(table.props('virtualScrollerOptions')).toBeTruthy()
  })
})

describe('no window-derived table height in the match browser', () => {
  const FILES = [
    'PaneBrowserMatch.vue',
    'MatchCollectionTable.vue',
    'MatchIonTable.vue',
    'PaneBrowserAssignment.vue',
    'PaneBrowserBatchPeaks.vue'
  ]

  it.each(FILES)('%s derives no height from the window', (name) => {
    const source = readFileSync(join(PANE_DIR, name), 'utf8')

    expect(source).not.toContain('useWindowSize')
    expect(source).not.toContain('match-table-height')
    // The old shape: :scrollHeight="`${tableHeight}px`" and its magic offsets.
    expect(source).not.toMatch(/scrollHeight="`\$\{/)
  })

  it.each(FILES.filter((name) => name !== 'PaneBrowserMatch.vue'))(
    '%s scrolls its table against the container',
    (name) => {
      const source = readFileSync(join(PANE_DIR, name), 'utf8')
      // Both halves, adjacent on the table itself. PrimeVue only applies the
      // flex scroll layout when scrollable && scrollHeight === 'flex', so
      // looking for the string alone would still pass with `scrollable` gone.
      expect(source).toMatch(/(?<![-:\w])scrollable\s+scrollHeight="flex"/)
    }
  )
})
