import { computed, onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'

import { useApp } from '@/stores'

/**
 * The key this store's entries take in `history.state`, beside vue-router's.
 */
export const HISTORY_KEY = 'mascopeLedger'

/**
 * The way back from a sample's Assignments ledger to its batch's Batch peaks
 * ledger, as a step of the browser's history.
 *
 * The two ledgers are one pane that swaps on the sample focus
 * (PaneBrowserMatch): the batch's with no sample focused, the sample's with
 * one. Going back is unfocusing the sample. The Batch peaks ledger keeps its
 * filters, its sort and its selection in stores that outlive the pane, so
 * they are as the reader left them; what this store adds is the row they came
 * from (selected and scrolled to, `reveal`) and an entry in the browser's
 * history for each step, so its Back and Forward buttons move between the two
 * ledgers as the in-app control does.
 *
 * The app keeps its address bar clean (`@/lib/location`), so an entry carries
 * no URL of its own: its state names the level, the batch, the sample and the
 * row. vue-router keeps its own fields in the same `history.state` (the
 * entry's position among them, which its popstate handler reads), so an entry
 * is written beside them, never over them.
 */
export const useBatchLedgerReturn = defineStore('browser.match.batchLedgerReturn', () => {
  const app = useApp()

  /**
   * Where the reader left the batch ledger from: the batch, the batch peak
   * whose row they opened and the sample that opened in. Null when the sample
   * ledger was reached another way.
   */
  const origin = ref(null)

  /**
   * The batch peak the Batch peaks ledger should select and scroll into view
   * when it shows next, or null. The ledger clears it once done.
   */
  const reveal = ref(null)

  const inAssignments = () => app.ui.matchMode?.mode === 'assignments'

  /**
   * Whether the sample's ledger offers the way back: a sample of a focused
   * batch is open, and that batch has a Batch peaks ledger to go back to.
   */
  const available = computed(
    () =>
      Boolean(app.data.batch.focusedId) &&
      Boolean(app.data.sample.focusedId) &&
      (app.data.batchPeak?.list?.length ?? 0) > 0
  )

  /** The row to bring back: the one the reader opened this sample from. */
  const originRow = () => {
    const from = origin.value
    return from &&
      from.sample_batch_id === app.data.batch.focusedId &&
      from.sample_item_id === app.data.sample.focusedId
      ? from.batch_peak_id
      : null
  }

  const entryOf = (state) => state?.[HISTORY_KEY] ?? null
  const replaceEntry = (entry) => {
    const state = window.history.state ?? {}
    window.history.replaceState({ ...state, [HISTORY_KEY]: entry }, '')
  }
  // The new entry continues vue-router's own bookkeeping: one position on,
  // with nothing ahead of it.
  const pushEntry = (entry) => {
    const state = window.history.state ?? {}
    window.history.pushState(
      {
        ...state,
        back: state.current ?? null,
        forward: null,
        replaced: false,
        scroll: null,
        position: (state.position ?? 0) + 1,
        [HISTORY_KEY]: entry
      },
      ''
    )
  }

  const showBatch = (batchPeakId) => {
    reveal.value = batchPeakId ?? null
    app.data.sample.unfocus()
  }

  /**
   * The batch ledger opens a sample from a row: mark the entry it leaves as
   * the batch ledger's and push one for the sample's. Call it before the
   * sample is focused.
   *
   * @param {string} batchPeakId - the row's batch peak
   * @param {string} sampleItemId - the sample it opens
   */
  function leaveFromRow(batchPeakId, sampleItemId) {
    const batchId = app.data.batch.focusedId
    if (!batchId || !sampleItemId) return
    origin.value = {
      sample_batch_id: batchId,
      batch_peak_id: batchPeakId ?? null,
      sample_item_id: sampleItemId
    }
    replaceEntry({ level: 'batch', batchId, batchPeakId: batchPeakId ?? null })
    pushEntry({
      level: 'sample',
      batchId,
      sampleId: sampleItemId,
      batchPeakId: batchPeakId ?? null
    })
  }

  /**
   * The inspector card stepped to another member of the row's batch peak:
   * the reader still came from that row, now in this sample.
   *
   * @param {string} sampleItemId - the sample the step opened
   */
  function stepped(sampleItemId) {
    if (!origin.value || !sampleItemId) return
    origin.value = { ...origin.value, sample_item_id: sampleItemId }
    const entry = entryOf(window.history.state)
    if (entry?.level === 'sample') replaceEntry({ ...entry, sampleId: sampleItemId })
  }

  /**
   * The control's way back. When the entry before this one is the batch
   * ledger the reader left from, it is the browser's own Back, so the two
   * agree; otherwise it pushes the batch ledger as a new entry, so Back
   * returns to this sample.
   */
  function back() {
    const batchId = app.data.batch.focusedId
    const sampleId = app.data.sample.focusedId
    if (!batchId || !sampleId) return
    const entry = entryOf(window.history.state)
    if (entry?.level === 'sample' && entry.batchId === batchId && entry.sampleId === sampleId) {
      window.history.back()
      return
    }
    const batchPeakId = originRow()
    replaceEntry({ level: 'sample', batchId, sampleId, batchPeakId })
    pushEntry({ level: 'batch', batchId, batchPeakId })
    showBatch(batchPeakId)
  }

  // The browser's Back or Forward onto one of this store's entries. Only in
  // the batch it was written for, and only while the browser shows the
  // assignment ledgers: elsewhere the entry names nothing on screen.
  function onPopState(event) {
    const entry = entryOf(event.state)
    if (!entry || entry.batchId !== app.data.batch.focusedId || !inAssignments()) return
    if (entry.level === 'batch') {
      if (app.data.sample.focusedId) showBatch(entry.batchPeakId)
      return
    }
    if (entry.level === 'sample') {
      const sample = app.data.sample.list.find((s) => s.sample_item_id === entry.sampleId)
      if (!sample) return
      origin.value = entry.batchPeakId
        ? {
            sample_batch_id: entry.batchId,
            batch_peak_id: entry.batchPeakId,
            sample_item_id: entry.sampleId
          }
        : null
      if (app.data.sample.focusedId !== entry.sampleId) app.data.sample.focus(sample)
    }
  }
  window.addEventListener('popstate', onPopState)
  onScopeDispose(() => window.removeEventListener('popstate', onPopState))

  return { origin, reveal, available, leaveFromRow, stepped, back }
})
