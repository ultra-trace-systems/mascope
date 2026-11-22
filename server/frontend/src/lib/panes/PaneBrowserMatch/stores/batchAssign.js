import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { getApiErrorMessage, isRefusedRequest } from '@/api/utils'
import { canEditWorkspace } from '@/lib/permissions'
import { useApp } from '@/stores'

/**
 * Assigning a batch: where its assignment stands, and the one action that
 * runs its pass (docs/dev/batch_assignment_one_action.md, sections 1 to 3).
 *
 * The control sits in the browser's switch bar (BatchAssignControl.vue) and
 * the ledger it fills (PaneBrowserBatchPeaks.vue) is a sibling under
 * PaneBrowserMatch, whose empty state offers the same action; the two cannot
 * pass a prop, so the status, the launch and everything that outlives a click
 * live here.
 *
 * - **The status is the server's.** One read
 *   (`GET /batch-peaks/batch/{id}/assignment-status`) says where the batch
 *   stands, so two users and two tabs see the same state. It is read on batch
 *   focus, on every `peak_assignment_reload`, as any pass ends, and every
 *   half minute while one runs - the read is a dozen queries whatever the
 *   batch's size, and a dropped socket then strands nothing.
 * - **A pass is followed by its process id**, one per batch. The launch
 *   answers with the `assign_batch` process the pass reports under; its
 *   pending packets give the phase, and its end ends the wait - or a status
 *   read that finds it no longer running, where the end never arrived.
 *   Another process's packets are another pass's: they refresh the status
 *   and end nothing here.
 * - **Its end is one message at most.** The pass is claimed for this tab, so
 *   the server's own outcome is logged, not toasted. While the control is on
 *   screen a pass that ran to its end says so in place - the status line and
 *   the ledger's tier chips - and nothing else; otherwise one toast gives the
 *   change in assigned rows. A pass that stopped is a warning naming the
 *   stage, unless the person on screen asked it to stop.
 *
 * Imported by path rather than through `./stores`, as the store it replaced
 * was: it reaches the HTTP client, and the barrel is imported by panes whose
 * specs mount without one.
 */

/** The four phases the stages are grouped under, in the order they run. */
export const PHASES = [
  'Gather samples',
  'Recognise source ions',
  'Find compositions',
  'Cross-check across the batch'
]

/** The fold of every sample: a phase of its own, and no stage of the list. */
export const FOLD_STAGE = 'fold'

/** How often the status is read again while a pass runs. */
export const POLL_MS = 30 * 1000

/** How long the end of a pass waits for the ledger to reload before it counts. */
export const LEDGER_WAIT_MS = 5 * 1000

/** Why the action is disabled for someone who cannot write to the batch. */
export const VIEWER_REASON = 'Ask an editor of this workspace to assign it'

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

/** A time as the status line says it: "today 14:02", "16 Nov 14:02". */
export function whenWords(iso, now = new Date()) {
  if (!iso) return ''
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const time = at.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  if (at.toDateString() === now.toDateString()) return `today ${time}`
  const day = at.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  return `${day} ${time}`
}

/** A change in assigned rows, as the status line and the toast say it. */
export function deltaWords(delta) {
  if (delta == null) return ''
  if (delta === 0) return 'no change in assigned rows'
  return `${delta > 0 ? '+' : '−'}${Math.abs(delta)} assigned`
}

/** A stage's label in the status read, the fold included. */
export function stageLabel(status, stage) {
  if (!stage) return ''
  if (stage === FOLD_STAGE) return 'Fold the samples'
  return status?.stages?.find((entry) => entry.stage === stage)?.label ?? stage
}

/** The phase a stage belongs to, by the status read. */
export function stagePhase(status, stage) {
  if (stage === FOLD_STAGE) return PHASES[0]
  return status?.stages?.find((entry) => entry.stage === stage)?.phase ?? null
}

/** One reason the batch is behind its last pass, in the user's words. */
export function behindWords(reason, status) {
  const count = reason?.count ?? 0
  switch (reason?.reason) {
    case 'new_samples':
      return `${plural(count, 'sample')} added since the last assignment`
    case 'removed_samples':
      return `${plural(count, 'sample')} removed since the last assignment`
    case 'resampled':
      return `${plural(count, 'sample')} assigned again on their own since the last assignment`
    case 'library_changed':
      return 'Target collections or reference lists changed since the last assignment'
    case 'engine_changed': {
      const version = reason.detail?.pass ?? status?.last_pass?.engine_version
      return version ? `Assigned by an older engine (${version})` : 'Assigned by an older engine'
    }
    case 'stages_stale': {
      const first = Array.isArray(reason.detail) ? reason.detail[0]?.stage : null
      return first ? `${stageLabel(status, first)} is out of date` : 'Some stages are out of date'
    }
    default:
      return 'Changed since the last assignment'
  }
}

/**
 * The body of a pass that brings the batch up to date: from the first stage
 * that is not current, folding where the server's rule says to. Where nothing
 * is stale and nothing needs folding the server would answer that there is
 * nothing to run, so a re-assignment of a batch that is up to date names the
 * first stage instead and runs every stage again.
 */
export function passBody(status) {
  if (status?.stale_stages?.length || status?.needs_fold) return {}
  const first = status?.stages?.[0]?.stage
  return first ? { from_stage: first } : {}
}

/** The body of a retry: the pass again from the stage it stopped at. */
export function retryBody(status) {
  const at = status?.stopped_pass?.stopped_at
  if (!at) return {}
  if (at === FOLD_STAGE) return { fold: 'always' }
  return { from_stage: at }
}

export const useBatchAssign = defineStore('browser.match.batchPeaks.assign', () => {
  const app = useApp()

  // --- The status read -------------------------------------------------------

  const status = ref(null)
  // The batch the status was read for, which lags the focus while a read for
  // the next batch is out.
  const statusBatchId = ref(null)
  const statusError = ref(null)
  const statusPending = ref(false)
  let readSeq = 0

  /** Read one batch's status; null where the read failed. */
  async function readStatus(batchId) {
    return api.http.get(`/batch-peaks/batch/${batchId}/assignment-status`, {
      use: 'read',
      type: 'load_batch_assignment_status',
      errors: 'inline'
    })
  }

  /**
   * Read the focused batch's status and answer what it says, whatever reads
   * start meanwhile. Only the newest read writes `status`: an older one may
   * land after it. Throws where the read failed.
   */
  async function readFocused(batchId) {
    const seq = ++readSeq
    statusPending.value = true
    try {
      const data = (await readStatus(batchId)) ?? null
      if (seq === readSeq) {
        status.value = data
        statusBatchId.value = batchId
        statusError.value = null
        settle(batchId, data)
      }
      return data
    } catch (error) {
      if (seq === readSeq) {
        statusError.value = getApiErrorMessage(error, 'Could not read where the batch stands.')
      }
      throw error
    } finally {
      if (seq === readSeq) statusPending.value = false
    }
  }

  /** Read the focused batch's status again; answers the newest status held. */
  async function loadStatus() {
    const batchId = app.data.batch.focusedId
    if (!batchId) {
      status.value = null
      statusBatchId.value = null
      return null
    }
    try {
      await readFocused(batchId)
    } catch {
      // Reported in `statusError`; the status held stays on screen.
    }
    return status.value
  }

  /** The focused batch's status, or null while it has not been read. */
  const current = computed(() =>
    statusBatchId.value && statusBatchId.value === app.data.batch.focusedId ? status.value : null
  )
  const state = computed(() => current.value?.state ?? null)

  // Every half minute while a pass runs, whoever's: the status is what says
  // it ended, should its packets not arrive.
  let pollTimer = null
  function stopPolling() {
    clearInterval(pollTimer)
    pollTimer = null
  }
  watch(
    () => state.value === 'running',
    (running) => {
      stopPolling()
      if (running) pollTimer = setInterval(loadStatus, POLL_MS)
    }
  )

  // A fold, a rebuild, a stage that rewrote members, an import, a pass's
  // end: anything that reloads the ledger can move where the batch stands.
  // The event names no batch (a `RecordEvent`), so every one is read again.
  api.socket?.on?.('peak_assignment_reload', () => loadStatus())

  // --- Who may run it ----------------------------------------------------------

  // Writing the batch needs the editor role on its workspace, the focused one.
  // The helper answers "yes" while the account or the workspace still loads.
  const canAssign = computed(() => canEditWorkspace(app.data.workspace.focused, app.auth.user))

  /** Why a pass cannot be started, or null when it can. */
  const blockedReason = computed(() => {
    if (!app.data.batch.focusedId) return 'Select a batch to assign it.'
    if (!canAssign.value) return VIEWER_REASON
    if (!app.data.sample.pending && !app.data.sample.list.length) {
      return 'This batch has no samples yet, so there is nothing to assign.'
    }
    return null
  })

  // --- The passes this tab started -------------------------------------------

  // By batch, one at most each, since a batch runs one pass at a time: a pass
  // on one batch holds nothing back on another. Each is its process id, its
  // run, what the ledger assigned as it started (to say what it changed), and
  // the phase its newest packet named - ahead of the status read, which is
  // refreshed only as stages that rewrite members end.
  const passes = ref({})
  // The launches still waiting for their 202, by batch.
  const launchingIds = ref({})
  // The runs asked to stop from here, by run id: also a pass this tab did not
  // launch - yours by `started_by`, after a reload or from another tab. By
  // run, so a stop asked of one pass is never the next one's.
  const stopRuns = ref({})
  const launchError = ref(null)
  const launchRefused = ref(false)
  // The change each observed pass made, by its run, for the status line.
  const deltas = ref({})

  const without = (map, key) => {
    const next = { ...map }
    delete next[key]
    return next
  }

  const focusedPass = computed(() => passes.value[app.data.batch.focusedId] ?? null)
  const launching = computed(() => Boolean(launchingIds.value[app.data.batch.focusedId]))
  const pendingProcessId = computed(() => focusedPass.value?.processId ?? null)
  const progress = computed(() => focusedPass.value?.progress ?? null)

  /** The run in flight on the focused batch was asked to stop from here. */
  const cancelling = computed(() => {
    const runId = current.value?.running?.batch_peak_run_id
    return Boolean(runId && stopRuns.value[runId])
  })

  /**
   * What the newest status read of a batch says of the pass this tab follows
   * there. The route opens the pass's run before it answers, so a read made
   * since that finds no pass running, or another run, means the pass ended -
   * also where its end packet never arrived, as after a dropped socket - and
   * it ends here. A read that started before the launch was answered is never
   * the newest: the launch reads again.
   */
  function settle(batchId, data) {
    const pass = passes.value[batchId]
    if (!pass || !data) return
    if (data.state !== 'running' || data.running?.batch_peak_run_id !== pass.runId) {
      finishPass(batchId, null)
    }
  }

  // Controls on screen that report a pass's end in place.
  const onScreen = ref(0)
  /** A control that shows the status is mounted; returns its release. */
  function attach() {
    onScreen.value += 1
    let attached = true
    return () => {
      if (!attached) return
      attached = false
      onScreen.value -= 1
    }
  }

  /** The batch peaks the ledger assigns, isotope lines aside; null when unknown. */
  function assignedCount(batchId) {
    const ledger = app.data.batchPeak
    if (!ledger || ledger.pending || ledger.error) return null
    if (app.data.batch.focusedId !== batchId) return null
    if (app.data.batchPeakRun && app.data.batchPeakRun.viewingCurrent === false) return null
    return (ledger.list ?? []).filter(
      (row) => row.consensus_tier === 'assigned' && !row.line_owner_id
    ).length
  }

  /**
   * Start a pass on the focused batch.
   *
   * @param {object} body What the pass is asked for: `from_stage`, `fold`.
   */
  async function assign(body = {}) {
    const batchId = app.data.batch.focusedId
    if (!batchId || blockedReason.value) return
    if (launchingIds.value[batchId] || passes.value[batchId]) return
    if (state.value === 'running') return
    launchingIds.value = { ...launchingIds.value, [batchId]: true }
    launchError.value = null
    launchRefused.value = false
    const before = assignedCount(batchId)
    try {
      // No `use` handler: the process id rides on the `Process-ID` header,
      // which the handlers throw away with the rest of the response.
      const response = await api.http.post(`/batch-peaks/batch/${batchId}/assign`, body, {
        type: 'assign_batch',
        errors: 'inline'
      })
      const processId = response?.headers?.['process-id'] ?? null
      const run = response?.data?.data?.[0] ?? null
      launchingIds.value = without(launchingIds.value, batchId)
      if (processId) {
        passes.value = {
          ...passes.value,
          [batchId]: {
            processId,
            runId: run?.batch_peak_run_id ?? null,
            before,
            progress: null
          }
        }
        app.ui.notification.claim(processId)
      }
      // The route opened the run, so the status reads it running already;
      // with nothing to run it reads the batch as it was.
      await loadStatus()
    } catch (error) {
      launchingIds.value = without(launchingIds.value, batchId)
      launchRefused.value = isRefusedRequest(error) || error?.response?.status === 403
      launchError.value = getApiErrorMessage(error, 'Could not start the batch assignment.')
      // A 409 is someone else's pass: the status says whose.
      loadStatus()
    }
  }

  /** Ask the running pass to stop, at its next stage. */
  async function cancel() {
    const batchId = app.data.batch.focusedId
    if (!batchId || cancelling.value) return
    const runId = current.value?.running?.batch_peak_run_id ?? null
    stopRuns.value = { ...stopRuns.value, [runId]: true }
    try {
      await api.http.post(
        `/batch-peaks/batch/${batchId}/assign/cancel`,
        {},
        { type: 'cancel_assign_batch', errors: 'inline' }
      )
    } catch (error) {
      stopRuns.value = without(stopRuns.value, runId)
      // A 404 is a pass that ended first: the status read says how.
      if (error?.response?.status !== 404) {
        launchRefused.value = isRefusedRequest(error) || error?.response?.status === 403
        launchError.value = getApiErrorMessage(error, 'Could not stop the batch assignment.')
      }
    }
    await loadStatus()
  }

  /** The next list the ledger loads, or the one it has after `ms`. */
  function nextLedgerList(ms) {
    return new Promise((resolve) => {
      let stop = null
      const timer = setTimeout(() => {
        stop?.()
        resolve()
      }, ms)
      stop = watch(
        () => app.data.batchPeak?.list,
        () => {
          clearTimeout(timer)
          stop?.()
          resolve()
        }
      )
    })
  }

  function toast(status, message) {
    app.ui.notification.push({ type: 'batch_assignment', status, message })
  }

  /**
   * The pass this tab followed on a batch has ended: say how, once.
   *
   * @param {string} batchId The batch it ran on.
   * @param {object|null} notification Its end packet; null where a status read
   *   found it ended instead.
   */
  async function finishPass(batchId, notification) {
    const pass = passes.value[batchId]
    if (!pass) return
    const askedToStop = Boolean(stopRuns.value[pass.runId])
    passes.value = without(passes.value, batchId)
    const focused = app.data.batch.focusedId === batchId
    const shown = onScreen.value > 0 && focused
    // The ledger reloads after the pass reports, and may do so while the
    // status is read, so it is watched from here.
    const ledgerLoaded = focused && pass.before != null ? nextLedgerList(LEDGER_WAIT_MS) : null
    // The status names the stage a pass stopped at. Read for what it says
    // now: the `peak_assignment_reload` sent right after the end packet
    // starts a newer read, which must not hand this one the status from
    // before the end.
    let read
    try {
      read = focused ? await readFocused(batchId) : await readStatus(batchId)
    } catch {
      read = null
    }
    const stopped =
      notification?.status === 'error' || read?.state === 'failed' || read?.state === 'cancelled'
    if (stopped) {
      if (askedToStop && shown) return
      const at = read?.stopped_pass?.stopped_at
      toast(
        'warning',
        at
          ? `Stopped at ${stageLabel(read, at)}: the stages before it were applied.`
          : 'The batch assignment stopped before its first stage.'
      )
      return
    }
    // Ran to its end: its change is counted once the ledger's reload lands,
    // or on what the ledger holds then.
    let delta = null
    if (ledgerLoaded) {
      await ledgerLoaded
      const after = assignedCount(batchId)
      if (after != null) delta = after - pass.before
    }
    // By the pass's own run: the 202 named it, and a status read may lag.
    if (delta != null) deltas.value = { ...deltas.value, [pass.runId]: delta }
    if (shown) return
    const samples = read?.last_pass?.n_samples ?? read?.n_samples
    const parts = [
      samples != null ? `${plural(samples, 'sample')}` : null,
      delta != null ? deltaWords(delta) : null
    ].filter(Boolean)
    const still = notification?.status === 'warning' ? ' Some stages are still out of date.' : ''
    toast(
      notification?.status === 'warning' ? 'warning' : 'success',
      `Assigned the batch${parts.length ? `: ${parts.join(', ')}` : ''}.${still}`
    )
  }

  // The passes' packets, by process id. Pending ones name the stage; the end
  // is the signal that a pass finished - the 202 says only that it started,
  // and a status read stands in where the end never arrives. Registered in
  // the store's scope, so a pass started before the control was unmounted (a
  // sample focused mid-pass) still ends here.
  let otherStage = null
  app.ui.notification.on('assign_batch', (notification) => {
    const id = notification?.process_id
    const batchId = id
      ? Object.keys(passes.value).find((key) => passes.value[key].processId === id)
      : undefined
    const data = notification?.data ?? {}
    if (notification?.status === 'pending') {
      if (batchId) {
        passes.value = {
          ...passes.value,
          [batchId]: {
            ...passes.value[batchId],
            progress: {
              stage: data.stage ?? null,
              phase: data.phase ?? null,
              stage_index: data.stage_index ?? null,
              n_stages: data.n_stages ?? null
            }
          }
        }
        return
      }
      // Another pass on this batch moved on a stage: say so.
      if (data.sample_batch_id && data.sample_batch_id === app.data.batch.focusedId) {
        const key = `${id}:${data.stage_index}`
        if (key !== otherStage) {
          otherStage = key
          loadStatus()
        }
      }
      return
    }
    if (batchId) {
      finishPass(batchId, notification)
      return
    }
    if (!data.sample_batch_id || data.sample_batch_id === app.data.batch.focusedId) loadStatus()
  })

  // --- What the control shows ------------------------------------------------

  /** The running pass is this person's: theirs to cancel. */
  const runningIsMine = computed(() => {
    // A batch runs one pass at a time, so while this tab's pass on the
    // focused batch has not ended, the one running is it - also before its
    // first stage, when the status names no process yet.
    if (focusedPass.value) return true
    const running = current.value?.running
    if (!running) return false
    const me = app.auth.user?.id
    return me != null && running.started_by?.id === me && running.stage != null
  })

  /** The phase the running pass is in, and which of the four. */
  const runningPhase = computed(() => {
    const st = current.value
    const live = runningIsMine.value ? progress.value : null
    const phase = live?.phase ?? stagePhase(st, st?.running?.stage)
    const index = PHASES.indexOf(phase)
    return phase && index !== -1 ? { phase, step: index + 1 } : null
  })

  /**
   * What the control says and offers, from the status read: a tone for the
   * dot (`neutral`, `accent`, `ok`, `warn`, `busy`), the status line and the
   * line under it, and the action - its label, whether it is filled, and the
   * body it posts - or null for none.
   */
  const view = computed(() => {
    const st = current.value
    if (!st) {
      return { state: null, tone: 'neutral', line: '', detail: null, action: null }
    }
    const assignAction = (label, filled, body = {}) => ({ kind: 'assign', label, filled, body })
    switch (st.state) {
      case 'none':
        return {
          state: 'none',
          tone: 'neutral',
          line: 'Not assigned',
          detail: null,
          action: assignAction('Assign batch', true, passBody(st))
        }
      case 'folded_only':
        // Every batch ingest builds starts here: neutral, not the accent of
        // `behind`, which would mark nearly every batch at once.
        return {
          state: 'folded_only',
          tone: 'neutral',
          line: 'Known compositions only',
          detail: 'The untargeted search and the batch checks have not run.',
          action: assignAction('Assign batch', true, passBody(st))
        }
      case 'running': {
        const running = st.running ?? {}
        const phase = runningPhase.value
        const step = phase ? `Step ${phase.step} of ${PHASES.length}` : null
        if (runningIsMine.value) {
          const stopping = cancelling.value || Boolean(running.cancel_requested_utc)
          return {
            state: 'running',
            tone: 'busy',
            line: phase ? `Assigning the batch · ${phase.phase}` : 'Assigning the batch',
            detail: step,
            action: {
              kind: 'cancel',
              label: stopping ? 'Stopping…' : 'Cancel',
              filled: false,
              disabled: stopping
            }
          }
        }
        const started = whenWords(running.utc_started)
        return {
          state: 'running',
          tone: 'busy',
          line:
            running.stage != null || running.action === 'assign'
              ? 'Another user is assigning this batch'
              : 'Another batch run is rewriting the ledger',
          detail: [step, started ? `started ${started}` : null].filter(Boolean).join(' · ') || null,
          action: null
        }
      }
      case 'current': {
        const last = st.last_pass ?? {}
        const delta = deltas.value[last.batch_peak_run_id]
        const detail = [
          last.n_samples != null ? plural(last.n_samples, 'sample') : null,
          delta != null ? deltaWords(delta) : null
        ]
          .filter(Boolean)
          .join(' · ')
        return {
          state: 'current',
          tone: 'ok',
          line: `Assigned ${whenWords(last.utc_completed ?? last.utc_started)} · up to date`,
          detail: detail || null,
          action: assignAction('Re-assign', false, passBody(st))
        }
      }
      case 'behind': {
        const reasons = st.behind ?? []
        const onlyNew = reasons.length > 0 && reasons.every((r) => r.reason === 'new_samples')
        const more = reasons.length - 1
        return {
          state: 'behind',
          tone: 'accent',
          line: reasons.length ? behindWords(reasons[0], st) : 'Changed since the last assignment',
          detail: more > 0 ? `and ${plural(more, 'other change')}` : null,
          action: assignAction(onlyNew ? 'Update assignment' : 'Re-assign', true, passBody(st))
        }
      }
      case 'failed':
      case 'cancelled': {
        const at = st.stopped_pass?.stopped_at
        return {
          state: st.state,
          tone: 'warn',
          line: at ? `Stopped at ${stageLabel(st, at)}` : 'Stopped before its first stage',
          detail: at ? 'The stages before it were applied.' : null,
          action: assignAction('Retry', true, retryBody(st))
        }
      }
      default:
        return { state: st.state, tone: 'neutral', line: st.state, detail: null, action: null }
    }
  })

  /**
   * Whether the ledger's empty state offers Assign batch: the batch is not
   * assigned (or its status not read yet), the live ledger is loaded and holds
   * no batch peaks, and no pass of this tab's is out. The bar hides its own
   * copy then, so one button is on screen, as AssignmentRunBar does for a
   * sample. One computed read by both, so the two cannot both show or both
   * hide. A pass that stopped keeps its Retry in the bar, also over a ledger
   * its fold left empty.
   */
  const ledgerOffersAssign = computed(() => {
    if (state.value !== 'none' && state.value !== null) return false
    const ledger = app.data.batchPeak
    if (!ledger || ledger.pending || ledger.error) return false
    if (app.data.batchPeakRun && app.data.batchPeakRun.viewingCurrent === false) return false
    if ((ledger.list ?? []).length) return false
    return !focusedPass.value && !launching.value
  })

  // --- The ledger export -------------------------------------------------------

  // A background task whose completion notification carries the file the
  // browser then downloads (App.vue), so this only keeps the control busy for
  // as long as the task runs. Moved here unchanged from the store this one
  // replaced.
  const EXPORT_TIMEOUT = 5 * 60 * 1000
  const exporting = ref(false)
  const pendingExportId = ref(null)
  let exportTimer = null
  function endExporting() {
    exporting.value = false
    pendingExportId.value = null
    clearTimeout(exportTimer)
    exportTimer = null
  }
  async function exportLedger() {
    const batchId = app.data.batch.focusedId
    if (!batchId || exporting.value) return
    exporting.value = true
    clearTimeout(exportTimer)
    exportTimer = setTimeout(endExporting, EXPORT_TIMEOUT)
    try {
      const response = await api.http.post(
        `/batch-peaks/batch/${batchId}/export`,
        {},
        { use: 'process', type: 'export_batch_ledger' }
      )
      pendingExportId.value = response?.headers?.['process-id'] ?? null
    } catch {
      endExporting()
    }
  }
  app.ui.notification.on('export_batch_ledger', (notification) => {
    if (!exporting.value) return
    if (notification?.status === 'pending') return
    const id = notification?.process_id
    if (pendingExportId.value && id && id !== pendingExportId.value) return
    endExporting()
  })

  // --- The batch changes -------------------------------------------------------

  /** What was shown or refused belonged to the previous batch. */
  function reset() {
    endExporting()
    launchError.value = null
    launchRefused.value = false
    otherStage = null
    // A pass already running is followed on: its end still reports, as a
    // toast, since the control that asked is no longer showing its batch.
  }

  watch(
    () => app.data.batch.focusedId,
    () => {
      reset()
      loadStatus()
    },
    { immediate: true }
  )

  return {
    status: current,
    state,
    statusError,
    statusPending,
    loadStatus,
    canAssign,
    blockedReason,
    launching,
    pendingProcessId,
    cancelling,
    progress,
    launchError,
    launchRefused,
    assign,
    cancel,
    attach,
    runningIsMine,
    runningPhase,
    view,
    ledgerOffersAssign,
    exporting,
    exportLedger,
    reset
  }
})
