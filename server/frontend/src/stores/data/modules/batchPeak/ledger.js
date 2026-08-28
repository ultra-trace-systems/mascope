import { computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { countTiers } from '@/lib/tiers'

import { useBatch } from '../batch'

/**
 * Ledger of a batch's "batch peaks" (cross-sample m/z anchors) -- the selection
 * surface for the peak-centric batch overview. Multi-select here drives which
 * batch peaks the Assignments chart plots, so the chart never renders 1000+
 * traces at once. Metadata only (no per-sample series); the chart fetches the
 * series for the selected peaks.
 */
export const useBatchPeakLedger = defineStore('app.data.batchPeak', () => {
  const name = 'batch_peak'
  const key = 'batch_peak_id'

  const data = useData(
    name,
    ({ sample_batch_id }) => {
      if (!sample_batch_id) return []
      // min_n_present=1: the ledger lists every batch peak so any (even
      // event-specific, low-prevalence) species is selectable; the chart is
      // what limits the plot, by selection.
      return api.http.get(`/batch-peaks/batch/${sample_batch_id}`, {
        params: { min_n_present: 1 },
        use: 'read',
        type: 'load_batch_peak_ledger'
      })
    },
    {
      key,
      deps: () => ({ sample_batch_id: useBatch().focusedId }),
      selection: { mode: 'multiple' },
      // Reload when the arrival fold-in / backfill updates batch peaks.
      events: ['peak_assignment_reload']
    }
  )

  // Tier histogram for the ledger's filter strip (one row per batch peak).
  //
  // Four buckets, not the sample ledger's five: `reagent` there is a role
  // rather than a tier, and a batch peak is a consensus across samples that
  // carries no role at all - a bucket that can only ever read zero is a
  // question the user is invited to click and get nothing back from.
  const tierCounts = computed(() => countTiers(data.list.value, (bp) => bp.consensus_tier))

  return { ...data, tierCounts }
})
