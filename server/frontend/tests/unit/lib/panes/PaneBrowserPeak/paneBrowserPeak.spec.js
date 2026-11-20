import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { inject, provide, reactive } from 'vue'

// The Peak Browser's assignment column, with peak-centric assignment on: the
// committed formula beside a mark for the stage that found it, which says on
// hover where the row came from in words rather than as the API's value.

vi.mock('@/lib/features', () => ({ peakAssignmentEnabled: true }))

let app
vi.mock('@/stores', () => ({ useApp: () => app }))

vi.mock('@/lib/panes/PaneBrowserPeak/stores', () => ({
  usePeakScroller: () => ({ bind: () => {}, sortField: null, sortOrder: null, setSort: () => {} })
}))

vi.mock('@/lib/base', () => ({
  BaseLoadError: { template: '<div class="load-error" />' },
  BaseTierTag: { props: ['tier', 'evidence', 'role', 'source'], template: '<span class="tier" />' }
}))

// The table hands its rows to its columns, and each column renders its body
// slot once per row, so a cell's markup is reachable without PrimeVue.
const ROWS = Symbol('rows')
const DataTableStub = {
  props: ['value'],
  setup(props) {
    provide(ROWS, props)
  },
  template: '<div class="table"><slot /></div>'
}
const ColumnStub = {
  props: ['header', 'field'],
  setup() {
    return { table: inject(ROWS) }
  },
  template:
    '<div class="column" :data-header="header">' +
    '<div v-for="(row, i) in table.value" :key="i" class="cell">' +
    '<slot name="body" :data="row" /></div></div>'
}

const { default: PaneBrowserPeak } = await import('@/lib/panes/PaneBrowserPeak/PaneBrowserPeak.vue')

function makeApp(assignmentsByPeak) {
  return reactive({
    ui: { help: { top: () => ({}), docUrl: () => '' } },
    data: {
      sample: { focusedId: 's-1' },
      match: { collection: { focusedId: null } },
      peak: {
        list: [
          { peak_id: 1, mz: 100.1, height: 10, match: [] },
          { peak_id: 2, mz: 200.2, height: 20, match: [] },
          { peak_id: 3, mz: 300.3, height: 30, match: [] }
        ],
        pending: false,
        error: null,
        focused: null,
        focusedId: null,
        segments: null
      },
      peakAssignment: {
        peak: {
          run: { peak_assignment_run_id: 'run-1' },
          tierCounts: { assigned: 0, candidate: 0, below_assignability: 0, unassigned: 0 },
          forPeak: (id) => assignmentsByPeak[id] ?? null
        }
      }
    }
  })
}

function mountPane() {
  const record = (el, binding) => el.setAttribute('data-tooltip', binding.value ?? '')
  return mount(PaneBrowserPeak, {
    props: { height: 300 },
    global: {
      stubs: {
        Panel: { template: '<div class="panel"><slot /></div>' },
        TabMenu: true,
        DataTable: DataTableStub,
        Column: ColumnStub,
        Button: true,
        ProgressSpinner: true
      },
      directives: { tooltip: { mounted: record, updated: record } }
    }
  })
}

let wrapper

beforeEach(() => {
  app = makeApp({
    1: { assigned_formula: 'C6H12O6', tier: 'assigned', source: 'database' },
    2: { assigned_formula: 'C7H8', tier: 'candidate', source: 'untargeted' },
    3: { assigned_formula: 'C4H4', tier: 'candidate', source: 'manual' }
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('PaneBrowserPeak source mark', () => {
  it('says on hover where the formula came from, in words', () => {
    wrapper = mountPane()
    const marks = wrapper.findAll('.source-icon')

    expect(marks.map((mark) => mark.attributes('data-tooltip'))).toEqual([
      'Matched from a target or reference list',
      'Found by the formula search'
    ])
  })

  it('marks only the two engine stages', () => {
    // A hand-curated row's mark is the tier chip's; this column adds none.
    wrapper = mountPane()

    expect(wrapper.findAll('.assignment-cell')).toHaveLength(3)
    expect(wrapper.findAll('.source-icon')).toHaveLength(2)
  })
})
