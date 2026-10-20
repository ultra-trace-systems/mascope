import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import PrimeVue from 'primevue/config'
import Tooltip from 'primevue/tooltip'

// The create button in the panel header spells itself out above an empty
// list, where it is the only way to make a first dataset, and goes back to
// the icon beside a full one. It is named for a screen reader in both states,
// and explained in a tooltip only while the label is hidden.

const mocks = vi.hoisted(() => ({ app: null, menu: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

vi.mock('@/lib/base', () => ({
  BaseEmptyState: {
    props: ['icon', 'title', 'hint'],
    template:
      '<div class="empty-state"><strong class="title">{{ title }}</strong><span class="hint">{{ hint }}</span><slot name="actions" /></div>'
  },
  BaseTabbedPanel: { template: '<div class="panel"><slot name="menu" /><slot /></div>' },
  BaseCopyableField: true
}))

vi.mock('@/lib/panes/PaneBrowserSample/stores', () => ({
  useDatasetContextMenu: () =>
    (mocks.menu = { dialog: { op: null }, selection: null, onClick: vi.fn() }),
  useDatasetTableConfig: () => ({
    config: { filters: { global: { value: null } }, sortField: null, sortOrder: null }
  })
}))

import DatasetTable from '@/lib/panes/PaneBrowserSample/DatasetTable.vue'

const DATASET = { dataset_id: 'ds-1', dataset_name: 'Dataset 1' }

function makeApp(list) {
  return reactive({
    data: {
      dataset: { list, focused: null, pending: false, error: null, load: vi.fn() },
      workspace: { focused: { workspace_name: 'Lab' } }
    },
    ui: {
      split: { top: 50 },
      help: { right: () => ({}), docUrl: (path = '') => `/docs/${path}` }
    }
  })
}

// The real PrimeVue Button and Tooltip, since the label, its name and when it
// is explained are what is under test; the table around it is not.
const mountTable = () =>
  mount(DatasetTable, {
    global: {
      plugins: [PrimeVue],
      directives: { tooltip: Tooltip },
      stubs: {
        // The table decides when its #empty slot shows (no rows, or none the
        // search left); the stub always renders it, so what the pane puts
        // in it is what these tests read.
        DataTable: { template: '<div class="datatable"><slot name="empty" /><slot /></div>' },
        Column: true,
        IconField: { template: '<div><slot /></div>' },
        InputIcon: true,
        InputText: true
      }
    }
  })

const createButton = (wrapper) => wrapper.get('button[aria-label="Create dataset"]')

describe('the Create dataset button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows its label above an empty list', () => {
    mocks.app = makeApp([])
    const button = createButton(mountTable())

    expect(button.classes()).not.toContain('hiddenlabel')
    expect(button.find('.p-button-label').text()).toBe('Create dataset')
  })

  // The tooltip repeats the label, so it is bound only while the label is
  // hidden; PrimeVue's directive marks an element it is bound to.
  it('is explained in a tooltip only while icon-only', () => {
    mocks.app = makeApp([])
    expect(createButton(mountTable()).attributes('data-pd-tooltip')).toBeUndefined()

    mocks.app = makeApp([DATASET])
    expect(createButton(mountTable()).attributes('data-pd-tooltip')).toBe('true')
  })

  it('is icon-only beside a list with rows', () => {
    mocks.app = makeApp([DATASET])
    const button = createButton(mountTable())

    expect(button.classes()).toContain('hiddenlabel')
  })

  it('is named for a screen reader in both states', () => {
    mocks.app = makeApp([])
    expect(createButton(mountTable()).attributes('aria-label')).toBe('Create dataset')

    mocks.app = makeApp([DATASET])
    expect(createButton(mountTable()).attributes('aria-label')).toBe('Create dataset')
  })

  it('follows the list as it fills and empties', async () => {
    mocks.app = makeApp([])
    const wrapper = mountTable()

    mocks.app.data.dataset.list = [DATASET]
    await wrapper.vm.$nextTick()
    expect(createButton(wrapper).classes()).toContain('hiddenlabel')

    mocks.app.data.dataset.list = []
    await wrapper.vm.$nextTick()
    expect(createButton(wrapper).classes()).not.toContain('hiddenlabel')
  })
})

// The table's empty slot: a workspace with no datasets is told what a
// dataset is and offered the first one; one whose datasets the search hid is
// told that instead, with no create offer that would read as "you have none".
describe('the empty dataset list', () => {
  it('names the empty list and offers to create the first dataset', async () => {
    mocks.app = makeApp([])
    const empty = mountTable().get('.empty-state')

    expect(empty.get('.title').text()).toBe('No datasets yet')
    expect(empty.get('.hint').text()).toMatch(/study or campaign/)

    await empty.get('button').trigger('click')
    expect(mocks.menu.dialog.op).toBe('create')
  })

  it('says the search hid the rows when there are datasets', () => {
    mocks.app = makeApp([DATASET])
    const empty = mountTable().get('.empty-state')

    expect(empty.get('.title').text()).toBe('No datasets match the search')
    expect(empty.find('button').exists()).toBe(false)
  })
})
