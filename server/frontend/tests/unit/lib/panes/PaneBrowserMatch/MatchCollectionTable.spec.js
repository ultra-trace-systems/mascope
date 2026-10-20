import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import PrimeVue from 'primevue/config'
import Tooltip from 'primevue/tooltip'

// The lower-left pane lists the target collections of the open batch. With no
// batch open it says what it is for and how it fills, and the create button
// spells itself out above an empty list rather than hiding behind the icon it
// uses beside a full one.

const mocks = vi.hoisted(() => ({ app: null, menu: null }))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))

vi.mock('@/lib/base', () => ({
  BaseEmptyState: {
    props: ['icon', 'title', 'hint'],
    template:
      '<div class="empty-state"><strong class="title">{{ title }}</strong><span class="hint">{{ hint }}</span><slot name="actions" /></div>'
  },
  BaseTabbedPanel: {
    name: 'BaseTabbedPanel',
    props: ['breadcrumb', 'label', 'icon'],
    template: '<div class="panel"><slot name="menu" /><slot /></div>'
  },
  BaseMatchTag: true,
  BaseCopyableField: true
}))

vi.mock('@/lib/panes/PaneBrowserMatch/stores', () => ({
  useCollectionContextMenu: () =>
    (mocks.menu = {
      entries: { value: [] },
      selection: null,
      dialog: { op: null },
      onClick: vi.fn()
    })
}))

vi.mock('@/lib/panes/PaneBrowserMatch/MatchCollectionContextMenu.vue', () => ({
  default: { template: '<div />' }
}))

import MatchCollectionTable from '@/lib/panes/PaneBrowserMatch/MatchCollectionTable.vue'

const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'Batch 1' }
const COLLECTION = {
  target_collection_id: 'tc-1',
  target_collection_name: 'Targets',
  target_collection_type: 'Target',
  workspace_id: 'ws-1',
  match: null
}

function makeApp({ batch = null, collections = [] } = {}) {
  return reactive({
    data: {
      batch: { focused: batch },
      sample: { focused: null, unfocus: vi.fn() },
      workspace: { list: [] },
      match: {
        collection: {
          list: collections,
          focused: null,
          pending: false,
          error: null,
          load: vi.fn()
        }
      }
    },
    ui: { help: { right: () => ({}), docUrl: (path = '') => `/docs/${path}` } }
  })
}

// The real PrimeVue Button, since the label and its name are what is under
// test; the table itself is not.
const mountTable = () =>
  mount(MatchCollectionTable, {
    global: {
      plugins: [PrimeVue],
      directives: { tooltip: Tooltip },
      stubs: {
        // The table decides when its #empty slot shows (no rows, or none the
        // search left); the stub always renders it, so what the pane puts
        // in it is what these tests read.
        DataTable: { template: '<div class="datatable"><slot name="empty" /><slot /></div>' },
        Column: true
      }
    }
  })

const createButton = (wrapper) => wrapper.get('button[aria-label="Create target collection"]')

describe('the collection pane with no batch open', () => {
  beforeEach(() => {
    mocks.app = makeApp()
  })

  it('says what it is for and how it fills, in place of the table', () => {
    const wrapper = mountTable()

    expect(wrapper.find('.datatable').exists()).toBe(false)
    expect(wrapper.get('.empty-state .title').text()).toBe(
      'Select a batch to see its target collections'
    )
    expect(wrapper.get('.empty-state .hint').text()).toMatch(/sample browser/)
  })

  // A header that names the pane, where a breadcrumb would have no chain to
  // show; the panel falls back to its label only without a breadcrumb.
  it('is headed by its name instead of an empty breadcrumb', () => {
    const panel = mountTable().getComponent({ name: 'BaseTabbedPanel' })

    expect(panel.props('breadcrumb')).toBeNull()
    expect(panel.props('label')).toBe('Target collections')
    expect(panel.props('icon')).toBe('pi ph ph-crosshair')
  })

  it('shows the table once a batch is open', async () => {
    const wrapper = mountTable()

    mocks.app.data.batch.focused = BATCH
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.datatable').exists()).toBe(true)
    expect(wrapper.find('.empty-state .title').text()).not.toMatch(/Select a batch/)
    expect(
      wrapper.getComponent({ name: 'BaseTabbedPanel' }).props('breadcrumb').items.length
    ).toBeGreaterThan(0)
  })
})

describe('an open batch with no target collections', () => {
  it('names the empty list and offers to create the first collection', async () => {
    mocks.app = makeApp({ batch: BATCH })
    const empty = mountTable().get('.datatable .empty-state')

    expect(empty.get('.title').text()).toBe('No target collections yet')

    await empty.get('button').trigger('click')
    expect(mocks.menu.dialog.op).toBe('create')
  })

  // The first opening of a batch starts with [] while its collections load:
  // that is not "none yet".
  it('says nothing while the list loads', async () => {
    mocks.app = makeApp({ batch: BATCH })
    mocks.app.data.match.collection.pending = true
    const wrapper = mountTable()

    expect(wrapper.find('.datatable .empty-state').exists()).toBe(false)

    mocks.app.data.match.collection.pending = false
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.datatable .empty-state .title').text()).toBe('No target collections yet')
  })
})

describe('the Create target collection button', () => {
  it('shows its label while there is nothing to list', () => {
    mocks.app = makeApp()
    const button = createButton(mountTable())

    expect(button.classes()).not.toContain('hiddenlabel')
    expect(button.find('.p-button-label').text()).toBe('Create target collection')
  })

  it('shows its label above an open batch with no collections yet', () => {
    mocks.app = makeApp({ batch: BATCH })

    expect(createButton(mountTable()).classes()).not.toContain('hiddenlabel')
  })

  it('is icon-only beside a list with rows', () => {
    mocks.app = makeApp({ batch: BATCH, collections: [COLLECTION] })

    expect(createButton(mountTable()).classes()).toContain('hiddenlabel')
  })

  // The tooltip repeats the label, so it is bound only while the label is
  // hidden; PrimeVue's directive marks an element it is bound to.
  it('is explained in a tooltip only while icon-only', () => {
    mocks.app = makeApp()
    expect(createButton(mountTable()).attributes('data-pd-tooltip')).toBeUndefined()

    mocks.app = makeApp({ batch: BATCH, collections: [COLLECTION] })
    expect(createButton(mountTable()).attributes('data-pd-tooltip')).toBe('true')
  })

  it('is named for a screen reader in both states', () => {
    mocks.app = makeApp()
    expect(createButton(mountTable()).attributes('aria-label')).toBe('Create target collection')

    mocks.app = makeApp({ batch: BATCH, collections: [COLLECTION] })
    expect(createButton(mountTable()).attributes('aria-label')).toBe('Create target collection')
  })
})
