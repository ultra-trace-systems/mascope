import { computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { peakAssignmentEnabled } from '@/lib/features'

import { useSample } from '../sample'

// Assignment VERIFICATIONS (labelling) for the focused sample.
//
// The GET returns the append-only verdict history (newest first). The CURRENT
// verdict for an assignment is the latest record sharing its stable identity
// `sample_peak_id | assigned_formula | ionization_mechanism_id` -- NOT
// peak_assignment_id, which is regenerated on every assignment run, so a verdict
// made against a prior run still lights up this run's matching assignment.
// See docs/dev/verification_capture_frontend.md.
export const usePeakAssignmentVerification = defineStore(
  'app.data.peakAssignment.verification',
  () => {
    const name = 'assignment_verification'
    const key = 'assignment_verification_id'

    const data = useData(
      name,
      ({ sample_item_id }) => {
        if (!sample_item_id) return []
        return api.http.get(`/peak-assignments/sample/${sample_item_id}/verifications`, {
          use: 'read',
          type: 'load_assignment_verifications'
        })
      },
      {
        key,
        // Opt-in feature, always-instantiated store: with the flag off the
        // dependency is unmet, so the loader never calls the verifications
        // endpoint (see run.js for the same gate).
        deps: () => ({
          sample_item_id: peakAssignmentEnabled ? useSample().focusedId : null
        })
      }
    )

    const identityOf = (record) =>
      `${record.sample_peak_id}|${record.assigned_formula ?? ''}|${record.ionization_mechanism_id ?? ''}`

    // Current (latest) verdict per stable identity. The list is newest-first, so
    // the first record seen for an identity is the current one.
    const currentByIdentity = computed(() => {
      const map = new Map()
      for (const record of data.list.value) {
        const id = identityOf(record)
        if (!map.has(id)) map.set(id, record)
      }
      return map
    })

    // Current verdict record for a given assignment (peak store row), or null.
    const forAssignment = (assignment) =>
      assignment ? (currentByIdentity.value.get(identityOf(assignment)) ?? null) : null

    // Record a verdict, then refetch so the badge + ledger filter reflect it.
    // Rejects (and auto-toasts) on 4xx via the shared http error handler; the
    // caller inspects the error status (e.g. 403 non-editor) for inline UI.
    async function verify(body) {
      const sample_item_id = useSample().focusedId
      if (!sample_item_id) return null
      const response = await api.http.post(
        `/peak-assignments/sample/${sample_item_id}/verify`,
        body,
        { use: 'create', type: 'verify_assignment' }
      )
      // sync() records a failed refetch rather than rejecting, so re-raise it
      // here: the caller treats a resolved verify() as "saved and visible", and
      // would otherwise close the form over a badge that never refreshed.
      //
      // Read the outcome this call returns, not the store's `error` ref: the ref
      // belongs to whichever sync wrote it last, so a deps-driven reload landing
      // in between would either hand us a failure that is not ours or hide one
      // that is. A superseded refetch reports no error - the newer sync owns the
      // list and its own error surface by then.
      const outcome = await data.load('verification')
      if (outcome.error) throw outcome.error
      return response?.data?.[0] ?? null
    }

    return { ...data, currentByIdentity, forAssignment, verify }
  }
)
