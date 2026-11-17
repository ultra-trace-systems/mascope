import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PrimeVue from 'primevue/config'

// The calibration trend dialog reads GET /api/calibration/trend for the
// instrument it is opened on, each time it opens and whenever the polarity or
// the window changes, and draws what it gets: the chart, its legend and the
// numbered changes of collection, or says there is nothing, or that the read
// failed. Export CSV downloads the window shown.

vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))
vi.mock('@/stores', () => ({ useApp: () => ({ ui: { darkmode: { active: false } } }) }))
vi.mock('@/lib/runtime.js', () => ({ runtime: { api_path: 'http://api.test' } }))

import { api } from '@/api'
import { TREND_TIMEOUT_MS } from '@/lib/calibrationTrend'
import DialogCalibrationTrend from '@/lib/dialogs/DialogCalibrationTrend.vue'

const SelectStub = {
  props: ['modelValue', 'options', 'optionValue', 'ariaLabel'],
  emits: ['update:modelValue'],
  template: `<select :aria-label="ariaLabel" :value="modelValue"
      @change="$emit('update:modelValue', optionValue === 'days' ? Number($event.target.value) : $event.target.value)">
      <option v-for="o in options" :key="o[optionValue]" :value="o[optionValue]">{{ o.label }}</option>
    </select>`
}
const STUBS = {
  Dialog: {
    props: ['visible', 'header'],
    template: '<div v-if="visible" class="dialog"><h2>{{ header }}</h2><slot /></div>'
  },
  Button: {
    props: ['label', 'disabled'],
    emits: ['click'],
    template: `<button :disabled="disabled" @click="$emit('click')">{{ label }}</button>`
  },
  Select: SelectStub,
  Message: { template: '<p class="message"><slot /></p>' },
  ProgressSpinner: { template: '<span>Loading</span>' },
  BaseEmptyState: {
    props: ['title', 'hint'],
    template: '<p class="empty">{{ title }}</p><p class="empty-hint">{{ hint }}</p>'
  },
  BaseLoadError: {
    props: ['fallback', 'onRetry'],
    template: `<p class="load-error">{{ fallback }}<button @click="onRetry()">Try again</button></p>`
  },
  BaseChartPlotly: {
    name: 'BaseChartPlotly',
    props: ['data', 'layout', 'loading'],
    template: '<div class="chart-stub" />'
  }
}

const file = (n, more = {}) => ({
  sample_file_id: `f${n}`,
  filename: `tof_${n}.h5`,
  datetime_utc: `2026-10-1${n}T08:00:00+00:00`,
  status: 'ok',
  collection: 0,
  n_points: 3,
  n_candidates: 4,
  post_fit_ppm: 1.2,
  pre_fit_abs_ppm: 80,
  axis_offset_ppm: -80,
  ...more
})

const TREND = {
  instrument: 'TOF-X',
  polarity: '-',
  truncated: false,
  binned: false,
  bin: 'day',
  n_files: 4,
  bins: [],
  files: [
    file(1),
    file(2, { collection: 1 }),
    file(3, { status: 'failed', n_points: null, n_candidates: null, collection: null }),
    file(4, { n_candidates: null, collection: null })
  ],
  calibrants: [
    {
      ion: 'NO3-',
      target_mz: 61.9884,
      residual_ppm: [0.5, 0.2, null, null],
      retained: [true, true, null, null]
    }
  ],
  collections: [
    { id: 'set', isotopes_sha256: 'a'.repeat(64), name: 'Nitrate set' },
    { id: 'set', isotopes_sha256: 'b'.repeat(64), name: 'Nitrate set' }
  ],
  changes: [
    {
      datetime_utc: '2026-10-12T08:00:00+00:00',
      sample_file_id: 'f2',
      kind: 'edited',
      collection: 1,
      collection_id: 'set',
      isotopes_sha256: 'b'.repeat(64),
      previous_isotopes_sha256: 'a'.repeat(64)
    }
  ]
}

// More files than the trend draws one by one: served per day. One day of
// the nitrate set, and failed calibrations and fits named no collection.
const BINNED = {
  ...TREND,
  binned: true,
  n_files: 6200,
  files: [],
  bins: [
    {
      start_utc: '2026-10-19T00:00:00+00:00',
      collection_id: 'set',
      n_files: 6000,
      n_failed: 0,
      n_without_lines: 1,
      axis_offset_ppm: [-82, -80, -78],
      post_fit_ppm: [0.4, 0.6, 0.8],
      n_points: [3, 3, 4],
      n_candidates: [4, 4, 4]
    },
    {
      start_utc: '2026-10-19T00:00:00+00:00',
      collection_id: null,
      n_files: 200,
      n_failed: 4,
      n_without_lines: 2,
      axis_offset_ppm: null,
      post_fit_ppm: [1, 1.2, 1.4],
      n_points: [2, 2, 2],
      n_candidates: null
    }
  ],
  calibrants: [
    {
      ion: 'NO3-',
      target_mz: 61.9884,
      residual_ppm: [[0.1, 0.2, 0.3], null],
      n_listed: [6000, 0],
      n_kept: [5990, 0]
    }
  ]
}

const serve = (answer) =>
  api.http.get.mockImplementation(async () => {
    if (answer instanceof Error) throw answer
    return { data: { data: answer } }
  })

const mountDialog = async (props = {}) => {
  const wrapper = mount(DialogCalibrationTrend, {
    props: { visible: true, instrument: 'TOF-X', polarity: '-', ...props },
    global: { plugins: [PrimeVue], stubs: STUBS }
  })
  await flushPromises()
  return wrapper
}

const lastQuery = () => api.http.get.mock.calls.at(-1)[1].params

const exportButton = (wrapper) => wrapper.findAll('button').find((b) => b.text() === 'Export CSV')

describe('DialogCalibrationTrend', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    serve(TREND)
  })

  it('reads the trend of the instrument and polarity it opens on', async () => {
    await mountDialog({ polarity: '+' })

    expect(api.http.get).toHaveBeenCalledTimes(1)
    const [url, config] = api.http.get.mock.calls[0]
    expect(url).toBe('/calibration/trend')
    expect(config.params.instrument).toBe('TOF-X')
    expect(config.params.polarity).toBe('+')
    expect(config.errors).toBe('inline')
    expect(config.timeout).toBe(TREND_TIMEOUT_MS)
    const days =
      (new Date(config.params.datetime_max) - new Date(config.params.datetime_min)) / 86_400_000
    expect(days).toBe(90)
  })

  it('reads nothing while closed, and again each time it opens', async () => {
    const wrapper = await mountDialog({ visible: false })
    expect(api.http.get).not.toHaveBeenCalled()

    await wrapper.setProps({ visible: true })
    await flushPromises()
    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(api.http.get).toHaveBeenCalledTimes(2)
  })

  it('opens on the polarity it is given, though another was chosen before', async () => {
    const wrapper = await mountDialog({ polarity: '-' })
    await wrapper.find('select[aria-label="Polarity"]').setValue('+')
    await flushPromises()
    await wrapper.setProps({ visible: false })

    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(lastQuery().polarity).toBe('-')
  })

  it('reads again for another polarity or window', async () => {
    const wrapper = await mountDialog()

    await wrapper.find('select[aria-label="Polarity"]').setValue('+')
    await flushPromises()
    expect(lastQuery().polarity).toBe('+')

    await wrapper.find('select[aria-label="Acquired in"]').setValue('30')
    await flushPromises()
    const query = lastQuery()
    expect((new Date(query.datetime_max) - new Date(query.datetime_min)) / 86_400_000).toBe(30)
    expect(api.http.get).toHaveBeenCalledTimes(3)
  })

  it('draws the trend with its legend and numbered changes', async () => {
    const wrapper = await mountDialog()

    const chart = wrapper.findComponent({ name: 'BaseChartPlotly' })
    expect(chart.exists()).toBe(true)
    expect(chart.props('data').length).toBeGreaterThan(0)
    const legend = wrapper.find('ul.legend').text()
    expect(legend).toContain('Nitrate set')
    expect(legend).toContain('Line left out by the fit')
    expect(legend).toContain('Calibration failed')
    expect(legend).toContain('No collection on record')
    expect(wrapper.find('ol.changes').text()).toBe('1 2026-10-12 08:00 · Nitrate set edited')
  })

  it('names no grey key where every fit names its collection', async () => {
    serve({
      ...TREND,
      files: TREND.files.filter((f) => f.collection != null || f.status === 'failed')
    })
    const wrapper = await mountDialog()

    expect(wrapper.find('ul.legend').text()).not.toContain('No collection on record')
  })

  it('says how many files show without their lines', async () => {
    const wrapper = await mountDialog()

    expect(wrapper.text()).toContain('1 file was fitted before records listed their lines')
  })

  it('says when only the latest files of the window are shown', async () => {
    serve({ ...TREND, truncated: true })
    const wrapper = await mountDialog()

    expect(wrapper.find('.message').text()).toContain('the latest 4 are shown')
  })

  it('names the median and its band, not the points, for a binned window', async () => {
    serve(BINNED)
    const wrapper = await mountDialog()

    const legend = wrapper.find('ul.legend').text()
    expect(legend).toContain('Median per day, the middle half shaded')
    expect(legend).not.toContain('Line kept by the fit')
    expect(legend).not.toContain('Line left out by the fit')
    expect(legend).toContain('Calibration failed')
    expect(legend).toContain('No collection on record')
    expect(wrapper.findComponent({ name: 'BaseChartPlotly' }).props('data').length).toBeGreaterThan(
      0
    )
  })

  it('says a binned window shows medians, and how many files they sum up', async () => {
    serve(BINNED)
    const wrapper = await mountDialog()

    const note = wrapper.find('.message').text()
    expect(note).toContain('6,200 fitted or failed files, more than the chart draws one by one')
    expect(note).toContain('median per day')
    expect(note).not.toContain('Export CSV')
    expect(wrapper.text()).toContain('3 files were fitted before records listed their lines')
    const exportButton = wrapper.findAll('button').find((b) => b.text() === 'Export CSV')
    expect(exportButton.attributes('disabled')).toBeUndefined()
  })

  it('says the export lists the latest files of a window that holds more', async () => {
    serve({ ...BINNED, bin: 'hour', n_files: 25_000 })
    const wrapper = await mountDialog()

    const note = wrapper.find('.message').text()
    expect(note).toContain('25,000 fitted or failed files')
    expect(note).toContain('median per hour')
    expect(note).toContain('Export CSV lists the latest 20,000 files one by one.')
  })

  it('names no grey key and no cross where a binned window has neither', async () => {
    serve({ ...BINNED, bins: BINNED.bins.slice(0, 1) })
    const wrapper = await mountDialog()

    const legend = wrapper.find('ul.legend').text()
    expect(legend).not.toContain('No collection on record')
    expect(legend).not.toContain('Calibration failed')
  })

  it('names no grey key for a bin of failed calibrations alone', async () => {
    const failedOnly = { ...BINNED.bins[1], n_files: 4, n_failed: 4 }
    serve({ ...BINNED, bins: [BINNED.bins[0], failedOnly] })
    const wrapper = await mountDialog()

    const legend = wrapper.find('ul.legend').text()
    expect(legend).not.toContain('No collection on record')
    expect(legend).toContain('Calibration failed')
  })

  it('says nothing of the export where it lists every file of the window', async () => {
    serve({ ...BINNED, n_files: 20_000 })
    const wrapper = await mountDialog()

    expect(wrapper.find('.message').text()).not.toContain('Export CSV')
  })

  it('says when the window holds no calibrated file', async () => {
    serve({ ...TREND, n_files: 0, files: [], calibrants: [], collections: [], changes: [] })
    const wrapper = await mountDialog()

    expect(wrapper.find('.empty').text()).toBe('No calibrated files')
    expect(wrapper.find('.empty-hint').text()).toBe(
      'No negative TOF file of this instrument was calibrated in this window. ' +
        'Try the positive polarity or a longer window.'
    )
    expect(wrapper.findComponent({ name: 'BaseChartPlotly' }).exists()).toBe(false)
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()

    await wrapper.find('select[aria-label="Polarity"]').setValue('+')
    await flushPromises()
    expect(wrapper.find('.empty-hint').text()).toContain('Try the negative polarity')
  })

  it('says when the read failed, and reads again on request', async () => {
    serve(new Error('offline'))
    const wrapper = await mountDialog()
    expect(wrapper.find('.load-error').text()).toContain('Could not load the calibration trend.')

    serve(TREND)
    await wrapper.find('.load-error button').trigger('click')
    await flushPromises()

    expect(wrapper.find('.load-error').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'BaseChartPlotly' }).exists()).toBe(true)
  })

  it('keeps the answer to the latest read when an earlier one comes after it', async () => {
    let releaseFirst
    const empty = { ...TREND, n_files: 0, files: [], calibrants: [], collections: [], changes: [] }
    api.http.get
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            releaseFirst = () => resolve({ data: { data: TREND } })
          })
      )
      .mockImplementationOnce(async () => ({ data: { data: empty } }))
    const wrapper = mount(DialogCalibrationTrend, {
      props: { visible: true, instrument: 'TOF-X', polarity: '-' },
      global: { plugins: [PrimeVue], stubs: STUBS }
    })
    await wrapper.find('select[aria-label="Polarity"]').setValue('+')
    await flushPromises()
    expect(wrapper.find('.empty').exists()).toBe(true)

    releaseFirst()
    await flushPromises()

    expect(wrapper.find('.empty').text()).toBe('No calibrated files')
    expect(wrapper.findComponent({ name: 'BaseChartPlotly' }).exists()).toBe(false)
  })

  it('shows no trend of another instrument while it reads the one it opens on', async () => {
    const wrapper = await mountDialog()
    expect(wrapper.find('ol.changes').exists()).toBe(true)
    await wrapper.setProps({ visible: false })
    let release
    api.http.get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ data: { data: { ...TREND, instrument: 'TOF-Y' } } })
        })
    )

    await wrapper.setProps({ instrument: 'TOF-Y', visible: true })
    await flushPromises()

    expect(wrapper.find('h2').text()).toBe('Calibration trend: TOF-Y')
    expect(lastQuery().instrument).toBe('TOF-Y')
    expect(wrapper.text()).toContain('Loading')
    expect(wrapper.findComponent({ name: 'BaseChartPlotly' }).exists()).toBe(false)
    expect(wrapper.find('ul.legend').exists()).toBe(false)
    expect(wrapper.find('ol.changes').exists()).toBe(false)
    expect(exportButton(wrapper).attributes('disabled')).toBeDefined()

    release()
    await flushPromises()

    expect(wrapper.findComponent({ name: 'BaseChartPlotly' }).exists()).toBe(true)
    expect(exportButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('keeps the chart, faded, while it reads the same instrument again', async () => {
    const wrapper = await mountDialog()
    api.http.get.mockImplementationOnce(() => new Promise(() => {}))

    await wrapper.find('select[aria-label="Polarity"]').setValue('+')
    await flushPromises()

    const chart = wrapper.findComponent({ name: 'BaseChartPlotly' })
    expect(chart.exists()).toBe(true)
    expect(chart.props('loading')).toBe(true)
    expect(wrapper.find('ol.changes').exists()).toBe(true)
  })

  it('downloads the window shown as CSV', async () => {
    const wrapper = await mountDialog({ polarity: '+' })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    let href
    click.mockImplementation(function () {
      href = this.href
    })

    await wrapper
      .findAll('button')
      .find((b) => b.text() === 'Export CSV')
      .trigger('click')

    const url = new URL(href)
    expect(url.pathname).toBe('/api/calibration/trend/export')
    expect(url.searchParams.get('instrument')).toBe('TOF-X')
    expect(url.searchParams.get('polarity')).toBe('+')
    expect(url.searchParams.get('datetime_min')).toBe(lastQuery().datetime_min)
    click.mockRestore()
  })
})
