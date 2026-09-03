import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'

const post = vi.fn()
vi.mock('@/api', () => ({ api: { http: { post: (...args) => post(...args) } } }))

let app
let handlers
vi.mock('@/stores', () => ({ useApp: () => app }))

const { useBatchPeakCuration } =
  await import('@/lib/panes/PanePeakAssign/stores/batchPeakCuration.js')

const BODY = { batch_peak_id: 'bp-1', candidate: 1, expected_formula: 'C6H12O6' }

function makeApp() {
  return reactive({
    data: {
      batch: { focusedId: 'sb-1' },
      peakAssignment: { peak: { load: vi.fn().mockResolvedValue({}) } },
      batchPeak: { load: vi.fn().mockResolvedValue({}) }
    },
    ui: {
      notification: {
        on: (type, callback) => {
          handlers[type] = [...(handlers[type] ?? []), callback]
          return { remove: () => {} }
        }
      }
    }
  })
}
const emit = (type, notification) => (handlers[type] ?? []).forEach((cb) => cb(notification))
/** A 202 acknowledgement, with the process id on the header the route sets. */
const ack = (processId = 'proc-1') => ({ headers: { 'process-id': processId } })

beforeEach(() => {
  vi.useFakeTimers()
  handlers = {}
  app = makeApp()
  setActivePinia(createPinia())
  post.mockReset()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('batchPeakCuration store', () => {
  it('posts the pin and resolves when the task reports back', async () => {
    post.mockResolvedValue(ack())
    const store = useBatchPeakCuration()

    const promise = store.curate(BODY)
    await vi.advanceTimersByTimeAsync(0)
    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1/curate',
      BODY,
      expect.objectContaining({ type: 'curate_batch_peak' })
    )
    expect(store.curating).toBe(true)

    // Progress is not completion.
    emit('curate_batch_peak', { status: 'pending', process_id: 'proc-1' })
    expect(store.curating).toBe(true)

    emit('curate_batch_peak', { status: 'success', process_id: 'proc-1', message: 'done' })
    await expect(promise).resolves.toMatchObject({ status: 'success' })
    expect(store.curating).toBe(false)
  })

  it("does not settle on another curation's packet", async () => {
    post.mockResolvedValue(ack('proc-1'))
    const store = useBatchPeakCuration()
    const promise = store.curate(BODY)
    await vi.advanceTimersByTimeAsync(0)

    emit('curate_batch_peak', { status: 'success', process_id: 'proc-2' })
    expect(store.curating).toBe(true)

    emit('curate_batch_peak', { status: 'success', process_id: 'proc-1' })
    await expect(promise).resolves.toBeTruthy()
  })

  it('rejects when the task reports an error', async () => {
    post.mockResolvedValue(ack())
    const store = useBatchPeakCuration()
    const promise = store.curate(BODY)
    await vi.advanceTimersByTimeAsync(0)

    emit('curate_batch_peak', { status: 'error', process_id: 'proc-1', message: 'no such anchor' })
    await expect(promise).rejects.toThrow('no such anchor')
    expect(store.curating).toBe(false)
  })

  it('rejects when the launch itself is refused, and is free again', async () => {
    const refusal = { response: { status: 409 } }
    post.mockRejectedValue(refusal)
    const store = useBatchPeakCuration()

    await expect(store.curate(BODY)).rejects.toBe(refusal)
    expect(store.curating).toBe(false)
  })

  it('gives up after the bounded wait', async () => {
    post.mockResolvedValue(ack())
    const store = useBatchPeakCuration()
    const promise = store.curate(BODY)
    await vi.advanceTimersByTimeAsync(0)

    const settled = expect(promise).rejects.toThrow('did not report back')
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 1)
    await settled
    expect(store.curating).toBe(false)
  })

  it('refuses a second launch while one is running', async () => {
    post.mockResolvedValue(ack())
    const store = useBatchPeakCuration()
    const first = store.curate(BODY)
    await vi.advanceTimersByTimeAsync(0)

    expect(await store.curate(BODY)).toBeNull()
    expect(post).toHaveBeenCalledTimes(1)

    emit('curate_batch_peak', { status: 'success', process_id: 'proc-1' })
    await first
  })

  it('releases and refreshes the sample and the batch ledger', async () => {
    post.mockResolvedValue({ data: [{ restored: 1, skipped: 0, formula: 'C7H14O7' }] })
    const store = useBatchPeakCuration()

    const outcome = await store.release({ batch_peak_id: 'bp-1' })
    expect(post).toHaveBeenCalledWith(
      '/batch-peaks/batch/sb-1/release-curation',
      { batch_peak_id: 'bp-1' },
      expect.objectContaining({ type: 'release_batch_peak_curation' })
    )
    expect(app.data.peakAssignment.peak.load).toHaveBeenCalled()
    expect(app.data.batchPeak.load).toHaveBeenCalled()
    expect(outcome).toEqual({ restored: 1, skipped: 0, formula: 'C7H14O7' })
  })
})
