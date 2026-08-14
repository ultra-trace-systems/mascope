import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The batch launcher's job is to send a config the API will accept, starting
// from the backend's batch default rather than the per-sample one. These mount
// the real component and assert the payload it posts.

const assign = vi.fn()

vi.mock('@/stores', () => ({
  useApp: () => ({ data: { batch: { assign } } })
}))

vi.mock('@/api', () => ({
  api: {
    http: {
      // The config form prefills from /params; give it defaults + limits.
      get: vi.fn().mockResolvedValue({
        data: {
          data: {
            params: {
              peak_assignment: {
                run_untargeted: true,
                mz_precision_ppm: 10,
                formula_ranges: 'C0-100 H0-100 O0-100 N0-100',
                max_untargeted_peaks: 300,
                peak_intensity_threshold: 0,
                max_alternatives: 5
              },
              peak_assignment_limits: {
                max_untargeted_peaks_ceiling: 5000,
                max_mz_precision_ppm: 100,
                max_alternatives_ceiling: 50
              }
            }
          }
        }
      })
    }
  }
}))

const BATCH = { sample_batch_id: 'sb-1', sample_batch_name: 'My Batch' }

// PrimeVue components render fine unregistered as long as we stub them; we care
// about the payload, not the widgets.
const GLOBAL_STUBS = {
  Dialog: { template: '<div><slot /><slot name="footer" /></div>' },
  Message: { template: '<div><slot /></div>' },
  Button: { template: '<button><slot /></button>' },
  InputNumber: true,
  InputText: true,
  ToggleSwitch: true,
  FloatLabel: { template: '<div><slot /></div>' }
}

async function mountDialog() {
  const { default: DialogPeakAssignBatch } = await import('@/lib/dialogs/DialogPeakAssignBatch.vue')
  return mount(DialogPeakAssignBatch, {
    props: { visible: true, batch: BATCH },
    global: { stubs: GLOBAL_STUBS }
  })
}

describe('DialogPeakAssignBatch', () => {
  beforeEach(() => {
    assign.mockReset()
    assign.mockResolvedValue(undefined)
  })
  afterEach(() => vi.clearAllMocks())

  it('defaults the untargeted stage off, matching the backend batch default', async () => {
    const wrapper = await mountDialog()
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(assign).toHaveBeenCalledTimes(1)
    const { sample_batch_id, config } = assign.mock.calls[0][0]
    expect(sample_batch_id).toBe('sb-1')
    expect(config.run_untargeted).toBe(false)
  })

  it('does not send nulls, so backend defaults apply to untouched fields', async () => {
    const wrapper = await mountDialog()
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    const { config } = assign.mock.calls[0][0]
    for (const [key, value] of Object.entries(config)) {
      expect(value, `${key} should not be null/empty`).not.toBeNull()
      expect(value).not.toBe('')
    }
  })

  it('sends an explicitly widened config through', async () => {
    const wrapper = await mountDialog()
    await wrapper.vm.$nextTick()

    wrapper.vm.config.run_untargeted = true
    wrapper.vm.config.max_untargeted_peaks = 42
    await wrapper.vm.launch()

    const { config } = assign.mock.calls[0][0]
    expect(config.run_untargeted).toBe(true)
    expect(config.max_untargeted_peaks).toBe(42)
  })

  it('does nothing without a batch', async () => {
    const { default: DialogPeakAssignBatch } =
      await import('@/lib/dialogs/DialogPeakAssignBatch.vue')
    const wrapper = mount(DialogPeakAssignBatch, {
      props: { visible: true, batch: null },
      global: { stubs: GLOBAL_STUBS }
    })
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(assign).not.toHaveBeenCalled()
  })
})
