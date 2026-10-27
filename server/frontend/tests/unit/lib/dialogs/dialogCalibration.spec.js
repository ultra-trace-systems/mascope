import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'

import PrimeVue from 'primevue/config'

// A stitched sample is calibrated scan range by scan range, and the fit the
// dialog previews says how each range came by its calibration. The dialog
// lists the ranges above the calibrants, and the calibrants say which range
// each was found in. The record the sample carries lists its ranges too, and
// where they read otherwise than the fit's - an applied fit made at other
// settings - the dialog lists both. A sample that is not stitched shows none.

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

import { api } from '@/api'
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
    note: 'No calibration peaks found',
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

const fitted = (quality, stats) => {
  const fit = reactive({
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
  // What `useMzFit`'s does: the fit and what came with it are let go
  fit.unload = vi.fn(() => {
    Object.assign(fit, { status: null, current: null, error: null, stats: null })
  })
  return fit
}
// A fit as it arrives for a sample, once the dialog has asked for it
const arrives = (fit, quality, stats) =>
  Object.assign(fit, {
    current: { mode: 'one-point', quality, quality_issues: [], quality_gate: 'warn' },
    stats,
    status: 'success'
  })
// Defaults that are still being fetched, so that the refit has not started
const fetching = () => new Promise(() => {})

const mountDialog = (context = SAMPLE) =>
  mount(DialogCalibration, {
    props: { visible: true, context },
    global: { plugins: [PrimeVue], stubs }
  })

// The record a sample carries once a fit is applied
const record = (segments) => ({ status: 'ok', verified: true, quality: { n_points: 2, segments } })

const ranges = (wrapper) => wrapper.findAll('[data-testid="calibration-segments"] li')
const lists = (wrapper) =>
  wrapper.findAll('[data-testid="calibration-segments"] ul').map((list) => ({
    heading: list.element.previousElementSibling?.classList.contains('segment-heading')
      ? list.element.previousElementSibling.textContent
      : null,
    ranges: list.findAll('li').map((item) => item.text())
  }))
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

    const wrapper = mountDialog()
    const listed = ranges(wrapper)

    expect(listed.map((item) => item.text())).toEqual([
      'm/z 40-138: fitted on 2 calibrants (-11.93 ppm).',
      'm/z 66-124: not fitted on calibrants of its own (no calibration peaks found); ' +
        'takes the calibration of m/z 40-138 across the 26 ions both ranges measure (-11.52 ppm).',
      'm/z 132-460: not fitted on calibrants of its own; takes the calibration of ' +
        'm/z 40-138 unchanged (-11.93 ppm).'
    ])
    // The range that took another's calibration unchanged stands out; the one
    // that took it across an overlap was checked against the ions it measures
    expect(listed.map((item) => item.classes('flagged'))).toEqual([false, false, true])
    // A sample with no record of its ranges shows the fit's alone, unheaded
    expect(lists(wrapper).map((list) => list.heading)).toEqual([null])
  })

  it('lists the ranges once where the fit repeats the one applied', () => {
    mocks.fit = fitted({ n_points: 2, segments: SEGMENTS }, [point(), SUMMARY])

    const wrapper = mountDialog({ ...SAMPLE, mz_calibration: record(SEGMENTS) })

    expect(lists(wrapper)).toHaveLength(1)
    expect(lists(wrapper)[0].heading).toBe(null)
    expect(lists(wrapper)[0].ranges).toHaveLength(3)
  })

  it('lists the ranges applied beside those of a fit that reads them otherwise', () => {
    // The pipeline fitted the reagent scan at a wider tolerance than the
    // dialog fits at, and found more calibrants there
    const applied = [{ ...SEGMENTS[0], quality: { n_points: 5 } }, ...SEGMENTS.slice(1)]
    mocks.fit = fitted({ n_points: 2, segments: SEGMENTS }, [point(), SUMMARY])

    const shown = lists(mountDialog({ ...SAMPLE, mz_calibration: record(applied) }))

    expect(shown.map((list) => list.heading)).toEqual(['Applied:', 'This fit:'])
    expect(shown[0].ranges[0]).toBe('m/z 40-138: fitted on 5 calibrants (-11.93 ppm).')
    expect(shown[1].ranges[0]).toBe('m/z 40-138: fitted on 2 calibrants (-11.93 ppm).')
  })

  it('lists the ranges applied where the fit here finds none', () => {
    // No range found calibrants at the dialog's settings: the fit ends in a
    // warning and lists no ranges, while the sample runs on a fit made at
    // wider ones, whose carried ranges its badge names
    mocks.fit = fitted({ n_points: 0 }, null)
    mocks.fit.status = 'warning'
    mocks.fit.error = 'No calibration peaks found'

    const shown = lists(mountDialog({ ...SAMPLE, mz_calibration: record(SEGMENTS) }))

    expect(shown).toHaveLength(1)
    expect(shown[0].heading).toBe('Applied:')
    expect(shown[0].ranges[1]).toContain('m/z 66-124: not fitted on calibrants of its own')
  })

  it('lists the ranges of the sample picked in a batch beside the batch list', async () => {
    const picked = { ...SAMPLE, mz_calibration: record(SEGMENTS) }
    api.http.get.mockResolvedValueOnce([picked])
    mocks.fit = fitted(null, null)
    mocks.fit.current = null

    const wrapper = mountDialog({ sample_batch_id: 'sb-1', sample_batch_name: 'composite' })
    await flushPromises()

    // In the column of the picked sample's results, not above the batch list
    expect(wrapper.find('.table-wrapper [data-testid="calibration-segments"]').exists()).toBe(true)
    expect(wrapper.find('.list-wrapper').exists()).toBe(true)
    // While the fit is under way, the ranges the sample runs on
    expect(lists(wrapper).map((list) => list.heading)).toEqual(['Applied:'])
    expect(lists(wrapper)[0].ranges).toHaveLength(3)
  })

  it('says which scan range each calibrant was found in', () => {
    mocks.fit = fitted({ n_points: 2, segments: SEGMENTS }, [
      point({ segment: 0, segment_label: 'm/z 40-138' }),
      SUMMARY
    ])

    const wrapper = mountDialog()

    expect(headers(wrapper)[0]).toBe('Scan range [m/z]')
    expect(headers(wrapper)).toHaveLength(8)
    // The range is shown by its name, and the summary row has none
    const table = wrapper.findComponent({ name: 'DataTable' }).text()
    // without the unit the header carries, on one line
    expect(table).toContain('40-138')
    expect(table).not.toContain('m/z 40-138')
    expect(wrapper.find('.range-cell').text()).toBe('40-138')
    expect(table).not.toContain('NaN')
  })

  it('names a scan range below the quality bar once, on its own line', () => {
    mocks.fit = fitted({ n_points: 2, segments: SEGMENTS }, [point(), SUMMARY])
    mocks.fit.current.quality_issues = [
      {
        code: 'points',
        message:
          'm/z 40-138: Fitted on 1 calibration point but moves the m/z axis 11.93 ppm from ' +
          'the acquisition axis; a fit nothing corroborates is trusted with a correction of ' +
          'at most 10 ppm.',
        segment: SEGMENTS[0].key
      },
      { code: 'residual', message: 'Mean m/z error after calibration is 1.20 ppm (limit 1 ppm).' }
    ]

    const wrapper = mountDialog()
    const warning = wrapper.find('[data-testid="calibration-quality-issues"]').text()
    const listed = ranges(wrapper)

    // The range's reason is its line, named once, and the line stands out
    expect(listed[0].text()).toBe(
      'm/z 40-138: fitted on 1 calibration point but moves the m/z axis 11.93 ppm from the ' +
        'acquisition axis; a fit nothing corroborates is trusted with a correction of at most ' +
        '10 ppm.'
    )
    expect(listed.map((item) => item.classes('flagged'))).toEqual([true, false, true])
    // The warning keeps what is the fit's own, and points to the list
    expect(warning).toContain('Mean m/z error after calibration is 1.20 ppm (limit 1 ppm).')
    expect(warning).toContain('One scan range misses it, marked below.')
    expect(warning).not.toContain('m/z 40-138')
  })

  it('folds the settings, named in one line, until they are asked for', async () => {
    mocks.fit = fitted({ n_points: 1 }, [point(), SUMMARY])
    mocks.fit.mzCalibrationParams = { match_score_min: 0, refine_window: 50 }

    const wrapper = mountDialog()
    const toggle = wrapper.find('[data-testid="calibration-settings-toggle"]')
    const pane = () => wrapper.findComponent({ name: 'PaneSettingsCalibration' })
    const summary = () => wrapper.find('[data-testid="calibration-settings-summary"]')

    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(pane().element.style.display).toBe('none')
    expect(summary().text()).toContain('Min. match score 0 · Refine window 50 ppm')

    await toggle.trigger('click')

    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(pane().element.style.display).toBe('')
    expect(summary().exists()).toBe(false)
  })

  it('shows a sample that is not stitched as before', () => {
    mocks.fit = fitted({ n_points: 1 }, [point(), SUMMARY])

    const wrapper = mountDialog()

    expect(wrapper.find('[data-testid="calibration-segments"]').exists()).toBe(false)
    expect(headers(wrapper)).toHaveLength(7)
    expect(headers(wrapper)[0]).toBe('Isotope m/z')
  })

  it('lists every line a TOF fit chose from, the ones it left out included', () => {
    // The fit kept two lines and left the third out; its own rows are the
    // two it kept and its summary
    const points = [
      {
        ion: 'NO3-',
        target_mz: 61.98837,
        observed_mz: 61.98394,
        residual_ppm: 0.4,
        snr: 310.2,
        retained: true
      },
      {
        ion: 'HN2O6-',
        target_mz: 124.9887,
        observed_mz: 124.97953,
        residual_ppm: -0.3,
        snr: 64.0,
        retained: true
      },
      {
        ion: 'H2N3O9-',
        target_mz: 187.98402,
        observed_mz: 187.97716,
        residual_ppm: 38.6,
        snr: 21.4,
        retained: false
      }
    ]
    mocks.fit = fitted({ n_points: 2, points, span: [61.98837, 124.9887] }, [
      point({ mz: 61.98837, sample_peak_mz: 61.98394, calibrant_to_tic: 0.0712 }),
      point({ mz: 124.9887, sample_peak_mz: 124.97953, calibrant_to_tic: 0.0034 }),
      SUMMARY
    ])

    const wrapper = mountDialog()
    const rows = wrapper.findComponent({ name: 'DataTable' }).props('value')
    const column = (header) =>
      wrapper
        .findAllComponents({ name: 'Column' })
        .find((found) => found.props('header') === header)
        .findAll('span > span')
        .map((cell) => cell.text())

    expect(headers(wrapper)).toEqual([
      'Ion',
      'Isotope m/z',
      'Observed m/z',
      'Pre-calibration error [ppm]',
      'Calibrated m/z',
      'Post-calibration error [ppm]',
      'Error Δ [ppm]',
      'S/N',
      'Fraction of TIC',
      'In fit'
    ])
    expect(rows.map((row) => row.type)).toEqual(['point', 'point', 'point', 'summary'])
    expect(column('Ion')).toEqual(['NO3-', 'HN2O6-', 'H2N3O9-', '-'])
    // An ion's formula stays on one line
    expect(wrapper.findAll('.ion-cell').map((cell) => cell.text())).toEqual([
      'NO3-',
      'HN2O6-',
      'H2N3O9-'
    ])
    expect(column('Post-calibration error [ppm]')).toEqual(['0.40', '-0.30', '38.60', '0.60'])
    expect(column('S/N')).toEqual(['310', '64', '21', '-'])
    expect(column('In fit')).toEqual(['yes', 'yes', 'left out', '-'])
    // A kept line's share of the TIC is the fit's own row's, and a line left
    // out has none
    expect(column('Fraction of TIC')).toEqual(['7.12%', '0.34%', '-', '1.00%'])
    // The line left out reads as such, every cell of it
    expect(wrapper.findAll('.left-out-cell').map((cell) => cell.text())).toContain('H2N3O9-')
    expect(wrapper.findAll('.left-out-cell')).toHaveLength(9)
    expect(wrapper.find('.summary-cell').exists()).toBe(true)
    expect(wrapper.findComponent({ name: 'DataTable' }).text()).not.toContain('NaN')
  })

  it('shows a fit that lists no points by the rows it sent, as before', () => {
    // An Orbitrap fit, or a TOF fit from a server older than the points
    mocks.fit = fitted({ n_points: 2 }, [point(), point({ mz: 80.0 }), SUMMARY])

    const wrapper = mountDialog()
    const rows = wrapper.findComponent({ name: 'DataTable' }).props('value')

    expect(headers(wrapper)).not.toContain('Ion')
    expect(headers(wrapper)).not.toContain('In fit')
    expect(rows.map((row) => row.type)).toEqual(['stat', 'stat', 'summary'])
    expect(wrapper.findAll('.summary-cell').length).toBeGreaterThan(0)
    expect(wrapper.find('.left-out-cell').exists()).toBe(false)
  })

  it('waits on a fit under way, and on none that warned before it had calibrants', () => {
    mocks.fit = fitted(null, null)
    mocks.fit.current = null
    const shown = () => {
      const wrapper = mountDialog()
      return {
        spinner: wrapper.findComponent({ name: 'ProgressSpinner' }).exists(),
        errors: wrapper
          .findAllComponents({ name: 'Message' })
          .filter((message) => message.props('severity') === 'error').length
      }
    }

    for (const status of [null, 'pending']) {
      mocks.fit.status = status
      expect(shown()).toEqual({ spinner: true, errors: 0 })
    }
    // A fit that found no calibrants ends in a warning with no rows: it is
    // done, and the warning says why, alone
    mocks.fit.status = 'warning'
    mocks.fit.error = 'No calibration peaks found'
    expect(shown()).toEqual({ spinner: false, errors: 0 })
    mocks.fit.status = 'error'
    expect(shown()).toEqual({ spinner: false, errors: 1 })
  })

  it('shows no fit of the sample before beside a sample it is opened on again', async () => {
    // The dialog stays mounted between openings, and the refit waits on the
    // next sample's defaults: until it starts, the fit is the last sample's
    mocks.fit = fitted({ n_points: 2, segments: SEGMENTS }, [point(), SUMMARY])
    mocks.fit.loadInstrumentDefaults = vi.fn(fetching)
    const applied = [{ ...SEGMENTS[0], quality: { n_points: 5 } }, ...SEGMENTS.slice(1)]
    const next = { ...SAMPLE, sample_item_id: 'si-2', mz_calibration: record(applied) }
    const wrapper = mount(DialogCalibration, {
      props: { visible: false, context: next },
      global: { plugins: [PrimeVue], stubs }
    })

    await wrapper.setProps({ visible: true })
    await flushPromises()

    // The sample's own record, and no fit until its own is made
    expect(lists(wrapper).map((list) => list.heading)).toEqual(['Applied:'])
    expect(wrapper.findComponent({ name: 'DataTable' }).exists()).toBe(false)
  })

  it('shows no fit of the sample picked before beside the one picked next', async () => {
    const applied = [{ ...SEGMENTS[0], quality: { n_points: 5 } }, ...SEGMENTS.slice(1)]
    const first = { ...SAMPLE, mz_calibration: record(SEGMENTS) }
    const second = { ...SAMPLE, sample_item_id: 'si-2', mz_calibration: record(applied) }
    api.http.get.mockResolvedValueOnce([first, second])
    mocks.fit = fitted(null, null)
    mocks.fit.current = null
    const wrapper = mountDialog({ sample_batch_id: 'sb-1', sample_batch_name: 'composite' })
    await flushPromises()
    arrives(mocks.fit, { n_points: 2, segments: SEGMENTS }, [point(), SUMMARY])
    await flushPromises()
    // The first sample's fit repeats its record
    expect(lists(wrapper).map((list) => list.heading)).toEqual([null])

    mocks.fit.loadInstrumentDefaults = vi.fn(fetching)
    wrapper.findComponent({ name: 'Listbox' }).vm.$emit('update:modelValue', second)
    await flushPromises()

    expect(lists(wrapper).map((list) => list.heading)).toEqual(['Applied:'])
    expect(lists(wrapper)[0].ranges[0]).toBe('m/z 40-138: fitted on 5 calibrants (-11.93 ppm).')
  })

  it('lists nothing while there is no fit', () => {
    mocks.fit = fitted(null, null)
    mocks.fit.current = null

    const wrapper = mountDialog()

    expect(wrapper.find('[data-testid="calibration-segments"]').exists()).toBe(false)
  })
})
