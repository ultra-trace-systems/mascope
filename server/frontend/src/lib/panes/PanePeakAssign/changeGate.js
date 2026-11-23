import { computed } from 'vue'

import { useApp } from '@/stores'
import { canEditWorkspace } from '@/lib/permissions'
import { runNotice } from './card/cardReadings.js'

/**
 * Whether a verdict or a change by hand may be recorded on the run on view,
 * and why not when it may not (docs/dev/assignment_ledger_and_card.md, 4.2).
 *
 * One rule for every place that writes onto the focused peak's row: the
 * inspector card (`card/useAssignmentCard.js`) and the composition search's
 * hand button (`PanePeakSearch.vue`). The run on view is the one the run
 * store focuses, which the run selector can set to an earlier run, a newer one
 * that has not completed, the batch ledger or, asked for, a decoy. None of
 * those is the sample's result, so nothing is recorded on it (`readOnly`, with
 * the notice saying which run it is and why). Whether the account may write in
 * the workspace at all is checked before anything is offered, as the ledger's
 * verdict popovers check it (`canEdit`); the 403 a refused write earns stays
 * each caller's fallback.
 *
 * @returns {{
 *   viewedRun: import('vue').ComputedRef<object|null>,
 *   notice: import('vue').ComputedRef<ReturnType<typeof runNotice>>,
 *   readOnly: import('vue').ComputedRef<boolean>,
 *   readOnlyReason: import('vue').ComputedRef<string>,
 *   canEdit: import('vue').ComputedRef<boolean>,
 *   canChange: import('vue').ComputedRef<boolean>
 * }}
 */
export function useChangeGate() {
  const app = useApp()

  const viewedRun = computed(() => app.data.peakAssignment.peak.run ?? null)
  const notice = computed(() =>
    runNotice(
      viewedRun.value,
      app.data.peakAssignment.run?.latestCompleted ?? null,
      app.data.peakAssignment.run?.list ?? []
    )
  )
  const readOnly = computed(() => notice.value != null)
  const readOnlyReason = computed(() => notice.value?.reason ?? '')

  const canEdit = computed(() =>
    canEditWorkspace(app.data.workspace?.focused ?? null, app.auth?.user ?? null)
  )
  // An editor, on the sample's own result.
  const canChange = computed(() => canEdit.value && !readOnly.value)

  return { viewedRun, notice, readOnly, readOnlyReason, canEdit, canChange }
}
