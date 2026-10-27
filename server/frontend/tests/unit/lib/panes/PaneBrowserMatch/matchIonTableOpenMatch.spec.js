import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive, inject, provide, h } from 'vue'

// Three ways open an ion's match, all through the ion context menu store's
// openMatch: its "Open match" entry, Enter on a focused row, and the row's
// expander button, whose tooltip says the same. A click on a row stays a
// selection, which the Batch chart reads; so does Space.

const mocks = vi.hoisted(() => ({ app: null, menu: null }))

vi.mock('@/stores', () => ({ useApp: () => mocks.app }))
vi.mock('@/lib/panes/PaneBrowserMatch/stores', () => ({
  useCollectionContextMenu: () => ({ entries: { value: [] }, onClick: vi.fn() }),
  useIonContextMenu: () => mocks.menu,
  useIonScroller: () => ({ bind: vi.fn(), scrollToIon: vi.fn() }),
  useIonTableCustomizer: () => ({ config: { columns: [] } })
}))
vi.mock('@/lib/panes/PaneBrowserSample/stores', () => ({
  useSampleScroller: () => ({ scrollToSample: vi.fn() })
}))
vi.mock('@/lib/base', () => ({
  BaseTabbedPanel: { template: '<div class="panel"><slot name="menu" /><slot /></div>' },
  BaseMatchTag: true,
  BaseCopyableField: true
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

const { default: MatchIonTable } = await import('@/lib/panes/PaneBrowserMatch/MatchIonTable.vue')

// The ion list as stored, and in the order the table shows it (sorted by
// score): the row's data-p-index is its place in the shown rows, not the list.
const IONS = [
  { target_ion_id: 'ti-low', match: { match_score: 0.1, sample_item_id: 'si-1' } },
  { target_ion_id: 'ti-high', match: { match_score: 0.9, sample_item_id: 'si-1' } }
]
const SHOWN = [IONS[1], IONS[0]]

// Stands in for PrimeVue's DataTable: rows as PrimeVue renders them (a
// focusable tr carrying data-p-index), its processedData, and its own Enter on
// a row, which selects that row.
const DataTableStub = {
  name: 'DataTable',
  props: ['value'],
  emits: ['update:selection'],
  setup(props, { slots, emit }) {
    provide('rows', SHOWN)
    const rowKeydown = (row) => (event) => {
      if (event.code === 'Enter' || event.code === 'Space') emit('update:selection', [row])
    }
    return () =>
      h('div', { class: 'datatable' }, [
        h('div', slots.default?.()),
        h(
          'table',
          h(
            'tbody',
            SHOWN.map((row, index) =>
              h('tr', { 'data-p-index': index, tabindex: -1, onKeydown: rowKeydown(row) }, [
                h('td', h('input', { class: 'inside' }))
              ])
            )
          )
        )
      ])
  },
  computed: { processedData: () => SHOWN }
}

// Renders the expander column's body once per shown row.
const ColumnStub = {
  name: 'Column',
  props: ['expander'],
  setup(props, { slots }) {
    const rows = inject('rows')
    return () =>
      props.expander === '' || props.expander === true
        ? h(
            'div',
            rows.map((data) => h('div', { class: 'expander' }, slots.body?.({ data })))
          )
        : null
  }
}

const tooltip = {
  mounted: (el, { value }) => (el.dataset.tooltip = value ?? ''),
  updated: (el, { value }) => (el.dataset.tooltip = value ?? '')
}

function makeApp({ sample = true } = {}) {
  return reactive({
    data: {
      batch: { focused: { sample_batch_name: 'Batch 1' } },
      sample: {
        focused: sample ? { sample_item_id: 'si-1', sample_item_name: 'Sample 1' } : null,
        focusedId: sample ? 'si-1' : null,
        focus: vi.fn(),
        unfocus: vi.fn()
      },
      ionization: { mechanism: { list: [], focused: null, focus: vi.fn(), unfocus: vi.fn() } },
      match: {
        collection: { focused: null, focusedId: 'tc-1', unfocus: vi.fn() },
        ion: { list: IONS, selected: [], pending: false, error: null, load: vi.fn() },
        visualized: { ion: null, set: vi.fn(), clear: vi.fn() }
      }
    },
    ui: {
      tab: { active: 'batch' },
      help: { right: () => ({}), docUrl: () => '' }
    }
  })
}

let wrapper

const mountTable = (options) => {
  mocks.app = makeApp(options)
  mocks.menu = reactive({ selection: null, openMatch: vi.fn(), onClick: vi.fn(), clear: vi.fn() })
  wrapper = mount(MatchIonTable, {
    attachTo: document.body,
    global: {
      stubs: {
        DataTable: DataTableStub,
        Column: ColumnStub,
        Button: { template: '<button class="expander-button" />' },
        InputText: true,
        Select: true
      },
      directives: { tooltip }
    }
  })
  return wrapper
}

const press = (element, code) => {
  const event = new KeyboardEvent('keydown', { code, bubbles: true, cancelable: true })
  element.dispatchEvent(event)
  return event
}
const rows = () => [...wrapper.element.querySelectorAll('tr[data-p-index]')]
const selections = () => wrapper.findComponent(DataTableStub).emitted('update:selection') ?? []

beforeEach(() => vi.clearAllMocks())
afterEach(() => wrapper?.unmount())

describe('Enter on a focused ion row', () => {
  it.each(['Enter', 'NumpadEnter'])('%s opens the match of the row shown there', (code) => {
    mountTable()

    const event = press(rows()[1], code)

    expect(mocks.menu.openMatch).toHaveBeenCalledWith(IONS[0])
    expect(event.defaultPrevented).toBe(true)
  })

  // PrimeVue's own Enter would select the row, and a changed selection closes
  // the match that was just opened.
  it('does not also select the row', () => {
    mountTable()

    press(rows()[0], 'Enter')

    expect(mocks.menu.openMatch).toHaveBeenCalledWith(IONS[1])
    expect(selections()).toEqual([])
  })

  it('leaves Space to select the row', () => {
    mountTable()

    press(rows()[0], 'Space')

    expect(mocks.menu.openMatch).not.toHaveBeenCalled()
    expect(selections()).toEqual([[[IONS[1]]]])
  })

  it('leaves Enter in a control inside the row alone', () => {
    mountTable()

    press(rows()[0].querySelector('input'), 'Enter')

    expect(mocks.menu.openMatch).not.toHaveBeenCalled()
  })

  it('leaves Enter to the table without a focused sample', () => {
    mountTable({ sample: false })

    press(rows()[0], 'Enter')

    expect(mocks.menu.openMatch).not.toHaveBeenCalled()
    expect(selections()).toEqual([[[IONS[1]]]])
  })
})

describe('the expander button', () => {
  const buttons = () => wrapper.findAll('.expander-button')

  it('says "Open match" with a sample focused', () => {
    mountTable()

    expect(buttons().map((button) => button.attributes('data-tooltip'))).toEqual([
      'Open match',
      'Open match'
    ])
  })

  it('opens the match of its row', async () => {
    mountTable()

    await buttons()[1].trigger('click')

    expect(mocks.menu.openMatch).toHaveBeenCalledWith(IONS[0])
  })

  it('says "Close match" once its match is open', async () => {
    mountTable()

    await buttons()[0].trigger('click')

    expect(buttons()[0].attributes('data-tooltip')).toBe('Close match')
    expect(buttons()[1].attributes('data-tooltip')).toBe('Open match')
  })

  it('selects the best-matching sample without a focused sample', async () => {
    mountTable({ sample: false })

    expect(buttons()[0].attributes('data-tooltip')).toBe(
      'Click to select sample with the best match'
    )
    await buttons()[0].trigger('click')

    expect(mocks.menu.openMatch).not.toHaveBeenCalled()
    expect(mocks.app.data.sample.focus).toHaveBeenCalledWith({ sample_item_id: 'si-1' })
  })
})
