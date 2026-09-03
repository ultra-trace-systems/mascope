import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

// The button that builds the batch peaks launches a background task, so the two
// things it must get right are both about time: it may not offer an action that
// cannot run, and it may not report "done" when all it has is an acknowledgement
// that the work started. It sits in the browser's switch bar rather than in the
// ledger's header, so the state behind it lives in a store - which is where the
// notification handling and the timeout are tested from.

const post = vi.fn()

vi.mock('@/api', () => ({ api: { http: { post: (...args) => post(...args) } } }))

let app
// Callbacks the store registered through app.ui.notification.on, by type.
let notificationHandlers

vi.mock('@/stores', () => ({ useApp: () => app }))

// Minimal help-mode facade: the bar registers a help card through these calls;
// the tests only need them to resolve.
const helpStub = {
  set: vi.fn(),
  docUrl: (path = '') => `/docs/${path}`,
  directive: () => ({}),
  right: () => ({}),
  left: () => ({}),
  top: () => ({}),
  bottom: () => ({})
}

const BATCH = { sample_batch_id: 'b-1', sample_batch_name: 'Batch 1' }
const WORKSPACE = { workspace_id: 'ws-1', workspace_name: 'Project', my_role: 'editor' }

function makeApp({ batch = BATCH, workspace = WORKSPACE, samples = [{}] } = {}) {
  return reactive({
    auth: { user: { role_id: 200, is_superuser: false } },
    data: {
      batch: { focused: batch, focusedId: batch?.sample_batch_id ?? null },
      workspace: { focused: workspace },
      sample: { list: samples, pending: false }
    },
    ui: {
      help: helpStub,
      notification: {
        on: (type, callback) => {
          notificationHandlers[type] = [...(notificationHandlers[type] ?? []), callback]
          return { remove: () => {} }
        },
        push: vi.fn()
      }
    }
  })
}

const GLOBAL_STUBS = {
  Button: {
    props: ['label', 'disabled', 'loading'],
    template:
      '<button class="compute-button" :disabled="disabled" :data-loading="String(!!loading)">{{ label }}</button>'
  }
}

const { default: BatchPeakComputeBar } =
  await import('@/lib/panes/PaneBrowserMatch/BatchPeakComputeBar.vue')
const { useBatchPeakCompute } =
  await import('@/lib/panes/PaneBrowserMatch/stores/batchPeakCompute.js')

let wrapper
let compute

function mountBar(options = {}) {
  // The app facade first: the store reads it while it is being created, and the
  // component creates the store on mount.
  app = makeApp(options)
  setActivePinia(createPinia())
  wrapper = mount(BatchPeakComputeBar, {
    global: { stubs: GLOBAL_STUBS, directives: { tooltip: {}, help: {} } }
  })
  compute = useBatchPeakCompute()
  return wrapper
}

/** Deliver a `compute_batch_peaks` packet the way the socket would. */
const notify = (payload) =>
  (notificationHandlers['compute_batch_peaks'] ?? []).forEach((cb) => cb(payload))

/** An axios-shaped rejection carrying a server message. */
const apiError = (status, message) => ({ response: { status, data: { error: message } } })

/** A 202 acknowledgement, with the process id on the header the route sets. */
const ack = (processId = 'proc-1') => ({ headers: { 'process-id': processId } })

beforeEach(() => {
  vi.useFakeTimers()
  notificationHandlers = {}
  post.mockReset()
  post.mockResolvedValue(ack())
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('BatchPeakComputeBar applicability', () => {
  it('offers the action when the batch has samples and the user may write', () => {
    mountBar()

    expect(compute.blockedReason).toBeNull()
    expect(wrapper.find('button.compute-button').attributes('disabled')).toBeUndefined()
  })

  it('disables with a reason when no batch is focused', () => {
    // The bar renders on "no sample focused" alone, so nothing-focused-at-all
    // renders it - the state the old silent early return hid.
    mountBar({ batch: null })

    expect(compute.blockedReason).toMatch(/select a batch/i)
    expect(wrapper.find('button.compute-button').attributes('disabled')).toBeDefined()
  })

  it('disables with a reason when the batch has no samples', () => {
    mountBar({ samples: [] })

    expect(compute.blockedReason).toMatch(/no samples/i)
  })

  it('disables with a reason for a viewer, rather than letting them earn a 403', () => {
    mountBar({ workspace: { ...WORKSPACE, my_role: 'guest' } })

    expect(compute.blockedReason).toMatch(/editor role/i)
  })

  it('stays enabled while the batch samples are still loading', async () => {
    // An empty list is not evidence of an empty batch until the load lands;
    // reading it as one would disable the button on every batch switch.
    mountBar({ samples: [] })
    app.data.sample.pending = true
    await wrapper.vm.$nextTick()

    expect(compute.blockedReason).toBeNull()
  })

  it('shows the reason on the wrapper, which a disabled button cannot show itself', () => {
    mountBar({ batch: null })

    expect(wrapper.find('.compute-bar').classes()).toContain('blocked')
    expect(compute.computeTooltip).toBe(compute.blockedReason)
  })

  it('does not fire the request while blocked', async () => {
    mountBar({ samples: [] })

    await compute.compute()

    expect(post).not.toHaveBeenCalled()
    expect(compute.computing).toBe(false)
  })
})

describe('BatchPeakComputeBar progress', () => {
  it('stays loading after the acknowledgement, and stops on the task notification', async () => {
    mountBar()

    await compute.compute()

    // The 202 says the work started, not that it finished.
    expect(post).toHaveBeenCalledTimes(1)
    expect(compute.computing).toBe(true)

    notify({ type: 'compute_batch_peaks', status: 'success', process_id: 'proc-1' })
    expect(compute.computing).toBe(false)
  })

  it('reports the wait on the button itself', async () => {
    mountBar()

    await compute.compute()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('button.compute-button').attributes('data-loading')).toBe('true')
  })

  it('ignores another backfill of the same batch', async () => {
    // The notification goes to the batch's room, so a second user's run of the
    // same batch arrives here too - and it is not this button's to end.
    mountBar()

    await compute.compute()
    notify({ type: 'compute_batch_peaks', status: 'success', process_id: 'someone-else' })

    expect(compute.computing).toBe(true)
  })

  it('stops loading when the task fails, not only when it succeeds', async () => {
    // The reload event fires on success only, so a spinner keyed off that would
    // never stop for a failed run.
    mountBar()

    await compute.compute()
    notify({ type: 'compute_batch_peaks', status: 'error', process_id: 'proc-1' })

    expect(compute.computing).toBe(false)
  })

  it('ends on an unidentifiable packet rather than spinning forever', async () => {
    post.mockResolvedValue({ headers: {} })
    mountBar()

    await compute.compute()
    expect(compute.computing).toBe(true)

    notify({ type: 'compute_batch_peaks', status: 'success' })
    expect(compute.computing).toBe(false)
  })

  it('keeps waiting through the per-sample progress packets', async () => {
    // The backfill reports as it folds each sample, on this same channel and
    // under this same process id. Those packets drive the app's progress bar;
    // the button is asking whether the run is still going, and while they
    // arrive the answer is yes.
    mountBar()

    await compute.compute()

    notify({ type: 'compute_batch_peaks', status: 'pending', process_id: 'proc-1', progress: 25 })
    expect(compute.computing).toBe(true)

    notify({ type: 'compute_batch_peaks', status: 'pending', process_id: 'proc-1', progress: 75 })
    expect(compute.computing).toBe(true)

    // ...and the terminal packet still ends it.
    notify({ type: 'compute_batch_peaks', status: 'success', process_id: 'proc-1' })
    expect(compute.computing).toBe(false)
  })

  it('gives up after the timeout, so a dropped socket cannot strand the button', async () => {
    mountBar()

    await compute.compute()
    expect(compute.computing).toBe(true)

    vi.advanceTimersByTime(5 * 60 * 1000)
    expect(compute.computing).toBe(false)
  })

  // The listener is registered in the store's scope rather than the bar's, so a
  // run survives the bar being torn down and rebuilt - which is what focusing a
  // sample mid-run and coming back to the batch does.
  it('still ends a run launched before the bar was unmounted', async () => {
    mountBar()
    await compute.compute()

    wrapper.unmount()
    wrapper = null

    notify({ type: 'compute_batch_peaks', status: 'success', process_id: 'proc-1' })
    expect(compute.computing).toBe(false)
  })
})

describe('BatchPeakComputeBar failed launch', () => {
  it('resets the button and records the refusal', async () => {
    // 403 is the refusal this route issues - the editor-role check and the
    // feature flag both answer with one - and what an editor role revoked
    // mid-session looks like from here. The message itself is rendered by the
    // ledger below (paneBrowserBatchPeaks.spec.js), next to the table it is
    // about.
    post.mockRejectedValue(
      apiError(403, 'Access denied. You do not have permission to perform this action.')
    )
    mountBar()

    await compute.compute()

    expect(compute.computing).toBe(false)
    expect(compute.launchRefused).toBe(true)
    expect(compute.launchError).toContain('do not have permission')
  })

  it('distinguishes a genuine failure from a refusal', async () => {
    post.mockRejectedValue(apiError(500, 'Unexpected error (ref: abc12345).'))
    mountBar()

    await compute.compute()

    expect(compute.launchRefused).toBe(false)
    expect(compute.launchError).toContain('Unexpected error')
  })

  it('asks the interceptor to hold the toast it would duplicate', async () => {
    // The message is rendered in the ledger, so the global error toast would say
    // the same thing twice. `errors: 'inline'` is what holds it back
    // (src/api/http.js); the interceptor itself is mocked out here, so the
    // option being sent is the whole of what this test can hold.
    post.mockRejectedValue(apiError(500, 'boom'))
    mountBar()

    await compute.compute()

    expect(post.mock.calls[0][2]).toMatchObject({ errors: 'inline' })
  })

  it('clears a previous failure when the next attempt starts', async () => {
    post.mockRejectedValueOnce(apiError(500, 'boom')).mockResolvedValue(ack())
    mountBar()

    await compute.compute()
    expect(compute.launchError).toBe('boom')

    await compute.compute()
    expect(compute.launchError).toBeNull()
  })
})

describe('BatchPeakComputeBar untargeted search', () => {
  it('offers the search beside the compute, under the same gate', () => {
    mountBar()
    const buttons = wrapper.findAll('.compute-button')
    expect(buttons.map((b) => b.text())).toEqual(['Rebuild batch ledger', 'Search untargeted'])
    expect(buttons[1].attributes('disabled')).toBeUndefined()
  })

  it('disables the search with the compute when no batch is focused', () => {
    mountBar({ batch: null })
    expect(wrapper.findAll('.compute-button')[1].attributes('disabled')).toBeDefined()
  })

  it('posts to the search route and waits for the task notification', async () => {
    mountBar()
    post.mockResolvedValueOnce({ headers: { 'process-id': 'proc-s' } })
    await wrapper.findAll('.compute-button')[1].trigger('click')
    await Promise.resolve()
    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/b-1/search-untargeted',
      {},
      expect.objectContaining({ type: 'search_batch_untargeted' })
    )
    expect(compute.searching).toBe(true)
    for (const handler of notificationHandlers['search_batch_untargeted'] ?? []) {
      handler({ status: 'pending', process_id: 'proc-s' })
    }
    expect(compute.searching).toBe(true)
    for (const handler of notificationHandlers['search_batch_untargeted'] ?? []) {
      handler({ status: 'success', process_id: 'proc-s' })
    }
    expect(compute.searching).toBe(false)
  })
})
