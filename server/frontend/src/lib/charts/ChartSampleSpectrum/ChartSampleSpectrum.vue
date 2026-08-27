<script setup>
import { ref, reactive, computed, toRaw, watch, watchEffect, nextTick } from 'vue'

import { useApp } from '@/stores'
import { usePreview } from '@/lib/panes'
import { ToolbarIntensityScale } from '@/lib/toolbars'
import { instrumentType as getInstrumentType } from '@/lib/utils'
import { peakAssignmentEnabled } from '@/lib/features'

import BaseChartPlotly from '../BaseChartPlotly.vue'
import { useChartData } from './data.js'

const app = useApp()
const data = useChartData()

// Help card for the whole chart. The tier colors deliberately have no on-chart
// legend (showlegend: false below), so this card is where they are named.
const spectrumHelp = peakAssignmentEnabled
  ? {
      message: `
        <h1>Sum Spectrum</h1>
        <p>
        The sample's spectrum: the continuous signal in green, with every
        detected peak drawn as a vertical line. Once the sample has an
        assignment run, the peak lines are colored by confidence tier &mdash;
        green identified, amber candidate, grey-blue below assignability,
        purple reagent / artifact, grey unassigned.
        </p>
        <p>
        Click a peak to focus it: the view zooms in, the inspector shows its
        assignment, and the predicted isotope pattern is drawn in crimson with
        circles at the expected peak heights.
        </p>`,
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
    }
  : {
      message: `
        <h1>Sum Spectrum</h1>
        <p>
        The sample's spectrum: the continuous signal in green, with every
        detected peak drawn as a vertical grey line. Click a peak to select it
        and assign a composition below.
        </p>`,
      doc: app.ui.help.docUrl('how-it-works/peak-detection/')
    }

const plot = ref({})
const preview = usePreview()

const props = defineProps({
  height: {
    type: Number,
    required: true
  }
})

const scale = ref({
  mode: 'average',
  max: null,
  log: false
})

const unit = computed(() =>
  // Adjust the y-axis unit based on "average / sum" toggle
  scale.value.mode == 'average' ? 'counts/s' : 'counts'
)
const sampleLength = computed(() => app.data.sample.focused.length) // duration in seconds

// Half-width of the m/z window the spectrum zooms to on peak focus. Orbitrap
// peaks are far narrower than TOF, so a tight window keeps the selected
// isotopologue centered instead of showing a wide, mostly-empty span.
const mzHalfWindow = computed(() =>
  getInstrumentType(app.data.sample.focused?.instrument) === 'tof' ? 0.3 : 0.05
)

const traces = computed(() =>
  scale.value.mode === 'average'
    ? data.traces
    : data.traces.map((trace) => {
        // Shallow copy with scaled y (and customdata for peak traces);
        // unchanged fields keep sharing the store's arrays
        const newTrace = {
          ...toRaw(trace),
          y: trace.y.map((value) => (value ? value * sampleLength.value : value))
        }
        // For peak traces, scale "customdata" containing [height, area]
        if (newTrace.name.endsWith('Peak')) {
          newTrace.customdata = trace.customdata.map((subarr) => {
            return subarr?.map((value, i) => (i < 2 ? value * sampleLength.value : value))
          })
        }
        return newTrace
      })
)

const zoom = reactive({
  rangeX: null,
  rangeY: null
})

watch(
  () => [scale.value.mode, scale.value.log],
  () => {
    zoom.rangeY = { autorange: true }
  }
)

watch(
  () => props.height,
  async () => {
    console.debug(`📊 [ChartSampleSpectrum] height changed to ${props.height}`)
    await nextTick()
    plot.value.resize()
  }
)

watch(
  () => app.ui.tab.active,
  async (newValue) => {
    if (newValue === 'sample') {
      // Wait for DOM to update after tab switch, then resize plot
      await nextTick()
      plot.value.resize()
    }
  }
)

watchEffect(() => {
  if (app.data.peak.focused) {
    const mz = preview.peak?.mz ?? app.data.peak.focused.mz
    const factor = scale.value.mode == 'sum' ? sampleLength.value : 1
    const height = factor * app.data.peak.focused.height
    zoom.rangeX = {
      range: [mz - mzHalfWindow.value, mz + mzHalfWindow.value],
      autorange: false
    }
    zoom.rangeY = scale.value.log
      ? { range: null, autorange: true }
      : { range: [0, height * 1.2], autorange: false }
  } else {
    zoom.rangeX = { range: null, autorange: true }
    zoom.rangeY = { range: null, autorange: true }
  }
})

const layout = computed(() => {
  const scaleRangeY =
    scale.value.max && scale.value.max > 0 ? { range: [0, scale.value.max] } : null
  const autorange = { autorange: true }
  const yRange = scaleRangeY ?? zoom.rangeY ?? autorange
  const xRange = zoom.rangeX ?? autorange
  return {
    xaxis: {
      title: { text: 'm/z [Th]' },
      showgrid: true,
      gridcolor: '#33333399',
      gridwidth: 1,
      ...xRange
    },
    yaxis: {
      title: { text: `Signal intensity [${unit.value}]` },
      showgrid: true,
      rangemode: 'nonnegative',
      gridcolor: '#33333399',
      gridwidth: 1,
      type: scale.value.log ? 'log' : 'lin',
      ...yRange
    },
    margin: { l: 60, r: 10, t: 45, b: 50 },
    dragmode: 'zoom',
    showlegend: false
  }
})

const config = {
  modeBarButtonsToRemove: ['autoScale', 'resetScale2d', 'pan2d']
}
</script>

<template>
  <BaseChartPlotly
    id="ChartSampleSpectrum"
    ref="plot"
    title="Sum spectrum"
    v-help.bottom="spectrumHelp"
    :data="traces"
    :layout="layout"
    :config="config"
    :loading="data.loading"
    @click="
      (clickData) => {
        if (clickData.event.button === 0) {
          // Focus the closest peak to the clicked m/z value
          if (!app.data.peak.list.length) return
          app.data.peak.focus({
            peak_id: app.data.peak.list.reduce((closest, peak) =>
              Math.abs(peak.mz - clickData.x) < Math.abs(closest.mz - clickData.x) ? peak : closest
            ).peak_id
          })
        }
      }
    "
  >
    <template v-slot:settings>
      <ToolbarIntensityScale v-model="scale" />
    </template>
  </BaseChartPlotly>
</template>

<style scoped>
.faded {
  opacity: 0.3;
}
</style>
