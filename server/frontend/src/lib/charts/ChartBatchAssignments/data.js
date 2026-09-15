import { ref, shallowRef, computed, watch, watchEffect } from 'vue'
import { defineStore } from 'pinia'
import { api } from '@/api'
import { getApiErrorMessage } from '@/api/utils'
import { beautifySnakeCase } from '@/lib/utils'
import { useApp } from '@/stores'
import { MAX_SELECTED_BATCH_PEAKS } from '@/stores/data/modules/batchPeak/ledger'
import { glasbey } from '../colors.js'

// Confidence tier -> Plotly marker symbol. Fill decreases with confidence, mirroring
// the target overview's filled/open square encoding of match_category.
const TIER_SYMBOL = {
  assigned: 'square',
  candidate: 'square-open',
  below_assignability: 'diamond-open',
  unassigned: 'circle-open'
}

/** Batch peaks per POST /batch-peaks/records/series request. */
const CHUNK_SIZE = 100

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
   *   peak_series: { sample_item_ids: [], sample_peak_ids: [], intensities: [],
   *     tiers: [] } }
   * Held in a shallowRef: the arrays are large and never need deep reactivity.
   */
  const records = shallowRef([])
  const samples = computed(() => app.data.sample.list ?? [])
  const pending = ref(false)
  const resetChart = ref(0)
  /** Message from the last failed series fetch, rendered by the chart itself. */
  const error = ref(null)

  // --- What the chart plots ---------------------------------------------------

  /** The ledger selection, in the order the table selected it. */
  const selectedIds = computed(() => app.data.batchPeak.selectedIds ?? [])
  /**
   * The head of that selection the chart will actually plot.
   *
   * The ledger caps its own selection, so this normally passes it through
   * whole. It is applied again here because the store has a second way in that
   * the ledger's table never sees - the `peak_assignment_reload` handler below
   * reads the selection directly - and because a plot bounded only by a
   * component it does not own is bounded by nothing it can point at.
   */
  const plottedIds = computed(() => selectedIds.value.slice(0, MAX_SELECTED_BATCH_PEAKS))
  const selectedCount = computed(() => selectedIds.value.length)
  const plottedCount = computed(() => plottedIds.value.length)
  const truncated = computed(() => selectedCount.value > plottedCount.value)

  /** Fetch series (per-sample intensity arrays) for a set of batch peaks. */
  const fetchSeries = async (batchId, batchPeakIds, signal) => {
    const data = await api.http.post(
      `/batch-peaks/records/series`,
      {
        sample_batch_id: batchId,
        batch_peak_ids: batchPeakIds,
        // An earlier run's series come off its snapshot; the live ledger names no run.
        ...(app.data.batchPeakRun?.viewingId
          ? { batch_peak_run_id: app.data.batchPeakRun.viewingId }
          : {})
      },
      // `errors: 'inline'` holds back the interceptor's toast. A chunk aborted
      // because the selection moved on reaches the interceptor as a
      // response-less failure, which it would otherwise announce as a timeout;
      // a genuine failure is reported once, by the chart, from `error` below.
      { use: 'read', type: 'load_batch_peak_series', errors: 'inline', signal }
    )
    return data ?? []
  }

  // Bumped whenever what should be plotted changes. A run that finds its token
  // superseded drops what it fetched instead of appending it: those records
  // belong to a selection that is no longer on screen, and mixing them into the
  // current one would plot two selections at once.
  let requestToken = 0
  let inFlight = null
  // A re-read that was cancelled before it landed is still owed, and the debt
  // has to outlive the run that cancelled it. Its point is that the records
  // held are out of date, which is not something a diff of ids can see: the
  // superseding run would find every wanted id already held, return early, and
  // leave the pre-fold-in series plotted for good. The fold-in event and the
  // ledger's own reload arrive together, so this is the ordinary case.
  let rereadOwed = false

  /**
   * Bring the plotted records in line with the (capped) ledger selection.
   *
   * Diffs against the records actually held rather than against the previous
   * selection: under a cap the plotted set is not the selection, so
   * de-selecting a peak promotes one that was below the cap into view - a
   * change a selection diff cannot see. Reading the held records instead makes
   * the sync self-correcting whatever route the selection took.
   *
   * @param {{ refetch?: boolean }} options `refetch` re-reads every plotted peak
   *   rather than reusing what is held, for when the records themselves changed.
   *   The intent is remembered until a run actually completes it, so a re-read
   *   cancelled by a selection change is not lost.
   */
  const syncPlotted = async ({ refetch = false } = {}) => {
    const token = ++requestToken
    // Whatever is still in flight was fetching a selection that has moved on.
    inFlight?.abort()
    inFlight = null
    // Take on the debt of any re-read this run is superseding, as well as its own.
    rereadOwed = rereadOwed || refetch
    const reread = rereadOwed

    const batchId = app.data.batch.focusedId
    const wanted = batchId ? plottedIds.value : []
    const wantedSet = new Set(wanted)

    // Set membership throughout: select-all hands us tens of thousands of ids,
    // and an `includes` inside a `filter` over them is a frozen tab.
    const kept = reread ? [] : records.value.filter((r) => wantedSet.has(r.batch_peak_id))
    const held = new Set(kept.map((r) => r.batch_peak_id))
    const missing = wanted.filter((id) => !held.has(id))

    if (!missing.length) {
      // Only when something was actually dropped: `kept` is a fresh array every
      // time, and assigning an identical one would redraw every trace. Equal
      // lengths mean nothing was filtered out, so the held array still holds.
      if (kept.length !== records.value.length) records.value = kept
      rereadOwed = false
      error.value = null
      pending.value = false
      return
    }

    // The cap is what bounds this: `ceil(MAX_SELECTED_BATCH_PEAKS / CHUNK_SIZE)`
    // requests, three today, so the whole set can go out at once and the plot is
    // drawn once, when all of it has landed. The chunk loop this replaced
    // awaited each request in turn, which on an uncapped select-all was a
    // hundred round trips and a hundred redraws of a growing set of traces.
    const chunks = []
    for (let i = 0; i < missing.length; i += CHUNK_SIZE) {
      chunks.push(missing.slice(i, i + CHUNK_SIZE))
    }

    const controller = new AbortController()
    inFlight = controller
    pending.value = true
    error.value = null
    try {
      const fetched = await Promise.all(
        chunks.map((chunk) => fetchSeries(batchId, chunk, controller.signal))
      )
      if (token !== requestToken) return
      rereadOwed = false
      // Ordered by the selection rather than by arrival, so the legend follows
      // the ledger and a peak keeps its colour when its neighbours load. The
      // records held are not necessarily the head of it: a peak selected above
      // one already plotted belongs before it, not appended after it.
      const byId = new Map(
        kept.concat(fetched.flat()).map((record) => [record.batch_peak_id, record])
      )
      records.value = wanted.map((id) => byId.get(id)).filter(Boolean)
    } catch (err) {
      // A chunk that failed leaves its siblings running for nothing.
      controller.abort()
      // Superseded: the abort at the top of a newer run is what rejected this,
      // and that run owns the state - including the re-read debt - now.
      if (token !== requestToken) return
      rereadOwed = false
      error.value = getApiErrorMessage(err, 'Could not load the selected batch peaks.')
      // On a re-read `kept` is empty by construction, and emptying the plot
      // because a refresh failed is worse than leaving the last good series up
      // beside the message saying the refresh did not happen.
      if (!reread) records.value = kept
    } finally {
      if (token === requestToken) {
        pending.value = false
        inFlight = null
      }
    }
  }

  // The ledger's multi-selection drives the plotted set.
  watch(plottedIds, () => syncPlotted())
  // The run on screen changed: every plotted series belongs to the other one.
  watch(
    () => app.data.batchPeakRun?.viewingId ?? null,
    () => syncPlotted({ refetch: true })
  )

  // On batch change, clear the plot (the ledger reloads its own list + selection).
  watch(
    () => app.data.batch.focusedId,
    (id, oldId) => {
      if (id !== oldId) {
        requestToken++
        inFlight?.abort()
        inFlight = null
        // A re-read owed for the batch being left is not owed for the new one.
        rereadOwed = false
        pending.value = false
        error.value = null
        records.value = []
        resetChart.value++
      }
    }
  )

  // When batch peaks change (arrival fold-in / backfill), refetch the current
  // selection so the plotted traces reflect the new consensus. Under the same
  // cap and the same token as a selection change, and holding the old records
  // until the new ones land, so a fold-in mid-session neither re-runs the
  // unbounded fetch nor blanks the chart while it re-reads. Registered once for
  // the store's session lifetime (Pinia singleton; the chart toggles in and out).
  api.socket.on('peak_assignment_reload', () => syncPlotted({ refetch: true }))

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
      // The sample peak each point was folded from, on the same sample axis as
      // y: it is what a click on that point follows back into the sample view.
      // Null wherever the batch peak is absent, exactly like y.
      const samplePeakIds = new Array(samples.value.length).fill(null)
      for (let i = 0; i < series.sample_item_ids.length; i++) {
        const sampleIndex = sampleIndexById.get(series.sample_item_ids[i])
        if (sampleIndex === undefined) continue // sample not in current list
        yValues[sampleIndex] = series.intensities[i]
        samplePeakIds[sampleIndex] = series.sample_peak_ids?.[i] ?? null
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
        assignmentData: {
          batch_peak_id: record.batch_peak_id,
          sample_peak_ids: samplePeakIds
        },
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

  /**
   * The sample peak behind a clicked point, from plotly's (curveNumber,
   * pointIndex). Null for the TIC reference trace, which is not a batch peak,
   * and for a sample where the batch peak was never observed -- both cases the
   * caller degrades to focusing the sample alone.
   */
  const samplePeakIdAt = (curveNumber, pointIndex) =>
    traces.value[curveNumber]?.assignmentData?.sample_peak_ids?.[pointIndex] ?? null

  return {
    samples,
    traces,
    xFields,
    xField,
    resetChart,
    pending,
    error,
    truncated,
    selectedCount,
    plottedCount,
    samplePeakIdAt
  }
})
