import { computed } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/api'
import { useData } from '@/lib/store'
import { peakAssignmentEnabled } from '@/lib/features'

import { useBatch } from '../batch'
import { usePeakAssignmentAnchorContext } from '../peakAssignment/anchorContext'

/**
 * Batch-level verdicts: one judgment per species at a batch peak.
 *
 * The listing is every verdict recorded on the focused batch's anchors, newest
 * first, superseded rows included; the store derives the live one per anchor. A
 * verdict is pinned to the claim it judged (formula + mechanism), so the ledger
 * row it is read against decides whether it is current or stale - the row in
 * hand is fresher than the `stale` flag the server computed at fetch time.
 */
export const useBatchPeakVerification = defineStore('app.data.batchPeak.verification', () => {
  const name = 'batch_peak_verification'
  const key = 'batch_peak_verification_id'

  const data = useData(
    name,
    ({ sample_batch_id }) => {
      if (!sample_batch_id) return []
      return api.http.get(`/batch-peaks/batch/${sample_batch_id}/verdicts`, {
        use: 'read',
        type: 'load_batch_peak_verdicts'
      })
    },
    {
      key,
      deps: () => ({
        sample_batch_id: peakAssignmentEnabled ? useBatch().focusedId : null
      })
    }
  )

  const sameClaim = (record, row) =>
    record.assigned_formula === row.consensus_formula &&
    (record.ionization_mechanism_id ?? null) === (row.ionization_mechanism_id ?? null)

  // Live verdicts by anchor, newest first as served. Exactly one per claim - a
  // partial unique index says so - but an anchor can hold several claims' worth
  // once the consensus has moved under a verdict.
  const liveByAnchor = computed(() => {
    const map = new Map()
    for (const record of data.list.value) {
      if (record.superseded_utc) continue
      const rows = map.get(record.batch_peak_id)
      if (rows) rows.push(record)
      else map.set(record.batch_peak_id, [record])
    }
    return map
  })

  /**
   * The verdict a ledger row shows: the live one on its present claim, else the
   * newest live one on a claim it no longer makes (stale - see `isStale`), else
   * none.
   */
  const forAnchor = (row) => {
    if (!row) return null
    const live = liveByAnchor.value.get(row.batch_peak_id)
    if (!live?.length) return null
    return live.find((record) => sameClaim(record, row)) ?? live[0]
  }

  /** Whether a verdict is about a claim the row no longer makes. */
  const isStale = (record, row) => Boolean(record && row && !sameClaim(record, row))

  // A write changes what every sample in the batch shows, so the focused
  // sample's overlay reloads with the listing.
  async function afterWrite(response) {
    const outcome = await data.load('verification')
    if (outcome.error) throw outcome.error
    usePeakAssignmentAnchorContext().load('batch verdict')
    return response?.data?.[0] ?? null
  }

  /** Record a verdict; the body names the formula judged (`expected_formula`). */
  async function verify(body) {
    const sample_batch_id = useBatch().focusedId
    if (!sample_batch_id) return null
    const response = await api.http.post(`/batch-peaks/batch/${sample_batch_id}/verify`, body, {
      use: 'create',
      type: 'verify_batch_peak'
    })
    return afterWrite(response)
  }

  /** Withdraw the live verdict(s) on a batch peak. */
  async function retract(body) {
    const sample_batch_id = useBatch().focusedId
    if (!sample_batch_id) return null
    const response = await api.http.post(`/batch-peaks/batch/${sample_batch_id}/retract`, body, {
      use: 'update',
      type: 'retract_batch_peak_verdict'
    })
    return afterWrite(response)
  }

  return { ...data, liveByAnchor, forAnchor, isStale, verify, retract }
})
