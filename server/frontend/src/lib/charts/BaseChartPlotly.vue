<script setup>
import { ref, shallowRef, computed, onMounted, onBeforeUnmount, toRaw, watch, useSlots } from 'vue'

import Plotly from 'plotly.js-dist-min'

import ProgressSpinner from 'primevue/progressspinner'
import Button from 'primevue/button'
import Popover from 'primevue/popover'

import { useWindowSize } from '@vueuse/core'

import { useApp } from '@/stores'

import { samplesShown, withSampleMarkers } from './samples.js'

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
  // Point indices drawn selected, the same on every trace; null or empty draws
  // no selection.
  selectedPoints: {
    type: Array,
    default: null
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

// The x range and plot-area width as last drawn, which decide whether a trace's
// samples sit far enough apart on screen to be dotted. Plotly keeps what it drew
// on the graph div's full layout; the layout handed in only holds what was
// asked for, which is "autorange" until someone zooms.
const view = shallowRef(null)

function readView() {
  const fullLayout = plot.value?._fullLayout
  const range = fullLayout?.xaxis?.range
  const width = fullLayout?._size?.w
  if (!range || !(width > 0)) return
  const [x0, x1] = range.map(Number)
  const last = view.value
  if (last && last.x0 === x0 && last.x1 === x1 && last.width === width) return
  view.value = { x0, x1, width }
}

// Per trace, whether its samples are dotted: only a trace that asks for it
// (`markSamples`), and only while they sit far enough apart to tell apart. The
// previous array is kept while nothing flips, so a zoom that changes nothing
// here does not redraw anything.
const dotted = computed((previous) => {
  const current = view.value
  const next = (props.data ?? []).map(
    (trace, index) =>
      Boolean(trace.markSamples) &&
      current !== null &&
      samplesShown(trace.x, [current.x0, current.x1], current.width, previous?.[index] ?? false)
  )
  const same = previous?.length === next.length && next.every((shown, i) => shown === previous[i])
  return same ? previous : next
})

// The traces as Plotly gets them, always copies: Plotly writes what the user
// does on the chart into the traces it is handed (a box select's
// selectedpoints, a legend click's visible), and the parent's own trace objects
// are not its to write. `markSamples` is this component's, not a trace
// attribute Plotly knows.
const plotData = computed(() =>
  (props.data ?? []).map((trace, index) => {
    const drawn = { ...toRaw(trace) }
    if (!('markSamples' in drawn)) return drawn
    delete drawn.markSamples
    return dotted.value[index] ? withSampleMarkers(drawn) : drawn
  })
)

/** The selection as it goes on a trace: null for none. */
const selection = () => (props.selectedPoints?.length ? props.selectedPoints : null)

// The `selectedPoints` last drawn, so a selection a redraw already drew is not
// drawn a second time.
let drawnSelection = null

/**
 * The traces for a redraw, carrying the selection. It goes on with the data so
 * that new data is drawn selected in the same pass: restyled on afterwards, the
 * data would show unselected until the restyle landed, and the selection would
 * blink. Written on every redraw, it also replaces what a box select left on
 * the copies.
 */
function selectedData() {
  const selected = selection()
  for (const trace of plotData.value) {
    if (selected) trace.selectedpoints = selected
    else delete trace.selectedpoints
  }
  drawnSelection = props.selectedPoints
  return plotData.value
}

/**
 * The layout for a draw. A copy, because Plotly keeps the layout it is handed
 * and writes a box select's outline into it; and without the outline, because
 * on every redraw Plotly selects the points under each outline it holds, which
 * would put an old box's points back over the selection the traces carry.
 */
const drawnLayout = () => ({ ...derived.value.layout, selections: [] })

/** Redraw the data, selection included. */
function draw() {
  Plotly.react(plot.value, selectedData(), drawnLayout(), derived.value.config)
}

/**
 * Draw a new selection over the data Plotly has. An update rather than a
 * redraw from the traces, so what the user changed on the chart, such as a
 * trace hidden from the legend, stays. It drops a box select's outline as a
 * redraw does.
 */
function drawSelection() {
  drawnSelection = props.selectedPoints
  Plotly.update(plot.value, { selectedpoints: [selection()] }, { selections: [] })
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

// Force Plotly to recalculate container dimensions
const resize = () => {
  if (plot.value) {
    console.debug(`📊 [${props.id}] resizing chart`)
    Plotly.Plots.resize(plot.value)
  }
}

defineExpose({
  resetZoom,
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
  readView()
}

onMounted(() => {
  console.debug(`📊 [${props.id}] creating chart`)
  // create the plot
  Plotly.newPlot(plot.value, selectedData(), drawnLayout(), derived.value.config)
  // add the event listener
  plot.value.on('plotly_click', handleClick)
  plot.value.on('plotly_relayout', handleRelayout)
  plot.value.on('plotly_selected', handleSelect)
  // A full draw ends in afterplot, an update of what is drawn in react; either
  // can move an autoranged axis.
  plot.value.on('plotly_afterplot', readView)
  plot.value.on('plotly_react', readView)
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
  plot.value?.removeEventListener('plotly_afterplot', readView)
  plot.value?.removeEventListener('plotly_react', readView)
})

const ready = computed(() => created.value && derived.value.layout && app.ui.split.right)

watch(
  () => ready.value,
  () => {
    if (ready.value) {
      console.debug(`📊 [${props.id}] redrawing chart with height ${props.height}`)
      // adapt to changes
      draw()
      // Relayout to autoranges to fix horizontal-only zoom
      Plotly.relayout(plot.value, {
        'xaxis.autorange': derived.value.layout.xaxis.autorange ? true : false,
        'yaxis.autorange': derived.value.layout.yaxis.autorange ? true : false
      })
    }
  },
  { flush: 'post' }
)

// New data, or a zoom that brings samples into reach of their dots or out of it
watch(
  [() => props.data, dotted],
  () => {
    if (ready.value && props.data && plot.value) {
      console.debug(`📊 [${props.id}] updating chart data`)
      draw()
    }
  },
  { deep: true }
)

// A new selection over the same data. Watched apart from the data, and
// shallowly, so selecting a sample does not walk every trace's data the way the
// deep watcher above does. After it, too: a reload that brings new data and a
// new selection together has drawn both by then, and draws once.
watch(
  () => props.selectedPoints,
  (selectedPoints) => {
    if (ready.value && props.data?.length && plot.value && selectedPoints !== drawnSelection) {
      drawSelection()
    }
  },
  { flush: 'post' }
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
