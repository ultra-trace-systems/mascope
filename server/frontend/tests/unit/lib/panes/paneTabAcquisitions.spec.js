import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive, toRefs } from 'vue'

// Raw files lists the sample files of the instrument chosen in the toolbar, or
// of every instrument the user can see when none is. Two things are under test
// here: that a row says which instrument its file was filed under, and the
// handover between Choose chemistry and the ionization settings, which are
// both modal and so cannot be open at once.

const mocks = vi.hoisted(() => ({
  app: null,
  uppy: null
}))

vi.mock('@/api', () => ({
  api: {
    http: { get: vi.fn(), post: vi.fn() },
    socket: { on: vi.fn(), addSubscription: vi.fn(), removeSubscription: vi.fn() }
  }
}))
vi.mock('@/stores', () => ({ useApp: () => mocks.app }))
vi.mock('@/lib/runtime', () => ({ runtime: { meta: {}, config: {}, version: null } }))
// The pane reads the store's own filters through storeToRefs; the stub store
// is a plain reactive object, which toRefs unwraps the same way. The rest of
// pinia is left alone - other modules in the import graph define real stores.
vi.mock('pinia', async (importOriginal) => ({
  ...(await importOriginal()),
  storeToRefs: (store) => toRefs(store)
}))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))
vi.mock('@uppy/vue', () => ({
  UppyContextProvider: { name: 'UppyContextProvider', template: '<div><slot /></div>' }
}))
vi.mock('@uppy/dashboard', () => ({ default: class Dashboard {} }))
vi.mock('@uppy/drop-target', () => ({ default: class DropTarget {} }))
vi.mock('@/lib/toolbars', () => ({
  InstrumentSelector: { name: 'InstrumentSelector', template: '<div />' }
}))

import PaneTabAcquisitions from '@/lib/panes/PaneTabAcquisitions.vue'

const file = (id, instrument, status = 'needs_chemistry') => ({
  sample_file_id: id,
  filename: `${instrument}_${id}.raw`,
  instrument,
  polarity: '-',
  datetime: '2026-09-23 10:00:00',
  processing_status: status
})

// PrimeVue's pieces are reduced to passthroughs that keep their props
// readable and still render their slots.
const passthrough = (name) => ({
  name,
  props: {
    // Typed, so that the bare `sortable` attribute arrives as a boolean
    // rather than as the empty string an untyped prop would keep.
    sortable: { type: Boolean, default: false },
    modelValue: null,
    visible: null,
    action: null,
    label: null,
    header: null,
    field: null,
    options: null,
    disabled: null,
    value: null,
    files: null,
    resume: { type: Boolean, default: false }
  },
  emits: ['update:modelValue', 'update:visible', 'update:action', 'click', 'configure', 'submit'],
  template: '<div><slot /><slot name="footer" /></div>'
})

const stubs = Object.fromEntries(
  [
    'Button',
    'Select',
    'Tag',
    'DatePicker',
    'DataTable',
    'Column',
    'IconField',
    'InputIcon',
    'InputText',
    'FloatLabel',
    'ContextMenu',
    'DialogSampleOp',
    'DialogBatchImport',
    'DialogFileUpload',
    'DialogIonizationOp',
    'DialogChooseChemistry'
  ].map((name) => [name, passthrough(name)])
)

function makeApp() {
  mocks.uppy = {
    use: vi.fn(function () {
      return this
    }),
    on: vi.fn(function () {
      return this
    }),
    getPlugin: vi.fn(() => null),
    removePlugin: vi.fn()
  }
  return reactive({
    data: {
      acquisition: {
        list: [file('a', 'Orbi-Lab1'), file('b', 'Tof-Lab2')],
        selected: [],
        focused: null,
        multiselected: false,
        search: '',
        polarity: '',
        first: 0,
        rows: 100,
        total: 2,
        sortField: 'datetime',
        sortOrder: -1,
        time: { mode: 'Last 24 hours', range: { min: null, max: null } },
        processingStatus: null,
        setSort: vi.fn(),
        setPage: vi.fn(),
        setTimeMode: vi.fn(),
        resetFilters: vi.fn(),
        unfocus: vi.fn(),
        setWatching: vi.fn()
      },
      batch: { focused: { sample_batch_id: 'sb-1' } },
      instrument: { focused: null, list: [] }
    },
    ui: { darkmode: { active: false } },
    uppy: {
      get: () => mocks.uppy,
      invalidFiles: [],
      addFromDialog: vi.fn()
    }
  })
}

const mountPane = (active = true) =>
  mount(PaneTabAcquisitions, { props: { active }, global: { stubs } })

const columns = (wrapper) => wrapper.findAllComponents({ name: 'Column' })
const chemistryDialog = (wrapper) => wrapper.findComponent({ name: 'DialogChooseChemistry' })
const ionizationDialog = (wrapper) => wrapper.findComponent({ name: 'DialogIonizationOp' })

describe('PaneTabAcquisitions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.app = makeApp()
  })

  describe('the instrument column', () => {
    // With no instrument chosen in the toolbar the list spans every
    // instrument the user can see, and the file name need not begin with the
    // one it was filed under - an upload reports its instrument, and the
    // stored name keeps whatever the file was called.
    it('says which instrument each file was filed under, and leads with it', () => {
      const wrapper = mountPane()

      const [first] = columns(wrapper)
      expect(first.props('field')).toBe('instrument')
      expect(first.props('header')).toBe('Instrument')
      // Ahead of the file name: with every instrument listed at once it is
      // what the rows group by, and the name no longer begins with it.
      expect(columns(wrapper).map((c) => c.props('field'))).toEqual([
        'instrument',
        'filename',
        'processing_status',
        'polarity',
        'datetime'
      ])
    })

    // The list is paginated server-side, so sorting has to be one the API
    // accepts: `instrument` is a SampleFileSortColumn, and the store passes
    // the field through unmapped.
    it('sorts on it', () => {
      const wrapper = mountPane()

      const instrument = columns(wrapper).find((c) => c.props('field') === 'instrument')
      expect(instrument.props('sortable')).toBe(true)
    })
  })

  describe('setting up a chemistry from Choose chemistry', () => {
    const openChemistry = async (wrapper) => {
      mocks.app.data.acquisition.selected = [file('a', 'Orbi-Lab1')]
      await chemistryDialog(wrapper).vm.$emit('update:visible', true)
      return wrapper
    }

    it('gives way to the ionization settings and comes back with them closed', async () => {
      const wrapper = await openChemistry(mountPane())

      await chemistryDialog(wrapper).vm.$emit('configure')
      expect(chemistryDialog(wrapper).props('visible')).toBe(false)
      expect(ionizationDialog(wrapper).props('visible')).toBe(true)

      await ionizationDialog(wrapper).vm.$emit('update:visible', false)
      expect(chemistryDialog(wrapper).props('visible')).toBe(true)
      // The same files, so the mode just added can be chosen for them.
      expect(chemistryDialog(wrapper).props('files')).toHaveLength(1)
    })

    // The toolbar opens the same settings, and closing them there must not
    // raise a dialog the user never asked for. Files are selected, so that
    // only having been asked can be what holds the dialog back.
    it('does not open it after the ionization settings were opened from the toolbar', async () => {
      const wrapper = mountPane()
      mocks.app.data.acquisition.selected = [file('a', 'Orbi-Lab1')]

      await ionizationDialog(wrapper).vm.$emit('update:visible', true)
      await ionizationDialog(wrapper).vm.$emit('update:visible', false)

      expect(chemistryDialog(wrapper).props('visible')).toBe(false)
    })

    // Between the two dialogs the files can be deleted, or the instrument
    // switched, either of which clears the selection.
    it('does not come back with nothing left to choose a chemistry for', async () => {
      const wrapper = await openChemistry(mountPane())
      await chemistryDialog(wrapper).vm.$emit('configure')

      mocks.app.data.acquisition.selected = []
      await ionizationDialog(wrapper).vm.$emit('update:visible', false)

      expect(chemistryDialog(wrapper).props('visible')).toBe(false)
    })
  })

  // The store holds a socket room per instrument shown and reloads on their
  // events. Raw files is one tab among several and the store outlives it, so
  // a tab parked elsewhere should pay none of that.
  describe('telling the store whether anyone is looking', () => {
    it('reports the tab it is on from the start', () => {
      mountPane(true)
      expect(mocks.app.data.acquisition.setWatching).toHaveBeenCalledWith(true)

      vi.clearAllMocks()
      mountPane(false)
      expect(mocks.app.data.acquisition.setWatching).toHaveBeenCalledWith(false)
    })

    it('reports leaving and returning', async () => {
      const wrapper = mountPane(true)

      await wrapper.setProps({ active: false })
      expect(mocks.app.data.acquisition.setWatching).toHaveBeenLastCalledWith(false)

      await wrapper.setProps({ active: true })
      expect(mocks.app.data.acquisition.setWatching).toHaveBeenLastCalledWith(true)
    })

    it('reports going away for good', () => {
      mountPane(true).unmount()

      expect(mocks.app.data.acquisition.setWatching).toHaveBeenLastCalledWith(false)
    })
  })

  describe('what Choose chemistry is asked about', () => {
    // A row is replaced in the list when its status changes; the selection
    // holds the copy taken when it was picked, which the dialog would
    // otherwise judge a stale status by.
    it('hands it the rows as the list has them now', () => {
      const chosen = file('a', 'Orbi-1', 'needs_chemistry')
      mocks.app.data.acquisition.selected = [chosen]
      mocks.app.data.acquisition.list = [{ ...chosen, processing_status: 'done' }]

      const wrapper = mountPane()

      expect(chemistryDialog(wrapper).props('files')[0].processing_status).toBe('done')
    })

    // Nothing stops the files being processed, or given a chemistry by
    // someone else, while the ionization settings are open.
    it('does not come back for files the server would now refuse', async () => {
      const chosen = file('a', 'Orbi-1', 'needs_chemistry')
      mocks.app.data.acquisition.selected = [chosen]
      mocks.app.data.acquisition.list = [chosen]
      const wrapper = mountPane()
      await chemistryDialog(wrapper).vm.$emit('update:visible', true)
      await chemistryDialog(wrapper).vm.$emit('configure')

      // Picked up for processing while the settings were open.
      mocks.app.data.acquisition.list = [
        {
          ...chosen,
          processing_status: 'converted',
          processing_updated_utc: new Date().toISOString()
        }
      ]
      await ionizationDialog(wrapper).vm.$emit('update:visible', false)

      expect(chemistryDialog(wrapper).props('visible')).toBe(false)
    })

    // Whether an open is the return from the settings is the pane's to say.
    // A visit that ends without one must leave nothing behind: the next open
    // is for whatever is selected by then, and starts empty.
    it('says an open is a resume only when it is the one it reopened', async () => {
      const chosen = file('a', 'Orbi-Lab1', 'needs_chemistry')
      mocks.app.data.acquisition.selected = [chosen]
      mocks.app.data.acquisition.list = [chosen]
      const wrapper = mountPane()
      await chemistryDialog(wrapper).vm.$emit('update:visible', true)
      expect(chemistryDialog(wrapper).props('resume')).toBe(false)

      await chemistryDialog(wrapper).vm.$emit('configure')
      await ionizationDialog(wrapper).vm.$emit('update:visible', false)
      expect(chemistryDialog(wrapper).props('resume')).toBe(true)

      // Processed, or cancelled - either way that dialog is done with.
      await chemistryDialog(wrapper).vm.$emit('update:visible', false)
      await chemistryDialog(wrapper).vm.$emit('update:visible', true)
      expect(chemistryDialog(wrapper).props('resume')).toBe(false)
    })

    it('says nothing about a resume when it decided not to come back', async () => {
      const chosen = file('a', 'Orbi-Lab1', 'needs_chemistry')
      mocks.app.data.acquisition.selected = [chosen]
      mocks.app.data.acquisition.list = [chosen]
      const wrapper = mountPane()
      await chemistryDialog(wrapper).vm.$emit('update:visible', true)
      await chemistryDialog(wrapper).vm.$emit('configure')

      // Taken up for processing while the settings were open, so no return.
      mocks.app.data.acquisition.list = [
        {
          ...chosen,
          processing_status: 'converted',
          processing_updated_utc: new Date().toISOString()
        }
      ]
      await ionizationDialog(wrapper).vm.$emit('update:visible', false)
      expect(chemistryDialog(wrapper).props('visible')).toBe(false)

      // Later, for different files that can be bound.
      mocks.app.data.acquisition.list = [chosen]
      await chemistryDialog(wrapper).vm.$emit('update:visible', true)

      expect(chemistryDialog(wrapper).props('resume')).toBe(false)
    })
  })

  // The list is narrowed to a time range, and on a server whose files are
  // older than the range it opened empty, saying only that no files matched
  // the filters. The empty state now names the range and offers the way out,
  // and All time is a range the selector offers at all.
  describe('an empty list', () => {
    const buttons = (wrapper) => wrapper.findAllComponents({ name: 'Button' })
    const button = (wrapper, label) => buttons(wrapper).find((b) => b.props('label') === label)
    const labels = (wrapper) =>
      buttons(wrapper)
        .map((b) => b.props('label'))
        .filter((label) => label?.startsWith('Show') || label === 'Clear filters')
    // The stub passes `inputId` through to its root, where the DOM keeps it
    // lowercased.
    const timeSelect = (wrapper) =>
      wrapper.findAllComponents({ name: 'Select' }).find((s) => s.attributes('inputid') === 'time')

    it('offers All time among the ranges', () => {
      const wrapper = mountPane()

      expect(timeSelect(wrapper).props('options')).toEqual([
        'Last 24 hours',
        'Last 7 days',
        'Last 30 days',
        'Last 90 days',
        'All time'
      ])
    })

    it('names the range in force and offers the next one up, or all time', () => {
      mocks.app.data.acquisition.list = []
      const wrapper = mountPane()

      expect(wrapper.text()).toContain('No raw files in the last 24 hours')
      expect(labels(wrapper)).toEqual(['Show last 7 days', 'Show all time'])
    })

    it('widens the range in one click', async () => {
      mocks.app.data.acquisition.list = []
      const wrapper = mountPane()

      await button(wrapper, 'Show last 7 days').vm.$emit('click')
      expect(mocks.app.data.acquisition.setTimeMode).toHaveBeenLastCalledWith('Last 7 days')

      await button(wrapper, 'Show all time').vm.$emit('click')
      expect(mocks.app.data.acquisition.setTimeMode).toHaveBeenLastCalledWith('All time')
    })

    it('follows the range as it widens', async () => {
      mocks.app.data.acquisition.list = []
      const wrapper = mountPane()

      mocks.app.data.acquisition.time.mode = 'Last 90 days'
      await wrapper.vm.$nextTick()

      expect(wrapper.text()).toContain('No raw files in the last 90 days')
      expect(labels(wrapper)).toEqual(['Show all time'])
    })

    // A date range has no next preset; all time is its one step out.
    it('offers all time alone from an explicit date range', () => {
      mocks.app.data.acquisition.list = []
      mocks.app.data.acquisition.time.mode = 'range'
      const wrapper = mountPane()

      expect(wrapper.text()).toContain('No raw files in the selected date range')
      expect(labels(wrapper)).toEqual(['Show all time'])
    })

    it('has nothing to widen to from all time', () => {
      mocks.app.data.acquisition.list = []
      mocks.app.data.acquisition.time.mode = 'All time'
      const wrapper = mountPane()

      expect(wrapper.text()).toContain('No raw files yet')
      expect(labels(wrapper)).toEqual([])
    })

    it('names the instrument when one is chosen and has no files at all', () => {
      mocks.app.data.acquisition.list = []
      mocks.app.data.acquisition.time.mode = 'All time'
      mocks.app.data.instrument.focused = { instrument: 'Orbi-Lab1' }
      const wrapper = mountPane()

      expect(wrapper.text()).toContain('No raw files from this instrument')
    })

    // The page filters narrow the page on screen: files were found, and the
    // range is not what hid them.
    it('blames the filters when they are what emptied it', async () => {
      mocks.app.data.acquisition.search = 'nothing-is-called-this'
      const wrapper = mountPane()

      expect(wrapper.text()).toContain(
        'No raw files match the current filters in the last 24 hours'
      )
      expect(labels(wrapper)).toContain('Clear filters')
    })

    it('blames a status filter too', () => {
      mocks.app.data.acquisition.list = []
      mocks.app.data.acquisition.processingStatus = ['failed']
      const wrapper = mountPane()

      expect(wrapper.text()).toContain(
        'No raw files match the current filters in the last 24 hours'
      )
      expect(labels(wrapper)).toContain('Clear filters')
    })

    // Unlike the toolbar's clear, which also puts the time range back: a range
    // the user just widened to is kept.
    it('clears the filters but keeps the range', async () => {
      mocks.app.data.acquisition.list = []
      mocks.app.data.acquisition.time.mode = 'All time'
      mocks.app.data.acquisition.search = 'x'
      mocks.app.data.acquisition.polarity = '+'
      mocks.app.data.acquisition.processingStatus = ['failed']
      const wrapper = mountPane()

      await button(wrapper, 'Clear filters').vm.$emit('click')

      expect(mocks.app.data.acquisition.search).toBe('')
      expect(mocks.app.data.acquisition.polarity).toBe('')
      expect(mocks.app.data.acquisition.processingStatus).toBeNull()
      expect(mocks.app.data.acquisition.time.mode).toBe('All time')
      expect(mocks.app.data.acquisition.resetFilters).not.toHaveBeenCalled()
    })
  })
})
