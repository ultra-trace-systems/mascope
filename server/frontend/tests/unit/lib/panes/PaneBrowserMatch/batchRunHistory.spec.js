import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { reactive } from 'vue'

// Run history opens only a run with a ledger to show: the current one, or an
// earlier one with a snapshot. A failed, cancelled or running run has none
// (batch_runs.start_run snapshots only the run current as the next starts),
// and its read answers with no rows, so it stays listed, says why, and is no
// choice.

let app
vi.mock('@/stores', () => ({ useApp: () => app }))
vi.mock('@/lib/base', () => ({
  BaseRunProvenance: { props: ['run', 'compact'], template: '<span class="provenance" />' }
}))
// The runs store's helpers are pure; the store itself is stood in for below.
vi.mock('@/lib/store', () => ({ useData: () => ({}) }))
vi.mock('@/api', () => ({ api: { http: { get: vi.fn() } } }))

const { default: BatchRunHistory } =
  await import('@/lib/panes/PaneBrowserMatch/BatchRunHistory.vue')
const { runClosedReason, runOpenable } = await import('@/stores/data/modules/batchPeak/run.js')

const run = (id, over = {}) => ({
  batch_peak_run_id: id,
  action: 'assign',
  status: 'completed',
  current: false,
  snapshot_utc: null,
  batch_peak_run_utc_created: '2026-11-16T12:00:00Z',
  ...over
})

const RUNS = [
  run('r6', { status: 'running' }),
  run('r5', { current: true }),
  run('r4', { status: 'failed' }),
  run('r3', { status: 'cancelled' }),
  run('r2', { snapshot_utc: '2026-11-16T11:00:00Z' }),
  run('r1', { action: 'fold', snapshot_utc: '2026-11-16T10:00:00Z' })
]

let wrapper

beforeEach(() => {
  app = reactive({
    data: {
      batchPeakRun: {
        list: RUNS,
        viewing: RUNS[1],
        ordinal: (r) =>
          RUNS.length - RUNS.findIndex((x) => x.batch_peak_run_id === r.batch_peak_run_id),
        focus: vi.fn(),
        backToCurrent: vi.fn()
      }
    },
    ui: { help: { docUrl: (path = '') => `/docs/${path}` } }
  })
  wrapper = mount(BatchRunHistory, { global: { directives: { help: {} } } })
})

const rowOf = (id) => wrapper.find(`.history-row[data-run="${id}"]`)

describe('BatchRunHistory', () => {
  it('opens an earlier completed run with a snapshot, and focuses it', async () => {
    const row = rowOf('r2')
    expect(row.attributes('aria-disabled')).toBeUndefined()
    expect(row.classes()).not.toContain('closed')
    expect(row.find('[data-testid="run-closed-reason"]').exists()).toBe(false)

    await row.trigger('click')
    expect(app.data.batchPeakRun.focus).toHaveBeenCalledWith(
      expect.objectContaining({ batch_peak_run_id: 'r2' })
    )
    expect(wrapper.emitted('picked')).toEqual([[RUNS[4]]])
  })

  it('opens an earlier run by Enter as by a click', async () => {
    await rowOf('r1').trigger('keydown', { key: 'Enter' })
    expect(app.data.batchPeakRun.focus).toHaveBeenCalledWith(
      expect.objectContaining({ batch_peak_run_id: 'r1' })
    )
    expect(wrapper.emitted('picked')).toHaveLength(1)
  })

  it('goes back to current on the current run, which has no snapshot', async () => {
    const row = rowOf('r5')
    expect(row.attributes('aria-disabled')).toBeUndefined()
    await row.trigger('click')
    expect(app.data.batchPeakRun.backToCurrent).toHaveBeenCalledTimes(1)
    expect(app.data.batchPeakRun.focus).not.toHaveBeenCalled()
    expect(wrapper.emitted('picked')).toEqual([[RUNS[1]]])
  })

  it.each([
    ['r4', 'Failed: it left the ledger as the run before it.'],
    ['r3', 'Cancelled: what it wrote stands in the current ledger.'],
    ['r6', 'Running: it has not finished, so it has no ledger yet.']
  ])('lists %s without a snapshot as no choice, and says why', async (id, reason) => {
    const row = rowOf(id)
    expect(row.attributes('aria-disabled')).toBe('true')
    expect(row.classes()).toContain('closed')
    expect(row.find('[data-testid="run-closed-reason"]').text()).toBe(reason)
    // Still listed as any run is: its label and state.
    expect(row.find('[data-testid="run-label"]').text()).toBe('Assignment')
    expect(row.find('.row-state').text()).not.toBe('')

    await row.trigger('click')
    await row.trigger('keydown', { key: 'Enter' })
    expect(app.data.batchPeakRun.focus).not.toHaveBeenCalled()
    expect(app.data.batchPeakRun.backToCurrent).not.toHaveBeenCalled()
    expect(wrapper.emitted('picked')).toBeUndefined()
  })

  it('opens a failed or cancelled run that has a snapshot all the same', async () => {
    // Not a run the backend makes today; the snapshot, not the status, decides.
    const kept = run('r9', { status: 'cancelled', snapshot_utc: '2026-11-16T09:00:00Z' })
    expect(runOpenable(kept)).toBe(true)
    expect(runClosedReason(kept)).toBeNull()
  })
})

describe('runClosedReason', () => {
  it('says why a completed run without a snapshot cannot be opened', () => {
    expect(runClosedReason(run('r1'))).toBe('Completed: no snapshot of its ledger was kept.')
  })

  it('has nothing to say of the current run or of a missing one', () => {
    expect(runClosedReason(run('r1', { current: true }))).toBeNull()
    expect(runClosedReason(null)).toBeNull()
    expect(runOpenable(null)).toBe(false)
  })
})
