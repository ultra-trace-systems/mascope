<script setup>
import { computed } from 'vue'

import Button from 'primevue/button'
import Select from 'primevue/select'

import { BaseRunProvenance } from '@/lib/base'
import { useApp } from '@/stores'

import { useAssignmentLauncher } from './stores'

/**
 * Which run the assignment ledger is showing, and the way to start another one.
 *
 * Both used to sit in the ledger's own panel header, beside the view controls,
 * which left four controls competing for a pane that is often half a window
 * wide - the run selector was the first to be squeezed out of it. They belong a
 * row up regardless: the run is what the whole Assignments paradigm is reading,
 * the same way the Targets/Assignments switch beside them picks the paradigm
 * itself, and both outlive whichever ledger happens to be on screen.
 *
 * Rendered inside PaneBrowserMatch's switch bar, so it inherits the bar's
 * feature-flag gate rather than carrying a second copy of it.
 */
const app = useApp()
const launcher = useAssignmentLauncher()

const runs = computed(() => app.data.peakAssignment.run)

// One dropdown option per run, labelled with its ordinal and status. The
// ellipsis marks only runs still in flight - failed/cancelled runs are done,
// just not completed (mirrors the backend's non-terminal statuses).
const IN_FLIGHT_STATUSES = ['pending', 'running', 'importing']

// Label a run from the run itself rather than from a property carried on the
// option copy. The `#value` slot is handed the raw v-model value - the record
// straight off the run store - not the matched option, so a label that lived
// only on the option would render blank in the closed selector.
function runLabel(run) {
  if (!run) return ''
  const list = runs.value.list
  const index = list.findIndex((r) => r.peak_assignment_run_id === run.peak_assignment_run_id)
  const ordinal = index === -1 ? '' : `#${list.length - index} · `
  return `${ordinal}${run.status}${IN_FLIGHT_STATUSES.includes(run.status) ? '…' : ''}`
}

const runOptions = computed(() => runs.value.list.map((run) => ({ ...run, _label: runLabel(run) })))

const selectedRun = computed({
  get: () => runs.value.focused,
  set: (run) => (run ? runs.value.focus(run) : runs.value.unfocus())
})

// The run store only loads for a focused sample, so at batch level there is
// nothing to select and nothing to add to - the bar collapses to the switch
// alone rather than offering a dead "Select run" box over the batch ledger.
//
// The button outlives the selector by one state: a run list that failed to
// load has no empty state to carry the call to action, and a failed load must
// not also cost the user the way to start a run. Its counterpart in the
// ledger's empty state (PaneBrowserAssignment) fills in for the other
// direction, so exactly one of the two is offered at any time.
const canAssign = computed(() => runs.value.list.length > 0 || Boolean(runs.value.error))
</script>

<template>
  <div class="run-bar">
    <Select
      v-if="runOptions.length"
      v-model="selectedRun"
      :options="runOptions"
      optionLabel="_label"
      dataKey="peak_assignment_run_id"
      size="small"
      placeholder="Select run"
      ariaLabel="Assignment run"
      class="run-select"
      :pt="
        app.ui.help.bottom(
          { title: 'Assignment Runs', helpKey: 'assignment-runs' },
          { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#assignment-runs') }
        )
      "
    >
      <!-- Which engine produced the run travels with the run itself, in
           both the closed selector and the open list: a published run is
           first-class and the ledger defaults to the newest completed run
           whatever produced it, so the engine has to be visible wherever a
           run is named rather than only after opening a menu. -->
      <template #value="{ value, placeholder }">
        <span v-if="value" class="run-option">
          <span class="run-name">{{ runLabel(value) }}</span>
          <BaseRunProvenance :run="value" compact />
        </span>
        <span v-else>{{ placeholder }}</span>
      </template>
      <template #option="{ option }">
        <span class="run-option">
          <span class="run-name">{{ option._label }}</span>
          <BaseRunProvenance :run="option" />
        </span>
      </template>
    </Select>

    <Button
      v-if="canAssign"
      label="Assign peaks"
      icon="pi ph ph-magic-wand"
      size="small"
      :disabled="!app.data.sample.focused"
      @click="launcher.open()"
      :pt="
        app.ui.help.bottom(
          `
          <h1>Assign Peaks</h1>
          <p>
          Launches an assignment run for this sample: every peak is matched
          against the known target library first, then optionally through the
          untargeted composition search. Opens the run configuration.
          </p>`,
          { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-two-stages') }
        )
      "
    />
  </div>
</template>

<style scoped>
.run-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

/* Shrinkable rather than fixed: this row is as wide as the browser column,
   which the user can drag narrow. The label ellipses inside (.run-name) so a
   squeezed selector loses characters instead of pushing the button out of the
   bar - which is what the ledger header used to do. */
.run-select {
  flex: 0 1 15rem;
  min-width: 7rem;
}

/* Run label + its provenance chips, in the closed selector and the open list.
   Applied inside Select's teleported overlay too, so these rules have to live
   wherever the Select is declared. */
.run-option {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  min-width: 0;
}
.run-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
