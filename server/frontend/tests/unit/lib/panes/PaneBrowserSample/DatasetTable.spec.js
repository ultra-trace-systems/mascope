import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import PrimeVue from 'primevue/config'
import Tooltip from 'primevue/tooltip'

// The create button in the panel header spells itself out above an empty
// list, where it is the only way to make a first dataset, and goes back to
// the icon beside a full one. It is named for a screen reader in both states,
// and explained in a tooltip only while the label is hidden.

const mocks = vi.hoisted(() => ({ app: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

vi.mock('@/lib/base', () => ({
  BaseTabbedPanel: { template: '<div class="panel"><slot name="menu" /><slot /></div>' },
  BaseCopyableField: true
}))

vi.mock('@/lib/panes/PaneBrowserSample/stores', () => ({
  useDatasetContextMenu: () => ({ dialog: { op: null }, selection: null, onClick: vi.fn() }),
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
        DataTable: { template: '<div class="datatable"><slot /></div>' },
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
