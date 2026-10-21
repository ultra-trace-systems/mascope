import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import PrimeVue from 'primevue/config'
import Tooltip from 'primevue/tooltip'

// The create button in the panel header spells itself out above an empty
// list, where it is the only way to make a first batch, and goes back to the
// icon beside a full one. It is named for a screen reader in both states, and
// explained in a tooltip only while the label is hidden.

const mocks = vi.hoisted(() => ({ app: null, menu: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

vi.mock('@/lib/base', () => ({
  BaseEmptyState: {
    props: ['icon', 'title', 'hint', 'note'],
    template:
      '<div class="empty-state"><strong class="title">{{ title }}</strong><span class="hint">{{ hint }}</span><slot name="actions" /><span class="note">{{ note }}</span></div>'
  },
  BaseTabbedPanel: { template: '<div class="panel"><slot name="menu" /><slot /></div>' },
  BaseCopyableField: true,
  BaseStatusIcon: true
}))

vi.mock('@/lib/panes/PaneBrowserSample/stores', () => ({
  useBatchContextMenu: () =>
    (mocks.menu = {
      dialog: { op: null, calibration: false },
      selection: null,
      row: null,
      onClick: vi.fn()
    }),
  useBatchTableConfig: () => ({
    config: { filters: { global: { value: null } }, sortField: null, sortOrder: null },
    filteredBatchList: []
  }),
  useBatchStatus: () => ({ config: {} })
}))

import BatchTable from '@/lib/panes/PaneBrowserSample/BatchTable.vue'

const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'Batch 1', status: null }

function makeApp(list) {
  return reactive({
    data: {
      batch: { list, focused: null, pending: false, error: null, load: vi.fn(), rematch: vi.fn() },
      dataset: { focused: { dataset_name: 'Dataset 1' } }
    },
    ui: {
      split: { top: 50 },
      help: { right: () => ({}), docUrl: (path = '') => `/docs/${path}` }
    }
  })
}

// The real PrimeVue Button, since the label and its name are what is under
// test; the table around it is not.
const mountTable = () =>
  mount(BatchTable, {
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

const createButton = (wrapper) => wrapper.get('button[aria-label="Create batch"]')

describe('the Create batch button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows its label above an empty list', () => {
    mocks.app = makeApp([])
    const button = createButton(mountTable())

    expect(button.classes()).not.toContain('hiddenlabel')
    expect(button.find('.p-button-label').text()).toBe('Create batch')
  })

  it('is icon-only beside a list with rows', () => {
    mocks.app = makeApp([BATCH])
    const button = createButton(mountTable())

    expect(button.classes()).toContain('hiddenlabel')
  })

  // The tooltip repeats the label, so it is bound only while the label is
  // hidden; PrimeVue's directive marks an element it is bound to.
  it('is explained in a tooltip only while icon-only', () => {
    mocks.app = makeApp([])
    expect(createButton(mountTable()).attributes('data-pd-tooltip')).toBeUndefined()

    mocks.app = makeApp([BATCH])
    expect(createButton(mountTable()).attributes('data-pd-tooltip')).toBe('true')
  })

  it('is named for a screen reader in both states', () => {
    mocks.app = makeApp([])
    expect(createButton(mountTable()).attributes('aria-label')).toBe('Create batch')

    mocks.app = makeApp([BATCH])
    expect(createButton(mountTable()).attributes('aria-label')).toBe('Create batch')
  })
})

// The table's empty slot: a dataset with no batches is told what a batch is
// and offered the first one; one whose batches the search hid is told that
// instead, with no create offer that would read as "you have none".
describe('the empty batch list', () => {
  it('names the empty list and offers to create the first batch', async () => {
    mocks.app = makeApp([])
    const empty = mountTable().get('.empty-state')

    expect(empty.get('.title').text()).toBe('No batches yet')
    expect(empty.get('.hint').text()).toMatch(/samples/)

    await empty.get('button').trigger('click')
    expect(mocks.menu.dialog.op).toBe('create')
  })

  // What the batch list's own menu pastes on empty space.
  it('says a batch or samples can be pasted in', () => {
    mocks.app = makeApp([])
    const note = mountTable().get('.empty-state .note').text()

    expect(note).toMatch(/right-click here to paste/)
    expect(note).toMatch(/a copied batch, or copied or cut samples/)
  })

  it('says the search hid the rows when there are batches', () => {
    mocks.app = makeApp([BATCH])
    const empty = mountTable().get('.empty-state')

    expect(empty.get('.title').text()).toBe('No batches match the search')
    expect(empty.find('button').exists()).toBe(false)
  })

  // A dataset opened from the list starts with [] while its batches load:
  // that is not "none yet".
  it('says nothing while the list loads', async () => {
    mocks.app = makeApp([])
    mocks.app.data.batch.pending = true
    const wrapper = mountTable()

    expect(wrapper.find('.empty-state').exists()).toBe(false)

    mocks.app.data.batch.pending = false
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.empty-state .title').text()).toBe('No batches yet')
  })
})
