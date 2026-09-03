import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

import { getApiErrorMessage, isRefusedRequest } from '@/api/utils'
import { canEditWorkspace } from '@/lib/permissions'
import { api } from '@/api'
import { useApp } from '@/stores'

/**
 * Building / refreshing the batch peaks, for the button that launches it.
 *
 * The button sits in the browser's switch bar (BatchPeakComputeBar.vue), beside
 * the Targets/Assignments switch and in the place the sample level puts
 * "Assign peaks" - while the ledger it fills (PaneBrowserBatchPeaks.vue) is a
 * sibling under PaneBrowserMatch. The two cannot pass a prop, so the launch and
 * everything that outlives a click live here.
 *
 * Imported by path rather than through `./stores`: this is the only store in
 * that folder that reaches the HTTP client, and the barrel is imported by panes
 * whose specs mount without one. Keeping it out means importing it stays a
 * deliberate act by the two files that launch and report the compute.
 *
 * More than the flag `assignmentLauncher` shares, because there is more to
 * share: this button launches directly rather than opening a dialog, so its
 * disabled reason, its loading state and the refusal it may come back with are
 * all the button's own - only the refusal is rendered by the ledger, below the
 * table it explains.
 */
export const useBatchPeakCompute = defineStore('browser.match.batchPeaks.compute', () => {
  const app = useApp()

  // Cleared on the notification the background task sends when it ends. A
  // dropped socket would otherwise strand the button in its loading state
  // forever, which is a worse failure than the premature reset this replaced,
  // so the wait is bounded. Generous: the backfill folds every sample of the
  // batch in turn.
  const COMPUTE_TIMEOUT = 5 * 60 * 1000

  const computing = ref(false)
  const pendingProcessId = ref(null)
  let computeTimer = null

  const launchError = ref(null)
  const launchRefused = ref(false)

  // Writing batch peaks needs the editor role on the batch's workspace, which is
  // the focused one - datasets, and so batches, load per focused workspace. The
  // helper answers "yes" while the account or the workspace is still loading, so
  // a slow load offers the button rather than hiding a capability the user has.
  const canCompute = computed(() => canEditWorkspace(app.data.workspace.focused, app.auth.user))

  // Why the button cannot run right now, or null when it can. One computed rather
  // than a chain of conditions on the button, so what is disabled and the reason
  // shown for it cannot disagree.
  //
  // "No completed assignment runs in this batch" is the condition that actually
  // matters and it is not knowable here - no loaded record carries a per-sample
  // run status - so the honest client-side stand-in is the batch having no
  // samples at all. A batch that has samples but no completed runs still reaches
  // the backend, which now reports folding nothing as a warning instead of
  // announcing it green.
  const blockedReason = computed(() => {
    if (!app.data.batch.focusedId) return 'Select a batch to rebuild its ledger.'
    if (!canCompute.value) {
      return 'Rebuilding the batch ledger writes to the batch, so it needs the editor role in this workspace.'
    }
    if (!app.data.sample.pending && !app.data.sample.list.length) {
      return 'This batch has no samples yet, so there is nothing to fold into the ledger.'
    }
    return null
  })

  const computeTooltip = computed(
    () =>
      blockedReason.value ??
      'Rebuild the batch ledger from every sample of the batch - from its assignment run where it has one, otherwise assigned from the known compositions.'
  )

  // The untargeted search over the batch peaks nothing has assigned yet: once
  // per species, on its brightest peak, then measured against every other
  // sample the species was seen in. Same gate as the compute, same shape of
  // wait: an acknowledgement is not completion, the task's own notification is.
  const searching = ref(false)
  const pendingSearchId = ref(null)
  let searchTimer = null
  const searchTooltip = computed(
    () =>
      blockedReason.value ??
      'Search untargeted compositions for the batch peaks nothing has assigned yet - once per species, on its brightest peak.'
  )

  function endComputing() {
    computing.value = false
    pendingProcessId.value = null
    clearTimeout(computeTimer)
    computeTimer = null
  }

  function endSearching() {
    searching.value = false
    pendingSearchId.value = null
    clearTimeout(searchTimer)
    searchTimer = null
  }

  async function searchUntargeted() {
    const batchId = app.data.batch.focusedId
    if (!batchId || searching.value || blockedReason.value) return
    searching.value = true
    launchError.value = null
    clearTimeout(searchTimer)
    searchTimer = setTimeout(endSearching, COMPUTE_TIMEOUT)
    try {
      const response = await api.http.post(
        `/batch-peaks/batch/${batchId}/search-untargeted`,
        {},
        { type: 'search_batch_untargeted', errors: 'inline' }
      )
      pendingSearchId.value = response?.headers?.['process-id'] ?? null
    } catch (error) {
      endSearching()
      launchRefused.value = isRefusedRequest(error) || error?.response?.status === 403
      launchError.value = getApiErrorMessage(error, 'Could not start the untargeted search.')
    }
  }

  /** Backfill batch peaks from this batch's existing assignments; the ledger and
   *  chart refresh on the peak_assignment_reload event the task emits. */
  async function compute() {
    const batchId = app.data.batch.focusedId
    if (!batchId || computing.value || blockedReason.value) return
    computing.value = true
    launchError.value = null
    clearTimeout(computeTimer)
    computeTimer = setTimeout(endComputing, COMPUTE_TIMEOUT)
    try {
      // No `use` handler: the acknowledgement's process id rides on the
      // `Process-ID` response header (the route pops it out of the body), and
      // both the `read` and `process` handlers throw the raw response away. The
      // id is what tells this pane's completion notification apart from someone
      // else's backfill of the same batch, which lands in the same socket room.
      const response = await api.http.post(
        `/batch-peaks/batch/${batchId}/backfill`,
        {},
        // `errors: 'inline'` holds back the interceptor's toast: the failure is
        // reported once, in the ledger below.
        { type: 'backfill_batch_peaks', errors: 'inline' }
      )
      pendingProcessId.value = response?.headers?.['process-id'] ?? null
    } catch (error) {
      // The launch was refused or failed, so nothing is running and the button
      // goes back to offering the action rather than pretending to perform it.
      endComputing()
      // A refusal is shown as a warning rather than an error: the server decided
      // this on purpose and said why. 403 counts as one here on top of the shared
      // helper's 409/422 - it is the refusal this route actually issues, from the
      // editor-role check and the feature flag, and it is what a role revoked
      // mid-session looks like. Anything else is a fault.
      launchRefused.value = isRefusedRequest(error) || error?.response?.status === 403
      launchError.value = getApiErrorMessage(error, 'Could not start the batch ledger rebuild.')
    }
  }

  // The task's own notification is the only signal that the work finished - the
  // 202 says only that it started. It is named for the controller that emits it
  // (compute_batch_peaks), not for the request that launched it, and it arrives
  // for a failure as well as a success, so the button leaves its loading state
  // either way.
  //
  // Registered in the store's scope rather than a pane's, which is what moving
  // the launch out of the ledger costs and buys: the listener now outlives any
  // one mount, so a compute still stops spinning if the user focuses a sample
  // mid-run and comes back to the batch ledger afterwards.
  app.ui.notification.on('compute_batch_peaks', (notification) => {
    if (!computing.value) return
    // The task reports per sample as it folds the batch, and those packets say
    // only how far along it is - the app's progress bar is what renders them.
    // The button is asking a different question, "is it still running", and the
    // answer to that is still yes.
    if (notification?.status === 'pending') return
    // A packet whose id we can read and that is not ours belongs to someone
    // else's backfill of this batch. One we cannot identify is accepted rather
    // than ignored: leaving the button spinning would be the worse guess.
    const id = notification?.process_id
    if (pendingProcessId.value && id && id !== pendingProcessId.value) return
    endComputing()
  })

  /** Whatever was in flight or went wrong belonged to the previous batch. */
  app.ui.notification.on('search_batch_untargeted', (notification) => {
    if (!searching.value) return
    if (notification?.status === 'pending') return
    const id = notification?.process_id
    if (pendingSearchId.value && id && id !== pendingSearchId.value) return
    endSearching()
  })

  function reset() {
    endComputing()
    endSearching()
    launchError.value = null
    launchRefused.value = false
  }

  return {
    computing,
    blockedReason,
    computeTooltip,
    launchError,
    launchRefused,
    compute,
    searching,
    searchTooltip,
    searchUntargeted,
    reset
  }
})
