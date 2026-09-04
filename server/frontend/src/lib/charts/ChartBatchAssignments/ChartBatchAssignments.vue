<script setup>
import { ref, computed, watch, toRaw, nextTick, onUnmounted } from 'vue'

import Select from 'primevue/select'
import FloatLabel from 'primevue/floatlabel'
import ProgressSpinner from 'primevue/progressspinner'

import { useApp } from '@/stores'
import { ToolbarDrawMode, ToolbarIntensityScale } from '@/lib/toolbars'

import BaseChartPlotly from '../BaseChartPlotly.vue'
import { focusSamplePeak, useSampleScroller } from '@/lib/panes/PaneBrowserSample/stores'
import { useChartAssignmentsData } from './data'

const app = useApp()
const data = useChartAssignmentsData()
const scroller = useSampleScroller()

const plot = ref({})
const showSpinner = ref(false)

const scale = ref({
  mode: 'average',
  max: null,
  log: true
})

const drawMode = ref('markers')

const chartTitle = computed(() => {
  const batchName = app.data.batch?.focused?.sample_batch_name || null
  const sampleCount = app.data.sample?.list.length || 0
  if (!batchName) return ''
  return `<i>Batch:</i>\t<b> ${batchName} </b>\t<i>(${sampleCount} samples)</i>`
})

// The chart's status line. It carries a failed series load as well as the
// count, because the store now asks the API to keep quiet about these requests
// - a chunk cancelled when the selection moves on is not a failure worth a
// toast - and a chart that just came up empty owes the reader a reason.
const chartSubtitle = computed(() => {
  if (data.error) return `<i>Could not load the selected batch peaks:</i> ${data.error}`
  // data.traces includes a trailing TIC trace.
  const peakCount = data.traces.length ? data.traces.length - 1 : 0
  if (!peakCount) {
    return 'Select batch peaks in the Assignments ledger to plot their time series'
  }
  // The ledger caps its own selection, so the two counts normally agree; when
  // they do not, saying only the plotted one would claim the chart shows a
  // selection it is showing the head of.
  if (data.truncated) {
    const total = data.selectedCount.toLocaleString('en-US')
    return `<i>Assignments:</i>\t\t ${peakCount} of ${total} selected batch peaks`
  }
  return `<i>Assignments:</i>\t\t ${peakCount} selected batch peak${peakCount === 1 ? '' : 's'}`
})

const unit = computed(() => (scale.value.mode == 'average' ? '[cps]' : '[counts]'))

/**
 * Scale traces based on average/sum mode (shallow copies; store arrays untouched).
 */
const traces = computed(() => {
  if (!data.traces.length) return []

  const sampleList = app.data.sample.list
  const average = scale.value.mode == 'average'
  const customdata = sampleList.map((sample) => [sample.datetime, average ? 'counts/s' : 'counts'])

  return data.traces.map((trace) => ({
    ...toRaw(trace),
    mode: drawMode.value,
    // The store colors traces via marker.color only; without an explicit line
    // color plotly would pick unrelated colorway colors for the line segments.
    // Tier lives on marker.symbol, so lines+markers keeps the tier encoding.
    ...(drawMode.value !== 'markers' ? { line: { color: trace.marker.color, width: 1 } } : {}),
    customdata,
    y: average
      ? trace.y
      : trace.y.map((value, i) => (value !== null ? value * sampleList[i].length : null))
  }))
})

const xAxis = computed(() => ({
  tickformat: data.xField?.field === 'time_of_day' ? '%H:%M:%S' : undefined
}))

const dragmode = ref('zoom')
const zoom = { rangeX: null, rangeY: null }

const layout = computed(() => {
  const scaleRangeY =
    scale.value.max && scale.value.max > 0
      ? { range: [0, scale.value.max], autorange: false }
      : null

  const autorange = { range: null, autorange: true }
  const yRange = scaleRangeY
    ? { ...scaleRangeY, autorange: false }
    : zoom.rangeY
      ? { ...zoom.rangeY, autorange: false }
      : autorange
  const xRange = zoom.rangeX ? { ...zoom.rangeX, autorange: false } : autorange

  return {
    xaxis: {
      title: { text: data.xField?.label },
      autorange: true,
      automargin: true,
      showgrid: true,
      gridcolor: '#33333399',
      tickmode: 'array',
      tickangle: 45,
      gridwidth: 1,
      ...xAxis.value,
      ...xRange
    },
    yaxis: {
      title: { text: `Intensity ${unit.value}` },
      type: scale.value.log ? 'log' : 'lin',
      showgrid: true,
      gridcolor: '#33333399',
      rangemode: 'tozero',
      gridwidth: 1,
      ...yRange
    },
    margin: { l: 50, r: 50, t: 50, b: 50 },
    showlegend: true,
    autosize: true,
    dragmode: dragmode.value
  }
})

/**
 * Click a point -> focus its sample, then the sample peak the point was folded
 * from, and bring the Sample tab (spectrum + inspector) forward -- the same
 * click-through the batch ledger offers on a row, the sample ledger offers on
 * an assignment and the overview chart offers for a matched ion; the shared
 * `focusSamplePeak` is where the wait for the peak store and the string
 * comparison of ids live. A click on the TIC trace, or on a sample where this
 * batch peak was never observed, focuses the sample and stops there -- this
 * handler writes no peak focus in that case. What the peak focus then does is
 * the general rule for any sample switch: it follows the peak that was already
 * focused into the new sample (see `peakFocusFollow`). That follow stands down
 * whenever a peak is focused here, so an explicit click-through always wins
 * over it.
 */
async function onClick({ pointIndex, curveNumber }) {
  if (pointIndex == null) return
  const sample = app.data.sample.list[pointIndex]
  if (!sample) {
    app.data.sample.unfocus()
    return
  }
  await focusSamplePeak(app, sample, data.samplePeakIdAt(curveNumber, pointIndex))
}

/** Box/lasso select -> update sample selection. */
function onSelect({ points }) {
  const samples = points.map((i) => app.data.sample.list[i])
  app.data.sample.selected = samples
  scroller.scrollToSamples(app.data.sample.selectedIds)
}

let syncTimeout = null
let loadingTimeout = null

const syncChartSelection = async () => {
  if (syncTimeout) clearTimeout(syncTimeout)
  syncTimeout = setTimeout(() => {
    if (!plot.value || !app.data.sample.list.length) return
    const selectedIds = app.data.sample.selectedIds
    if (selectedIds.length === 0) {
      plot.value.resetSelection()
    } else {
      const pointIndices = app.data.sample.list
        .map((sample, index) => (selectedIds.includes(sample.sample_item_id) ? index : null))
        .filter((index) => index !== null)
      if (pointIndices.length > 0) plot.value.selectPoints(pointIndices)
    }
  }, 50)
}

watch(() => app.data.sample.selectedIds, syncChartSelection)

watch(traces, () => {
  if (data.pending) return
  syncChartSelection()
})

watch(
  () => data.resetChart,
  () => {
    if (plot.value) {
      zoom.rangeX = null
      zoom.rangeY = null
      plot.value.resetZoom()
    }
  }
)

watch(
  () => data.pending,
  (isLoading) => {
    if (loadingTimeout) {
      clearTimeout(loadingTimeout)
      loadingTimeout = null
    }
    if (isLoading) {
      loadingTimeout = setTimeout(() => {
        showSpinner.value = true
      }, 1000)
    } else {
      showSpinner.value = false
    }
  },
  { immediate: true }
)

watch(
  () => scale.value.log,
  (prev, next) => {
    if (next) {
      scale.value.max = null
      plot.value.resetZoom()
    }
  }
)

watch(
  () => app.ui.tab.active,
  async (newValue) => {
    if (newValue === 'batch') {
      await nextTick()
      plot.value.resize()
    }
  }
)

onUnmounted(() => {
  if (loadingTimeout) clearTimeout(loadingTimeout)
  if (syncTimeout) clearTimeout(syncTimeout)
})
</script>

<template>
  <figure
    style="height: calc(100vh - 200px); position: relative"
    v-help.top="{
      message: `
        <h1>Batch Assignments</h1>
        <p>
        Time series of the batch peaks selected in the Assignments ledger: one
        trace per batch peak, one point per sample. Marker fill encodes the
        consensus tier &mdash; filled squares assigned, open squares
        candidate, open diamonds below assignability, open circles unassigned
        &mdash; and the neutral open-diamond trace is the total ion current
        (TIC) reference.
        </p>
        <p>
        Click a point to open it in the Sample tab: its sample is focused and
        so is the peak the point was measured from. Drag with the select tool
        to select samples instead.
        </p>`,
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks')
    }"
  >
    <div v-if="showSpinner" class="loading-indicator">
      <ProgressSpinner strokeWidth="3px" />
    </div>

    <BaseChartPlotly
      id="ChartBatchAssignments"
      ref="plot"
      :title="chartTitle"
      :subtitle="chartSubtitle"
      :data="traces"
      :layout="layout"
      @click="onClick"
      @dragmode="
        (mode) => {
          dragmode = mode
        }
      "
      @select="onSelect"
      @zoom="
        ({ rangeX, rangeY }) => {
          zoom.rangeX = rangeX ?? zoom.rangeX
          zoom.rangeY = rangeY ?? zoom.rangeY
        }
      "
    >
      <template v-slot:settings>
        <ToolbarIntensityScale v-model="scale" />
        <div style="height: 0.5rem" />
        <ToolbarDrawMode v-model="drawMode" />
        <div style="height: 0.5rem" />
        <FloatLabel>
          <Select
            v-model="data.xField"
            :options="data.xFields"
            optionLabel="label"
            dataKey="field"
            filter
            fluid
          />
          <label>X-axis</label>
        </FloatLabel>
      </template>
    </BaseChartPlotly>
  </figure>
</template>

<style scoped>
.loading-indicator {
  position: absolute;
  top: 10px;
  left: 50px;
  z-index: 1000;
  background-color: transparent;
  border-radius: 4px;
  padding: 8px;
}

.loading-indicator :deep(.p-progressspinner) {
  width: 20px !important;
  height: 20px !important;
}
</style>
