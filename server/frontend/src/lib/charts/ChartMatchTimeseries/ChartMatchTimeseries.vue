<script setup>
import { ref, computed, nextTick, toRaw, watch, onMounted } from 'vue'

import { useApp } from '@/stores'

import BaseChartPlotly from '../BaseChartPlotly.vue'
import { useChartData } from './data.js'
import { ToolbarIntensityScale } from '@/lib/toolbars'

const app = useApp()
const data = useChartData()

const scale = defineModel()

const props = defineProps({
  height: {
    type: Number,
    required: false
  }
})

const plot = ref(null)

const sampleLength = computed(() =>
  // Length of the selected sample in seconds
  app.data.sample.selected.length != 1 ? null : app.data.sample.selected[0].length
)

const traces = computed(() => {
  if (plot.value === null) return []
  // Scale trace y-values based on "sum / average" toggle
  if (sampleLength.value === null) {
    return []
  }
  return scale.value.mode == 'average'
    ? // Shallow copy: only the fill mode changes, the (potentially long,
      // one point per scan) data arrays can be shared with the store
      data.traces.map((trace) => ({ ...toRaw(trace), fill: 'none' }))
    : data.traces.toReversed()
})

watch(
  () => app.data.match.visualized.isotopes,
  () => {
    // Reset zoom when data changes
    if (plot.value !== null) {
      plot.value.resetZoom()
    }
  }
)

onMounted(() => {
  scale.value.log = true
})

// The chart is as tall as its container, which takes `height`, and the layout
// names no height of its own. Plotly's responsive resize deletes a layout
// height and autosizes to the container, so a height held only in the layout
// lasts until the plot div next changes size; after that the chart fills a
// container with no height of its own and draws nothing.
const containerStyle = computed(() => (props.height ? { height: `${props.height}px` } : null))

watch(
  () => props.height,
  async () => {
    await nextTick()
    plot.value?.resize()
  }
)

// Drawn while the Match tab was hidden, the chart took no size from its
// container; it takes one once the tab shows.
watch(
  () => app.ui.tab.active,
  async (tab) => {
    if (tab === 'match') {
      await nextTick()
      plot.value?.resize()
    }
  }
)

const layout = computed(() => ({
  xaxis: {
    title: { text: 'Time [s]' },
    autorange: true,
    showgrid: true,
    gridcolor: '#33333399',
    gridwidth: 1
  },
  yaxis: {
    title: { text: `Peak intensity [counts/s]` },
    showgrid: true,
    autorange: true,
    rangemode: 'tozero',
    type: scale.value.log ? 'log' : 'lin',
    gridcolor: '#33333399',
    gridwidth: 1
  },
  margin: { l: 50, r: 30, t: 30, b: 40 },
  dragmode: 'zoom',
  showlegend: true,
  legend: {
    x: 1,
    y: 1
  }
}))
</script>

<template>
  <figure
    v-if="app.data.match.visualized.isotopes?.length"
    style="padding: 0; margin: 0; width: 100%; min-width: 0; overflow: hidden; flex-shrink: 1"
  >
    <div style="width: 100%; min-width: 0; overflow: hidden" :style="containerStyle">
      <BaseChartPlotly
        id="ChartMatchTimeseries"
        ref="plot"
        title="Timeseries"
        :data="traces"
        :layout="layout"
      >
        <template v-slot:settings>
          <ToolbarIntensityScale v-model="scale" />
        </template>
      </BaseChartPlotly>
    </div>
  </figure>
</template>
