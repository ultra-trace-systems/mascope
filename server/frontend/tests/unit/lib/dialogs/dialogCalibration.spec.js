import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

import PrimeVue from 'primevue/config'

// A stitched sample is calibrated scan range by scan range, and the fit the
// dialog previews says how each range came by its calibration. The dialog
// lists the ranges above the calibrants, and the calibrants say which range
// each was found in. A sample that is not stitched shows neither.

const mocks = vi.hoisted(() => ({ fit: null }))

vi.mock('@/api', () => ({ api: { http: { get: vi.fn(), post: vi.fn() } } }))
vi.mock('@/stores', () => ({
  useApp: () => ({
    data: { sample: { focused: null }, workspace: { list: [] } },
    auth: { user: null },
    ui: { help: { set: vi.fn() } }
  })
}))
vi.mock('@/lib/mzFit', () => ({ useMzFit: () => mocks.fit }))
vi.mock('@/lib/panes', () => ({
  PaneSettingsCalibration: { name: 'PaneSettingsCalibration', template: '<div />' }
}))
vi.mock('@/lib/permissions', () => ({ canCalibrateInstruments: () => true }))
vi.mock('primevue/useconfirm', () => ({ useConfirm: () => ({ require: vi.fn() }) }))

import DialogCalibration from '@/lib/dialogs/DialogCalibration.vue'

// Only the dialog's own logic is under test; PrimeVue's pieces are reduced to
// passthroughs that still render their slots and keep what they are given.
const passthrough = (name) => ({
  name,
  props: ['visible', 'header', 'field', 'label', 'value', 'severity', 'disabled'],
  template: '<div><slot /></div>'
})
// The table hands its rows to its columns, and a column renders its cell for
// each: enough of a table to read what the dialog puts in one.
const DataTable = {
  name: 'DataTable',
  props: ['value'],
  provide() {
    return { rows: () => this.value }
  },
  template: '<div><slot /></div>'
}
const Column = {
  name: 'Column',
  props: ['field', 'header'],
  inject: ['rows'],
  template:
    '<div><span v-for="(row, index) in rows()" :key="index"><slot name="body" :data="row" /></span></div>'
}
const stubs = {
  ...Object.fromEntries(
    ['Dialog', 'Message', 'Button', 'Listbox', 'ProgressSpinner'].map((name) => [
      name,
      passthrough(name)
    ])
  ),
  DataTable,
  Column
}

const SAMPLE = { sample_item_id: 'si-1', sample_item_name: 'composite', instrument: 'Orbi' }

const SEGMENTS = [
  {
    key: 'FTMS - p NSI Full ms [40.0000-138.0000] R=120000',
    label: 'm/z 40-138',
    source: 'anchors',
    origin: null,
    calibration_factor: 1 - 11.934e-6,
    quality: { n_points: 2 }
  },
  {
    key: 'FTMS - p NSI Full ms [66.0000-124.0000] R=120000',
    label: 'm/z 66-124',
    source: 'overlap',
    origin: 'm/z 40-138',
    calibration_factor: 1 - 11.517e-6,
    shared_ions: 26,
    quality: null
  },
  {
    key: 'FTMS - p NSI Full ms [132.0000-460.0000] R=120000',
    label: 'm/z 132-460',
    source: 'borrowed',
    origin: 'm/z 40-138',
    calibration_factor: 1 - 11.934e-6,
    quality: null
  }
]

const point = (extra = {}) => ({
  mz: 62.9854,
  sample_peak_mz: 62.9861,
  match_mz_error: 11.3,
  calibration_mz: 62.9854,
  calibration_mz_error: -0.6,
  mz_error_diff: -10.7,
  calibrant_to_tic: 0.01,
  ...extra
})
const SUMMARY = { match_mz_error: 11.3, calibration_mz_error: 0.6, calibrant_to_tic: 0.01 }

const fitted = (quality, stats) =>
  reactive({
    current: { mode: 'one-point', quality, quality_issues: [], quality_gate: 'warn' },
    stats,
    status: 'success',
    error: null,
    mzCalibrationParams: {},
    affectedBatches: [],
    affectedSamples: [],
    compute: vi.fn(),
    loadInstrumentDefaults: vi.fn()
  })

const mountDialog = () =>
  mount(DialogCalibration, {
    props: { visible: true, context: SAMPLE },
    global: { plugins: [PrimeVue], stubs }
  })

const ranges = (wrapper) => wrapper.findAll('[data-testid="calibration-segments"] li')
const headers = (wrapper) =>
  wrapper.findAllComponents({ name: 'Column' }).map((column) => column.props('header'))

describe('DialogCalibration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('lists the scan ranges of a stitched sample with how each was calibrated', () => {
    mocks.fit = fitted({ n_points: 2, segments: SEGMENTS }, [
      point({ segment: 0, segment_label: 'm/z 40-138' }),
      SUMMARY
    ])

    const listed = ranges(mountDialog())

    expect(listed.map((item) => item.text())).toEqual([
      'm/z 40-138: fitted on 2 calibrants (-11.93 ppm).',
      'm/z 66-124: holds no calibrant, so it takes the calibration of m/z 40-138 ' +
        'across the 26 ions both ranges measure (-11.52 ppm).',
      'm/z 132-460: holds no calibrant and shares too few ions with a neighbouring ' +
        'range, so it takes the calibration of m/z 40-138 as it is (-11.93 ppm).'
    ])
    // The ranges that run on another range's calibration stand out
    expect(listed.map((item) => item.classes('carried'))).toEqual([false, true, true])
  })

  it('says which scan range each calibrant was found in', () => {
    mocks.fit = fitted({ n_points: 2, segments: SEGMENTS }, [
      point({ segment: 0, segment_label: 'm/z 40-138' }),
      SUMMARY
    ])

    const wrapper = mountDialog()

    expect(headers(wrapper)[0]).toBe('Scan range')
    expect(headers(wrapper)).toHaveLength(8)
    // The range is shown by its name, and the summary row has none
    const table = wrapper.findComponent({ name: 'DataTable' }).text()
    expect(table).toContain('m/z 40-138')
    expect(table).not.toContain('NaN')
  })

  it('shows a sample that is not stitched as before', () => {
    mocks.fit = fitted({ n_points: 1 }, [point(), SUMMARY])

    const wrapper = mountDialog()

    expect(wrapper.find('[data-testid="calibration-segments"]').exists()).toBe(false)
    expect(headers(wrapper)).toHaveLength(7)
    expect(headers(wrapper)[0]).toBe('Isotope m/z')
  })

  it('lists nothing while there is no fit', () => {
    mocks.fit = fitted(null, null)
    mocks.fit.current = null

    const wrapper = mountDialog()

    expect(wrapper.find('[data-testid="calibration-segments"]').exists()).toBe(false)
  })
})
