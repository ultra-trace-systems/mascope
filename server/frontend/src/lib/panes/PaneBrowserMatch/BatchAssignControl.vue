<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import Button from 'primevue/button'
import Popover from 'primevue/popover'
import TieredMenu from 'primevue/tieredmenu'

import { useApp } from '@/stores'

import BatchPeakRunSelect from './BatchPeakRunSelect.vue'
import { PHASES, useBatchAssign } from './stores/batchAssign.js'

/**
 * Assign batch: the one control that runs the batch's pass and says where the
 * batch stands (docs/dev/batch_assignment_one_action.md, 3.1 and 3.3).
 *
 * At the right of PaneBrowserMatch's switch bar, where the sample level puts
 * Assign sample: a status block, then a split button. The main half runs what
 * the batch needs - Assign batch, Update assignment, Re-assign, Retry - or
 * Cancel while this person's pass runs, and is gone while somebody else's
 * does, since the server would refuse it. The chevron opens the menu:
 * Re-assign from scratch, Run history, Export ledger, and, behind the user
 * setting *Show assignment stages*, Advanced › Run from a stage.
 *
 * Everything it shows comes from the status read in the store, so two users
 * and two tabs see the same state. Rendered inside the switch bar, so it
 * inherits the bar's feature-flag gate.
 */
const app = useApp()
const assign = useBatchAssign()

// While mounted, a pass's end is reported here, in place, not as a toast.
const release = assign.attach()
onBeforeUnmount(release)

const view = computed(() => assign.view)

// The ledger's empty state carries Assign batch itself; the bar then hides
// its copy, so the two are never on screen together.
const action = computed(() => {
  const act = view.value.action
  if (!act) return null
  if (act.kind === 'assign' && assign.ledgerOffersAssign) return null
  return act
})

const running = computed(() => view.value.state === 'running')

// Why the main half is disabled, shown on its wrapper: a disabled button
// receives no hover of its own. Cancel is this person's own pass, which the
// role that started it may stop.
const disabledReason = computed(() => {
  const act = action.value
  if (!act || act.kind !== 'assign') return null
  return assign.blockedReason
})

function runAction() {
  const act = action.value
  if (!act) return
  if (act.kind === 'cancel') assign.cancel()
  else assign.assign(act.body)
}

// --- The menu ------------------------------------------------------------------

const menu = ref(null)
const history = ref(null)
const chevron = ref(null)

const canStart = computed(() => !assign.blockedReason && !running.value && !assign.launching)

// MAS-983's stages under their phases, each starting the pass there.
const stageItems = computed(() => {
  const stages = assign.status?.stages ?? []
  const items = []
  for (const phase of PHASES) {
    const inPhase = stages.filter((entry) => entry.phase === phase)
    if (!inPhase.length) continue
    items.push({ label: phase, disabled: true, class: 'phase-heading' })
    for (const entry of inPhase) {
      items.push({
        label: entry.label,
        class: 'stage-item',
        disabled: !canStart.value,
        command: () => assign.assign({ from_stage: entry.stage })
      })
    }
  }
  return items
})

const menuItems = computed(() => {
  const items = [
    {
      label: 'Re-assign from scratch',
      icon: 'pi ph ph-arrow-counter-clockwise',
      class: 'from-scratch',
      disabled: !canStart.value,
      command: () => assign.assign({ fold: 'always' })
    },
    {
      label: 'Run history',
      icon: 'pi ph ph-clock-counter-clockwise',
      class: 'run-history',
      disabled: !app.data.batchPeakRun?.list?.length,
      // Until Details lands (MAS-1016), the existing run selector, in a
      // popover off the chevron.
      command: ({ originalEvent }) => history.value?.show(originalEvent, chevron.value?.$el)
    },
    {
      label: 'Export ledger',
      icon: 'pi ph ph-download-simple',
      class: 'export-ledger',
      disabled: !app.data.batchPeak?.list?.length || assign.exporting,
      command: () => assign.exportLedger()
    }
  ]
  if (app.ui.assignmentStages?.show && stageItems.value.length) {
    items.push(
      { separator: true },
      { label: 'Advanced', disabled: true, class: 'advanced-heading' },
      {
        label: 'Run from a stage',
        icon: 'pi ph ph-list-numbers',
        class: 'run-from-stage',
        items: stageItems.value
      }
    )
  }
  return items
})

function toggleMenu(event) {
  menu.value?.toggle(event)
}
</script>

<template>
  <span class="assign-control">
    <!-- Where the batch stands: a dot, the status line and the line under it. -->
    <span
      class="assign-status"
      :class="`tone-${view.tone}`"
      data-testid="assign-status"
      :data-state="view.state ?? ''"
      v-help.bottom="{
        title: 'Batch Assignment',
        helpKey: 'batch-assignment',
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/#assigning-a-batch')
      }"
    >
      <span v-if="running" class="pi pi-spin pi-spinner status-icon" aria-hidden="true" />
      <span v-else-if="view.tone === 'ok'" class="pi ph ph-check status-icon" aria-hidden="true" />
      <span v-else-if="view.state" class="status-dot" aria-hidden="true" />
      <span class="status-text">
        <span class="status-line" data-testid="assign-status-line">{{ view.line }}</span>
        <span v-if="view.detail" class="status-detail" data-testid="assign-status-detail">
          {{ view.detail }}
        </span>
      </span>
    </span>

    <span class="assign-split">
      <span v-if="action" v-tooltip.bottom="disabledReason" class="assign-main-wrap">
        <Button
          class="assign-main"
          data-testid="assign-main"
          :label="action.label"
          :icon="action.kind === 'cancel' ? 'pi ph ph-stop' : 'pi ph ph-magic-wand'"
          size="small"
          :outlined="!action.filled"
          :severity="action.kind === 'cancel' ? 'secondary' : undefined"
          :loading="assign.launching"
          :disabled="Boolean(disabledReason) || Boolean(action.disabled)"
          @click="runAction"
        />
      </span>
      <Button
        ref="chevron"
        class="assign-menu-toggle"
        data-testid="assign-menu-toggle"
        icon="pi ph ph-caret-down"
        size="small"
        :outlined="!action || !action.filled"
        severity="secondary"
        aria-label="More assignment actions"
        aria-haspopup="true"
        @click="toggleMenu"
      />
    </span>
    <TieredMenu ref="menu" :model="menuItems" popup />
    <Popover ref="history">
      <BatchPeakRunSelect />
    </Popover>
  </span>
</template>

<style scoped>
/* Takes the width the switch beside it does not, so the control lands in the
   bar's right corner - where Assign sample lands once a sample is focused. */
.assign-control {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.6rem;
  flex: 1 1 auto;
  min-width: 0;
}
.assign-status {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  min-width: 0;
}
.status-text {
  display: inline-flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.2;
}
.status-line,
.status-detail {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status-detail {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
}
.status-dot {
  flex: none;
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  background: currentColor;
}
.status-icon {
  flex: none;
}
/* The dot's tone. Known compositions only and Not assigned are neutral grey:
   every batch ingest builds starts there, and the accent of behind on all of
   them would mean nothing. */
.tone-neutral .status-dot {
  color: var(--p-text-muted-color);
}
.tone-accent .status-dot {
  color: var(--p-primary-color);
}
.tone-ok .status-icon {
  color: var(--p-green-500);
}
.tone-warn .status-dot {
  color: var(--p-orange-500);
}
/* The two halves read as one split button. */
.assign-split {
  display: inline-flex;
  align-items: stretch;
}
.assign-split > .assign-main-wrap :deep(.p-button) {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}
.assign-split > .assign-main-wrap + .assign-menu-toggle {
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
  margin-left: -1px;
}
/* Take a disabled button out of hit-testing so the wrapper receives the hover
   that shows why. */
.assign-main-wrap :deep(.p-button:disabled) {
  pointer-events: none;
}
</style>
