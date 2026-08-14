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
// With it on, it becomes the assignment workspace: inspector | spectrum on top,
// assignment time series (or the search) below.
//
// The layouts keep separate saved splits: the legacy one stays on the original
// key so an existing user's saved layout survives, and the assignment layout
// (different panes, different sensible ratio) gets its own.
const splitKey = peakAssignmentEnabled ? 'sample-tab-assign-split' : 'sample-tab-split'
const [initTop, initBottom] = JSON.parse(
  localStorage.getItem(splitKey) ?? (peakAssignmentEnabled ? '[55, 45]' : '[50, 50]')
)

const topSplit = ref(initTop)
const bottomSplit = ref(initBottom)

// Pixel heights of the two rows, used to nudge the Plotly charts to resize when
// the splitter moves. In the assignment layout the top row is split
// horizontally (inspector | spectrum) and the bottom row spans both.
const topHeight = computed(() => ((height.value - padding) * topSplit.value) / 100)
const bottomHeight = computed(() => ((height.value - padding) * bottomSplit.value) / 100)

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
      <template v-if="peakAssignmentEnabled">
        <SplitterPanel :size="55" :minSize="10">
          <Splitter class="top-splitter">
            <SplitterPanel :size="42" class="inspector-panel" :minSize="10">
              <PanePeakAssign v-model:showSearch="showSearch" />
            </SplitterPanel>
            <SplitterPanel :size="58" :minSize="10">
              <ChartSampleSpectrum :height="topHeight" />
            </SplitterPanel>
          </Splitter>
        </SplitterPanel>
        <SplitterPanel :size="45" :minSize="10">
          <PanePeakSearch v-if="showSearch" :height="bottomHeight" @close="showSearch = false" />
          <ChartAssignmentTimeseries v-else :height="bottomHeight" />
        </SplitterPanel>
      </template>
      <template v-else>
        <SplitterPanel :minSize="10">
          <ChartSampleSpectrum :height="legacyTopHeight" />
        </SplitterPanel>
        <SplitterPanel :minSize="10">
          <div class="row">
            <PaneBrowserPeak :height="legacyBottomHeight - 3" />
            <PanePeakSearch :height="legacyBottomHeight - 3" embedded />
          </div>
        </SplitterPanel>
      </template>
    </Splitter>
  </div>
</template>

<style scoped>
.sample-splitter {
  height: 100%;
  width: 100%;
}

.top-splitter {
  height: 100%;
  width: 100%;
  border: none;
}

/* The inspector column scrolls on its own when the assignment card is tall,
   so it never pushes the spectrum out of the top row. */
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
