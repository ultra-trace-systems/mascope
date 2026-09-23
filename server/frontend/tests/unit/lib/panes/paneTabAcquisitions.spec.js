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
    files: null
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
        resetFilters: vi.fn(),
        unfocus: vi.fn()
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

const mountPane = () => mount(PaneTabAcquisitions, { props: { active: true }, global: { stubs } })

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
    it('says which instrument each file was filed under', () => {
      const wrapper = mountPane()

      const instrument = columns(wrapper).find((c) => c.props('field') === 'instrument')
      expect(instrument).toBeDefined()
      expect(instrument.props('header')).toBe('Instrument')
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
})
