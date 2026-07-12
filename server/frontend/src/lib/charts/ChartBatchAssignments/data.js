import { ref, shallowRef, computed, watch, watchEffect } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'
import { beautifySnakeCase } from '@/lib/utils'
import { useApp } from '@/stores'
import { glasbey } from '../colors.js'

// Confidence tier -> Plotly marker symbol. Fill decreases with confidence, mirroring
// the target overview's filled/open square encoding of match_category.
const TIER_SYMBOL = {
  identified: 'square',
  candidate: 'square-open',
  below_assignability: 'diamond-open',
  unassigned: 'circle-open'
}

/**
 * Data store for the peak-centric batch overview ("Assignments" mode).
 *
 * Peaks are assigned per-sample; this store reads the batch-level aggregate
 * (`POST /batch-peaks/records/series`) and builds one trace per **batch peak** — a
 * frozen cross-sample m/z anchor, the peak-centric replacement for the target-ion
 * identity of the legacy overview. Each point is one sample's intensity at that
 * anchor; the marker encodes the anchor's consensus confidence tier.
 */
export const useChartAssignmentsData = defineStore('chart.batch.assignments', () => {
  const app = useApp()
  const theme = computed(() => (app.ui.darkmode.active ? glasbey.dark : glasbey.light))

  /**
   * Per-batch-peak series records from the API.
   * Shape: { batch_peak_id, mz, consensus_formula, consensus_tier, n_present,
   *   support_fraction, is_ambiguous,
   *   peak_series: { sample_item_ids: [], intensities: [], tiers: [] } }
   * Held in a shallowRef: the arrays are large and never need deep reactivity.
   */
  const records = shallowRef([])
  const samples = computed(() => app.data.sample.list ?? [])
  const pending = ref(false)
  const resetChart = ref(0)

  /** Fetch series (per-sample intensity arrays) for a set of batch peaks. */
  const fetchSeries = async (batchPeakIds) => {
    const batchId = app.data.batch.focusedId
    if (!batchId || !batchPeakIds.length) return []
    const data = await api.http.post(
      `/batch-peaks/records/series`,
      { sample_batch_id: batchId, batch_peak_ids: batchPeakIds },
      { use: 'read', type: 'load_batch_peak_series' }
    )
    return data ?? []
  }

  /**
   * Plot only the SELECTED batch peaks (chosen in the ledger). Diffs old/new
   * selection: drops de-selected records and chunk-fetches newly-selected ones,
   * so the chart draws exactly the selection -- never all 1000+ batch peaks.
   */
  const handlePeaksSelected = async (nextSelected, prevSelected = []) => {
    records.value = records.value.filter((r) => nextSelected.includes(r.batch_peak_id))
    const newlySelected = nextSelected.filter((id) => !prevSelected.includes(id))
    if (!newlySelected.length) return
    pending.value = true
    try {
      const chunkSize = 100
      for (let i = 0; i < newlySelected.length; i += chunkSize) {
        const chunk = newlySelected.slice(i, i + chunkSize)
        const newRecords = await fetchSeries(chunk)
        records.value = records.value.concat(newRecords)
      }
    } finally {
      pending.value = false
    }
  }

  // The ledger's multi-selection drives the plotted set.
  watch(
    () => app.data.batchPeak.selectedIds,
    (next, prev) => handlePeaksSelected(next ?? [], prev ?? [])
  )

  // On batch change, clear the plot (the ledger reloads its own list + selection).
  watch(
    () => app.data.batch.focusedId,
    (id, oldId) => {
      if (id !== oldId) {
        records.value = []
        resetChart.value++
      }
    }
  )

  // When batch peaks change (arrival fold-in / backfill), refetch the current
  // selection so the plotted traces reflect the new consensus. Registered once for
  // the store's session lifetime (Pinia singleton; the chart toggles in and out).
  api.socket.on('peak_assignment_reload', async () => {
    const selected = app.data.batchPeak.selectedIds ?? []
    records.value = []
    await handlePeaksSelected(selected, [])
  })

  // --- X-axis field selection (mirrors ChartBatchOverview) ---
  const inferType = (field) => {
    const withField = samples.value.filter((item) => field in item)
    const types = [
      ...new Set(withField.map((item) => (item[field] ? typeof item[field] : 'null')))
    ].filter((type) => type !== 'null')
    return types.length === 1 ? types[0] : 'unknown'
  }

  const xFields = computed(() => {
    const standard = [
      ...new Set(
        samples.value
          ?.map((item) => Object.keys(item ?? {}))
          .flat()
          .filter((field) => field !== 'sample_item_attributes')
      )
    ].map((field) => ({ field, kind: 'standard' }))

    const custom = [
      ...new Set(
        samples.value?.map((item) => Object.keys(item?.sample_item_attributes ?? {})).flat()
      )
    ].map((field) => ({ field, kind: 'custom' }))

    return [...standard, { field: 'time_of_day', kind: 'custom' }, ...custom]
      .map(({ field, kind }) => ({
        field,
        kind,
        label: beautifySnakeCase(field),
        type: kind === 'custom' ? 'string' : inferType(field)
      }))
      .filter(({ type }) => type !== 'object')
  })

  const xField = ref()
  watchEffect(() => {
    xField.value = xFields.value.find(({ field }) => field === 'datetime')
  })

  /**
   * Build traces from per-batch-peak series records (one trace per batch peak).
   */
  const traces = computed(() => {
    if (!samples.value.length) return []

    const xFieldName = xField.value?.field || 'index'
    const xValues = samples.value.map(
      xFieldName === 'time_of_day'
        ? (sample) => `1970-01-01T${sample.datetime.split('T')[1].split('.')[0]}`
        : (sample) => sample[xFieldName]
    )
    const customdata = samples.value.map((sample) => [sample.datetime, 'counts/s'])
    const text = samples.value.map((sample) => sample.sample_item_name)
    const sampleIndexById = new Map(
      samples.value.map((sample, index) => [sample.sample_item_id, index])
    )

    const peakTraces = records.value.map((record, index) => {
      const series = record.peak_series
      const yValues = new Array(samples.value.length).fill(null)
      for (let i = 0; i < series.sample_item_ids.length; i++) {
        const sampleIndex = sampleIndexById.get(series.sample_item_ids[i])
        if (sampleIndex === undefined) continue // sample not in current list
        yValues[sampleIndex] = series.intensities[i]
      }

      const mz = Number(record.mz).toFixed(4)
      const label = record.consensus_formula ? record.consensus_formula : `m/z ${mz}`
      const traceName = `${label} · ${mz}`

      return {
        name: traceName,
        x: xValues,
        y: yValues,
        mode: 'markers',
        type: 'scattergl',
        marker: {
          color: theme.value[index % theme.value.length],
          size: 10,
          symbol: TIER_SYMBOL[record.consensus_tier] ?? 'circle-open'
        },
        // Click metadata for focusing
        assignmentData: { batch_peak_id: record.batch_peak_id },
        customdata,
        text,
        hovertemplate: `
          <i>Batch peak</i>
          <b># %{x}</b>
          <br>
          <b>${traceName}</b>
          <br>
          Tier: ${record.consensus_tier}${record.n_present ? ` · ${record.n_present} samples` : ''}
          <br>
          <b>%{text}</b>
          <br>
          Intensity: %{y:,.2e} %{customdata[1]}
          <br>
          %{customdata[0]}
          <extra></extra>
        `
      }
    })

    // Always add a TIC reference trace.
    peakTraces.push({
      name: 'TIC',
      x: xValues,
      y: samples.value.map((sample) => sample.tic),
      customdata: samples.value.map((sample) => [sample.datetime, '']),
      text,
      hovertemplate: `
        <b># %{x}</b>
        <br>
        <b>%{text}</b>
        <br>
        TIC: %{y:,.2e}
        <br>
        %{customdata[0]}
        <extra></extra>
      `,
      mode: 'markers',
      type: 'scattergl',
      marker: {
        color: app.ui.darkmode.active ? '#888' : '#222',
        size: 10,
        symbol: 'diamond-open'
      }
    })
    return peakTraces
  })

  return { samples, traces, xFields, xField, resetChart, pending }
})
