<script setup>
import { watch } from 'vue'

import SelectButton from 'primevue/selectbutton'

import { useApp } from '@/stores'
import { peakAssignmentEnabled } from '@/lib/features'

import AssignmentRunBar from './AssignmentRunBar.vue'
import MatchCollectionTable from './MatchCollectionTable.vue'
import MatchIonTable from './MatchIonTable.vue'
import PaneBrowserAssignment from './PaneBrowserAssignment.vue'
import PaneBrowserBatchPeaks from './PaneBrowserBatchPeaks.vue'

const app = useApp()

// Coexistence switch: the legacy targeted view vs. the peak-centric assignment
// ledger. Targeted is on a retire path (docs/dev/peak_assignment_frontend.md).
// This bar is the app's only Targets/Assignments control -- the choice lives in
// `app.ui.matchMode` (persisted, and pinned to targets with the flag off), so
// the batch overview chart plots the same paradigm the browser is showing.
//
// The bar also carries the run selector and the Assign-peaks button
// (AssignmentRunBar): both apply to the assignment paradigm as a whole rather
// than to whichever of its two ledgers is on screen, and both were being
// squeezed out of the ledger's own header. They render INSIDE this bar rather
// than as a row beside it, so the feature-flag gate above and the column's
// height arithmetic below keep covering them without a second rule each.

/**
 * Utility function to allow scrolling to matches in the watchers below
 * Lock prevents race conditions when focusing propagates through hierarchy,
 * ensuring only the initially focused level is scrolled to.
 */
let scrollLock = false
const scrollToMatch = (target) => {
  if (!scrollLock && target) {
    scrollLock = true
    setTimeout(() => {
      const element = document.getElementById(
        `match-${target.target_collection_id || target.target_ion_id}`
      )
      element?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      scrollLock = false
    }, 300)
  }
}

/**
 * Watch collection focus changes
 * Clear visualized matches when collection changes or is deselected
 */
watch(
  () => app.data.match.collection.focused,
  (collection, oldCollection) => {
    const collectionChanged =
      collection?.target_collection_id !== oldCollection?.target_collection_id

    // Clear visualized match on collection change or deselection
    if (!collection || collectionChanged) {
      app.data.match.visualized.clear()
    }

    // Clear ion selection when collection changes (but not when first selecting)
    if (collectionChanged && oldCollection) {
      app.data.match.ion.unfocus()
    }

    if (collection) {
      scrollToMatch(collection)
    }
  }
)

/**
 * Watch ion focus changes
 */
watch(
  () => app.data.match.ion.focused,
  (ion) => {
    if (ion) {
      scrollToMatch(ion)
    } else {
      // Clear visualized match when ion is unfocused
      app.data.match.visualized.clear()
    }
  }
)

/**
 * Watch sample changes - re-scroll to current selection
 */
watch(
  () => app.data.sample.focused,
  () => {
    const currentSelection = app.data.match.ion.focused ?? app.data.match.collection.focused
    if (currentSelection) {
      scrollToMatch(currentSelection)
    }
  }
)
</script>

<template>
  <div class="browser-switch">
    <div
      v-if="peakAssignmentEnabled"
      class="switch-bar"
      v-help.bottom="{
        message: `
          <h1>Targets / Assignments</h1>
          <p>
          <b>Targets</b> browses matches against your target collections &mdash;
          the targeted workflow. <b>Assignments</b> browses the peak-centric
          ledgers: the batch peaks of the whole batch, and every peak's
          assignment once a sample is focused.
          </p>`,
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/')
      }"
    >
      <SelectButton
        v-model="app.ui.matchMode.mode"
        :options="app.ui.matchMode.options"
        optionLabel="label"
        optionValue="value"
        :allowEmpty="false"
        size="small"
        aria-label="Targets or assignments"
        v-tooltip.bottom="'Switch between target matches and peak assignments'"
      />
      <AssignmentRunBar v-if="app.ui.matchMode.mode === 'assignments'" />
    </div>
    <template v-if="app.ui.matchMode.mode === 'assignments'">
      <!-- Batch-level batch-peak ledger (selects what the Assignments chart plots)
           at batch level; the per-sample assignments ledger once a sample is
           focused - mirroring how targets swap collection -> ion by focus. -->
      <PaneBrowserBatchPeaks v-if="!app.data.sample.focused" />
      <PaneBrowserAssignment v-else />
    </template>
    <template v-else>
      <MatchIonTable v-if="app.data.match.collection.focused" />
      <MatchCollectionTable v-else />
    </template>
  </div>
</template>

<style scoped>
/* The panes below size themselves from this column, not from the window: the
   switch bar takes its natural height and whatever is left is the pane's, so
   the bar cannot push a table past the bottom of the splitter panel. */
.browser-switch {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
/* One row: the paradigm switch, then the run selector and the Assign-peaks
   button when the assignment paradigm is showing. It wraps rather than
   overflows - the browser column is user-resizable, and a header that clips its
   own last control is the thing this row exists to replace. */
.switch-bar {
  display: flex;
  flex-flow: row wrap;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.35rem;
}
.browser-switch > :not(.switch-bar) {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
