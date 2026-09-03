<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, useSlots } from 'vue'

import Plotly from 'plotly.js-dist-min'

import ProgressSpinner from 'primevue/progressspinner'
import Button from 'primevue/button'
import Popover from 'primevue/popover'

import { useWindowSize } from '@vueuse/core'

import { useApp } from '@/stores'

const win = useWindowSize()

const app = useApp()

const props = defineProps({
  id: {
    type: String,
    required: true
  },
  title: {
    type: String
  },
  subtitle: {
    type: String,
    required: false
  },
  data: {
    type: Array
  },
  config: {
    type: Object
  },
  layout: {
    type: Object
  },
  hideTitle: {
    type: Boolean,
    default: false
  },
  loading: {
    type: Boolean,
    default: false
  },
  height: {
    type: Number,
    required: false
  }
})
const slots = useSlots()

const emit = defineEmits(['click', 'dragmode', 'select', 'zoom'])

const plot = ref(null)
const created = ref(false)
const settings = ref()
let clickTimeout = null

const resetSelection = () => {
  if (plot.value && props.data.length > 0) {
    // removes the selection
    Plotly.update(plot.value, {}, { selections: [] })
    // removes the selection overlay
    Plotly.restyle(plot.value, { selectedpoints: [null] })
  }
}

// reset chart zoom to autorange
const resetZoom = () => {
  if (plot.value) {
    Plotly.relayout(plot.value, {
      'xaxis.autorange': true,
      'yaxis.autorange': true
    })
  }
}

const selectPoints = (pointIndices) => {
  if (plot.value && props.data.length > 0) {
    // Make the selection
    Plotly.update(plot.value, {}, { selections: pointIndices })
    // Make the selection overlay
    Plotly.restyle(plot.value, { selectedpoints: [pointIndices] })
  }
}

// Force Plotly to recalculate container dimensions
const resize = () => {
  if (plot.value) {
    console.debug(`📊 [${props.id}] resizing chart`)
    Plotly.Plots.resize(plot.value)
  }
}

defineExpose({
  resetSelection,
  resetZoom,
  selectPoints,
  resize
})

const derived = computed(() => ({
  layout: Object.assign(
    {
      ...(!props.hideTitle
        ? {
            title: {
              text: props.subtitle
                ? `${props.title}<br><span style="font-size: 14px; font-weight: 400; opacity: 0.85;">${props.subtitle}</span>`
                : props.title,
              pad: {
                b: 10
              }
            }
          }
        : {
            margin: {
              t: 40
            },
            title: {
              automargin: false
            }
          }),
      hoverinfo: 'name+y',
      paper_bgcolor: 'transparent',
      autosize: true,
      useResizeHandler: true,
      modebar: {
        bgcolor: 'transparent'
      }
    },
    props.layout
  ),
  config: Object.assign(
    {
      displaylogo: false,
      displayModeBar: true,
      // plotly.js 4 turns its "Share Chart" button on by default and points it
      // at cloud.plotly.com. Every chart here draws customer measurements, so
      // a one-click upload to a third party is not something to inherit from a
      // library default - keep it off explicitly.
      showSendToCloud: false,
      responsive: true,
      modeBarButtonsToRemove: ['autoScale', 'resetScale2d', 'pan2d', 'zoomIn2d', 'zoomOut2d'],
      toImageButtonOptions: {
        format: 'png', // one of png, svg, jpeg, webp
        filename: props.title.toLowerCase().replaceAll(/[\s-]/g, '_'),
        height: 500,
        width: 700,
        scale: 1 // Multiply title/legend/axis/canvas sizes by this factor
      }
    },
    props.config
  )
}))

function handleClick(event) {
  console.debug(`📊 [${props.id}] click event:`, event)

  // Clear any existing timeout
  if (clickTimeout) {
    clearTimeout(clickTimeout)
  }

  // Debounce click to avoid conflict with double-click
  clickTimeout = setTimeout(() => {
    const { data, x, y } = event.points[0]
    emit('click', { data, x, y, event: event.event, ...event.points[0] })
    clickTimeout = null
  }, 250) // 250ms delay to distinguish from double-click
}

function handleSelect(event) {
  if (event && event.points.length === 0) {
    // Skip selection event for programmatically triggered selections
    return
  }
  const pointIndices = event
    ? [...new Set(event.points.map((point) => point.pointIndex))].sort()
    : []
  emit('select', { points: pointIndices })
}

function handleRelayout(data) {
  console.debug(`📊 [${props.id}] relayout event:`, data)

  // Cancel pending click if double-click caused relayout
  if (clickTimeout) {
    clearTimeout(clickTimeout)
    clickTimeout = null
  }

  // Handle zoom events
  const xmin = data['xaxis.range[0]']
  const xmax = data['xaxis.range[1]']
  const ymin = data['yaxis.range[0]']
  const ymax = data['yaxis.range[1]']
  emit('zoom', {
    rangeX: xmin != null && xmax != null ? { range: [xmin, xmax] } : null,
    rangeY: ymin != null && ymax != null ? { range: [ymin, ymax] } : null
  })
  // Handle dragmode changes
  const dragmode = data['dragmode']
  if (dragmode) {
    // Update dragmode state
    emit('dragmode', dragmode)
  }
}

onMounted(() => {
  console.debug(`📊 [${props.id}] creating chart`)
  // create the plot
  Plotly.newPlot(plot.value, props.data, derived.value.layout, derived.value.config)
  // add the event listener
  plot.value.on('plotly_click', handleClick)
  plot.value.on('plotly_relayout', handleRelayout)
  plot.value.on('plotly_selected', handleSelect)
  // mark as created
  created.value = true
})
onBeforeUnmount(() => {
  console.debug(`📊 [${props.id}] destroying chart`)

  // Clear any pending click timeout
  if (clickTimeout) {
    clearTimeout(clickTimeout)
    clickTimeout = null
  }

  plot.value?.removeEventListener('plotly_click', handleClick)
  plot.value?.removeEventListener('plotly_relayout', handleRelayout)
  plot.value?.removeEventListener('plotly_selected', handleSelect)
})

const ready = computed(() => created.value && derived.value.layout && app.ui.split.right)

watch(
  () => ready.value,
  () => {
    if (ready.value) {
      console.debug(`📊 [${props.id}] redrawing chart with height ${props.height}`)
      // adapt to changes
      Plotly.react(plot.value, props.data, derived.value.layout, derived.value.config)
      // Relayout to autoranges to fix horizontal-only zoom
      Plotly.relayout(plot.value, {
        'xaxis.autorange': derived.value.layout.xaxis.autorange ? true : false,
        'yaxis.autorange': derived.value.layout.yaxis.autorange ? true : false
      })
    }
  },
  { flush: 'post' }
)

watch(
  () => props.data,
  () => {
    if (ready.value && props.data && plot.value) {
      console.debug(`📊 [${props.id}] updating chart data`)
      Plotly.react(plot.value, props.data, derived.value.layout, derived.value.config)
    }
  },
  { deep: true }
)

watch(
  () => props.layout,
  () => {
    if (ready.value && props.data && plot.value) {
      console.debug(`📊 [${props.id}] updating chart layout`)
      Plotly.relayout(plot.value, derived.value.layout)
    }
  },
  { deep: true }
)
</script>

<template>
  <div
    style="position: relative; width: 100%; height: 100%; min-width: 0"
    :class="props.loading ? 'faded' : ''"
  >
    <div class="overlay" v-if="props.loading">
      <ProgressSpinner />
    </div>
    <div
      ref="plot"
      :id="id"
      class="plot"
      style="width: 100%; height: 100%; min-width: 0"
      :key="`${win.width}-${win.height}-${height}`"
      @contextmenu="
        (e) => {
          e.preventDefault()
        }
      "
    />
    <div class="topleft" v-if="slots.settings">
      <Button
        v-tooltip.right="'Chart settings'"
        severity="secondary"
        text
        @click="
          (event) => {
            settings.toggle(event)
          }
        "
        icon="pi pi-chart-bar"
      />
      <Popover ref="settings">
        <slot name="settings" />
      </Popover>
    </div>
    <div class="bottomleft" v-if="slots.origin">
      <slot name="origin" />
    </div>
  </div>
</template>

<style scoped>
.plot :deep(*) {
  font-family: 'IBM Plex Sans' !important;
}

/* Override Plotly's fixed-width container to allow shrinking */
.plot :deep(.svg-container) {
  width: 100% !important;
  max-width: 100% !important;
}

:deep(.legendtext),
:deep(.icon) > path,
:deep(.gtitle),
:deep(.xtitle),
:deep(.ytitle),
:deep(.ytick) > text,
:deep(.xtick) > text,
:deep(.annotation-text) {
  fill: var(--p-panel-color) !important;
  color: var(--p-panel-color) !important;
}

:deep(.bg) {
  fill: var(--p-chip-background) !important;
  opacity: 0.3;
}

.faded {
  opacity: 0.3;
}

.overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: 100;
  display: grid;
  place-items: center;
}

.topleft {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 50;
}

.bottomleft {
  position: absolute;
  bottom: 0;
  left: 0;
  z-index: 50;
}
</style>
