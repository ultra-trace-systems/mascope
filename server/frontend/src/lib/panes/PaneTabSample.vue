<script setup>
import { ref, computed } from 'vue'

import { useWindowSize } from '@vueuse/core'

import Splitter from 'primevue/splitter'
import SplitterPanel from 'primevue/splitterpanel'

import { PaneBrowserPeak, PanePeakAssign, PanePeakSearch } from '@/lib/panes'
import { ChartSampleSpectrum, ChartAssignmentTimeseries } from '@/lib/charts'
import { peakAssignmentEnabled } from '@/lib/features'

const { height } = useWindowSize()

const padding = 180

// Two layouts share this tab. With peak-centric assignment off, the Sample tab
// is the long-standing spectrum-over-(ledger | composition search) arrangement.
// With it on, it becomes the assignment workspace: the inspector in a column of
// its own, the whole height of the tab, beside the spectrum over the assignment
// time series (or the search).
//
// The layouts keep separate saved splits: the legacy one stays on the original
// key so an existing user's saved layout survives, and the assignment layout
// (different panes, different sensible ratio) gets its own. Its rows - the
// spectrum over what is below it - are saved on that key, and the inspector's
// column on one of its own.
const splitKey = peakAssignmentEnabled ? 'sample-tab-assign-split' : 'sample-tab-split'
const columnsKey = 'sample-tab-assign-columns'
const [initTop, initBottom] = JSON.parse(
  localStorage.getItem(splitKey) ?? (peakAssignmentEnabled ? '[55, 45]' : '[50, 50]')
)

const topSplit = ref(initTop)
const bottomSplit = ref(initBottom)

// Pixel heights of the two rows, used to nudge the Plotly charts to resize when
// the splitter moves. In the assignment layout both rows are the right-hand
// column's; the inspector beside them takes the full height.
const topHeight = computed(() => ((height.value - padding) * topSplit.value) / 100)
const bottomHeight = computed(() => ((height.value - padding) * bottomSplit.value) / 100)

// Moving the column divider changes the charts' width and not their height, so
// it resizes them itself; the row divider reaches them through `height`.
const spectrum = ref(null)
const timeseries = ref(null)
const resizeCharts = () => {
  spectrum.value?.resize()
  timeseries.value?.resize()
}

// The legacy layout passes the raw split percentage as the spectrum height and
// leaves room for the pane chrome below it.
const legacyTopHeight = computed(() => topSplit.value)
const legacyBottomHeight = computed(() => ((height.value - padding) * bottomSplit.value) / 100 - 50)

// The bottom pane shows the assignment time series by default; "Re-search" in
// the inspector flips it to the composition search for the focused peak.
const showSearch = ref(false)
</script>

<template>
  <div class="pane-wrapper">
    <Splitter
      v-if="peakAssignmentEnabled"
      stateStorage="local"
      :stateKey="columnsKey"
      class="sample-splitter"
      @resizeend="resizeCharts"
    >
      <SplitterPanel :size="36" class="inspector-panel" :minSize="15">
        <PanePeakAssign v-model:showSearch="showSearch" />
      </SplitterPanel>
      <SplitterPanel :size="64" :minSize="25">
        <Splitter
          layout="vertical"
          stateStorage="local"
          :stateKey="splitKey"
          class="rows-splitter"
          @resizeend="
            ({ sizes }) => {
              topSplit = sizes[0]
              bottomSplit = sizes[1]
            }
          "
        >
          <SplitterPanel :size="55" :minSize="10">
            <ChartSampleSpectrum ref="spectrum" :height="topHeight" />
          </SplitterPanel>
          <SplitterPanel :size="45" :minSize="10">
            <PanePeakSearch v-if="showSearch" :height="bottomHeight" @close="showSearch = false" />
            <ChartAssignmentTimeseries v-else ref="timeseries" :height="bottomHeight" />
          </SplitterPanel>
        </Splitter>
      </SplitterPanel>
    </Splitter>
    <Splitter
      v-else
      layout="vertical"
      stateStorage="local"
      :stateKey="splitKey"
      class="sample-splitter"
      @resizeend="
        ({ sizes }) => {
          topSplit = sizes[0]
          bottomSplit = sizes[1]
        }
      "
    >
      <SplitterPanel :minSize="10">
        <ChartSampleSpectrum :height="legacyTopHeight" />
      </SplitterPanel>
      <SplitterPanel :minSize="10">
        <div class="row">
          <PaneBrowserPeak :height="legacyBottomHeight - 3" />
          <PanePeakSearch :height="legacyBottomHeight - 3" embedded />
        </div>
      </SplitterPanel>
    </Splitter>
  </div>
</template>

<style scoped>
.sample-splitter {
  height: 100%;
  width: 100%;
}

.rows-splitter {
  height: 100%;
  width: 100%;
  border: none;
}

/* The inspector column scrolls on its own when the assignment card is taller
   than the tab, and never pushes the charts beside it. */
.inspector-panel {
  overflow-y: auto;
  overflow-x: hidden;
}

/* Legacy layout: ledger and composition search side by side, wrapping on narrow
   viewports. */
.row {
  display: flex;
  flex-flow: row wrap;
  height: 100%;
  width: 100%;
  max-width: 100%;
  justify-content: flex-start;
  gap: 0.5rem;
  overflow-x: hidden;
  overflow-y: auto;
}

.row > :deep(*) {
  flex: 1 1 300px;
  min-width: 0;
  max-width: 100%;
}
</style>
