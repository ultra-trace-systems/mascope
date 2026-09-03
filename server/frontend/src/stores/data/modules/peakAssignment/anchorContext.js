import { computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { peakAssignmentEnabled } from '@/lib/features'

import { useSample } from '../sample'
import { usePeakAssignment } from './assignment'

/**
 * The batch-level verdicts that reach the focused sample.
 *
 * One sparse fetch per sample: for each of its peaks whose batch peak carries a
 * live verdict, that verdict with the peak's id on it. Whether it *applies* to a
 * row is decided here, by the precedence the continuity note sets out. A row's
 * own per-sample verdict wins - the verification store answers that, and the
 * panes ask it first. Otherwise the batch-level verdict applies iff the family
 * M0's peak folded into the judged batch peak and the judgment is about the M0's
 * own claim - formula and mechanism, null-safe - so a dissenting sample gets no
 * overlay from a verdict about another formula. Isotopologues resolve through
 * their M0, as a verdict does everywhere else.
 */
export const usePeakAssignmentAnchorContext = defineStore(
  'app.data.peakAssignment.anchorContext',
  () => {
    const name = 'anchor_context'
    const key = 'batch_peak_verification_id'

    const data = useData(
      name,
      ({ sample_item_id }) => {
        if (!sample_item_id) return []
        return api.http.get(`/batch-peaks/sample/${sample_item_id}/anchor-context`, {
          use: 'read',
          type: 'load_anchor_context'
        })
      },
      {
        key,
        deps: () => ({
          sample_item_id: peakAssignmentEnabled ? useSample().focusedId : null
        }),
        // A fold or a run changes which batch peak a peak sits in.
        events: ['peak_assignment_reload']
      }
    )

    const byPeak = computed(() => {
      const map = new Map()
      for (const record of data.list.value) {
        const rows = map.get(record.sample_peak_id)
        if (rows) rows.push(record)
        else map.set(record.sample_peak_id, [record])
      }
      return map
    })

    /** The batch-level verdict that overlays an assignment, or null. */
    const overlayFor = (assignment) => {
      const m0 = usePeakAssignment().m0Of(assignment)
      if (!m0?.assigned_formula) return null
      const rows = byPeak.value.get(m0.sample_peak_id)
      if (!rows) return null
      return (
        rows.find(
          (record) =>
            record.assigned_formula === m0.assigned_formula &&
            (record.ionization_mechanism_id ?? null) === (m0.ionization_mechanism_id ?? null)
        ) ?? null
      )
    }

    return { ...data, byPeak, overlayFor }
  }
)
