import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'

// The batch launcher's job is to send a config the API will accept, starting
// from the backend's batch default rather than the per-sample one, and to say
// what happened when the endpoint declines. These mount the real component and
// assert the payload it posts and how it reports a refusal.

const assign = vi.fn()
const push = vi.fn()

vi.mock('@/stores', () => ({
  useApp: () => ({ data: { batch: { assign } }, ui: { notification: { push } } })
}))

/** Whether the launcher asked its parent to close the dialog. */
function closedBy(wrapper) {
  const events = wrapper.emitted('update:visible') ?? []
  return events.length > 0 && events[events.length - 1][0] === false
}

/** An axios-shaped rejection carrying a server message. */
function apiError(status, message) {
  return { response: { status, data: { error: message } } }
}

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

// Imported statically (vi.mock is hoisted above it, so the stubs still apply):
// compiling the dialog and its config form is the expensive part, and as a
// dynamic import inside the first test it lands on that one test's clock.
const { default: DialogPeakAssignBatch } = await import(
  '@/lib/dialogs/DialogPeakAssignBatch.vue'
)

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
  return mount(DialogPeakAssignBatch, {
    props: { visible: true, batch: BATCH },
    global: { stubs: GLOBAL_STUBS }
  })
}

describe('DialogPeakAssignBatch', () => {
  beforeEach(() => {
    assign.mockReset()
    assign.mockResolvedValue(undefined)
    push.mockReset()
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

  it('closes and reports nothing extra when the launch is accepted', async () => {
    const wrapper = await mountDialog()
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(closedBy(wrapper)).toBe(true)
    expect(push).not.toHaveBeenCalled()
  })

  it('closes and reports the reason when the batch is refused', async () => {
    // A run already in flight for one of the batch's samples: the endpoint
    // declines with 409 and says which sample holds it up.
    assign.mockRejectedValue(
      apiError(409, "Peak assignment is already running for 1 sample of sample batch 'My Batch'.")
    )
    const wrapper = await mountDialog()
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(closedBy(wrapper)).toBe(true)
    expect(push).toHaveBeenCalledTimes(1)
    const notification = push.mock.calls[0][0]
    expect(notification.status).toBe('warning')
    expect(notification.message).toContain('already running')
  })

  it('reports a genuine failure as an error, not a refusal', async () => {
    assign.mockRejectedValue(apiError(500, 'Unexpected error (ref: abc12345).'))
    const wrapper = await mountDialog()
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(push.mock.calls[0][0].status).toBe('error')
  })

  it('leaves the launch button usable after a refusal', async () => {
    assign.mockRejectedValue(apiError(409, 'busy'))
    const wrapper = await mountDialog()
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    // The rejection must not be left uncaught, and must not strand the spinner.
    expect(wrapper.vm.submitting).toBe(false)
  })

  it('does nothing without a batch', async () => {
    const wrapper = mount(DialogPeakAssignBatch, {
      props: { visible: true, batch: null },
      global: { stubs: GLOBAL_STUBS }
    })
    await wrapper.vm.$nextTick()

    await wrapper.vm.launch()

    expect(assign).not.toHaveBeenCalled()
  })
})
