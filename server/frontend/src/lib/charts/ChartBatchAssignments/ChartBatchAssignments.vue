<script setup>
import { ref, computed, watch, toRaw, nextTick, onUnmounted } from 'vue'

import Select from 'primevue/select'
import FloatLabel from 'primevue/floatlabel'
import ProgressSpinner from 'primevue/progressspinner'
import Button from 'primevue/button'

import { api } from '@/api'
import { useApp } from '@/stores'
import { ToolbarIntensityScale } from '@/lib/toolbars'

import BaseChartPlotly from '../BaseChartPlotly.vue'
import { useSampleScroller } from '@/lib/panes/PaneBrowserSample/stores'
import { useChartAssignmentsData } from './data'

const app = useApp()
const data = useChartAssignmentsData()
const scroller = useSampleScroller()

const plot = ref({})
const showSpinner = ref(false)
const computing = ref(false)

const scale = ref({
  mode: 'average',
  max: null,
  log: true
})

const chartTitle = computed(() => {
  const batchName = app.data.batch?.focused?.sample_batch_name || null
  const sampleCount = app.data.sample?.list.length || 0
  if (!batchName) return ''
  return `<i>Batch:</i>\t<b> ${batchName} </b>\t<i>(${sampleCount} samples)</i>`
})

const chartSubtitle = computed(() => {
  // data.traces includes a trailing TIC trace.
  const peakCount = data.traces.length ? data.traces.length - 1 : 0
  if (!peakCount) {
    return 'No batch peaks yet - assign samples to populate the batch overview'
  }
  return `<i>Assignments:</i>\t\t ${peakCount} batch peaks (present in >= ${data.minPresent} samples)`
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

/** Click a point -> focus its sample (and scroll to it in the table). */
async function onClick({ pointIndex }) {
  if (pointIndex == null) return
  const sample = app.data.sample.list[pointIndex]
  if (sample) {
    app.data.sample.focus(sample)
    scroller.scrollToSample(app.data.sample.focusedId)
  } else {
    app.data.sample.unfocus()
  }
}

/** Box/lasso select -> update sample selection. */
function onSelect({ points }) {
  const samples = points.map((i) => app.data.sample.list[i])
  app.data.sample.selected = samples
  scroller.scrollToSamples(app.data.sample.selectedIds)
}

/**
 * Backfill this batch's batch peaks from its samples' existing assignments. The
 * store reloads when the background task emits `peak_assignment_reload`.
 */
async function computeBatchPeaks() {
  const batchId = app.data.batch.focusedId
  if (!batchId || computing.value) return
  computing.value = true
  try {
    await api.http.post(
      `/batch-peaks/batch/${batchId}/backfill`,
      {},
      { use: 'read', type: 'backfill_batch_peaks' }
    )
  } finally {
    computing.value = false
  }
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

// Load on mount if a batch is already focused.
if (app.data.batch.focusedId) data.load()

onUnmounted(() => {
  if (loadingTimeout) clearTimeout(loadingTimeout)
  if (syncTimeout) clearTimeout(syncTimeout)
})
</script>

<template>
  <figure style="height: calc(100vh - 200px); position: relative">
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
        <div style="height: 0.5rem" />
        <Button
          label="Compute batch peaks"
          icon="ph ph-arrows-clockwise"
          size="small"
          severity="secondary"
          :loading="computing"
          fluid
          @click="computeBatchPeaks"
        />
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
