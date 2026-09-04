import { describe, it, expect, vi, beforeEach } from 'vitest'

const scrollToSample = vi.fn()
vi.mock('@/lib/panes/PaneBrowserSample/stores/sampleScroller.js', () => ({
  useSampleScroller: () => ({ scrollToSample })
}))
// The wait for the peak store is the settle helper's business; here it resolves
// at once, and `moved` covers the one thing the helper must check after it.
const untilStoreSettled = vi.fn(() => Promise.resolve())
vi.mock('@/lib/store/settle', () => ({
  untilStoreSettled: (...args) => untilStoreSettled(...args)
}))

const { focusSamplePeak } = await import('@/lib/panes/PaneBrowserSample/stores/focusSamplePeak.js')

const SAMPLE = { sample_item_id: 's-1', sample_item_name: 'S1' }
const PEAK = { peak_id: 7, mz: 181.0707 }

function makeApp({ peaks = [PEAK], focusedAfterSwitch = 's-1' } = {}) {
  const app = {
    data: {
      sample: {
        focusedId: null,
        focus: vi.fn((sample) => {
          app.data.sample.focusedId = sample.sample_item_id
        })
      },
      peak: { list: peaks, pending: false, focus: vi.fn() }
    },
    ui: { tab: { active: 'batch' } }
  }
  untilStoreSettled.mockImplementation(async () => {
    app.data.sample.focusedId = focusedAfterSwitch
  })
  return app
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('focusSamplePeak', () => {
  it('focuses the sample and stops there when no peak is asked for', async () => {
    const app = makeApp()
    expect(await focusSamplePeak(app, SAMPLE, null)).toBe('sample')
    expect(app.data.sample.focus).toHaveBeenCalledWith(SAMPLE)
    expect(scrollToSample).toHaveBeenCalledWith('s-1')
    expect(untilStoreSettled).not.toHaveBeenCalled()
    expect(app.data.peak.focus).not.toHaveBeenCalled()
    expect(app.ui.tab.active).toBe('batch')
  })

  it('waits for the peaks, focuses the peak and brings the Sample tab forward', async () => {
    const app = makeApp()
    // The series and the peak feed disagree on the id type: compared as strings.
    expect(await focusSamplePeak(app, SAMPLE, '7')).toBe('peak')
    expect(untilStoreSettled).toHaveBeenCalledTimes(1)
    expect(app.data.peak.focus).toHaveBeenCalledWith(PEAK)
    expect(app.ui.tab.active).toBe('sample')
  })

  it('leaves the peak alone when the focus moved to another sample while waiting', async () => {
    const app = makeApp({ focusedAfterSwitch: 's-2' })
    expect(await focusSamplePeak(app, SAMPLE, 7)).toBe('moved')
    expect(app.data.peak.focus).not.toHaveBeenCalled()
    expect(app.ui.tab.active).toBe('batch')
  })

  it('says so when the peak is not in the loaded list, and stays on the sample', async () => {
    const app = makeApp({ peaks: [] })
    expect(await focusSamplePeak(app, SAMPLE, 7)).toBe('missing')
    expect(app.data.sample.focus).toHaveBeenCalledWith(SAMPLE)
    expect(app.data.peak.focus).not.toHaveBeenCalled()
  })
})
