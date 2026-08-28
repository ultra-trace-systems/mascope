<script setup>
import { ref, inject, computed, watch, onScopeDispose } from 'vue'

import Button from 'primevue/button'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Select from 'primevue/select'
import { FilterMatchMode, FilterOperator } from '@primevue/core/api'

import { getApiErrorMessage, isRefusedRequest } from '@/api/utils'
import { BaseTabbedPanel, BaseTierTag, BaseCopyableField } from '@/lib/base'
import { num } from '@/lib/formatters'
import { canEditWorkspace } from '@/lib/permissions'
import { TIERS, TIER_META, tierRank } from '@/lib/tiers'
import { prettyTrim } from '@/lib/utils'
import { api } from '@/api'
import { useApp } from '@/stores'

/**
 * Batch-peak ledger: the selection surface for the peak-centric batch overview.
 * A virtual-scrolled, multi-select table of the batch's batch peaks (cross-sample
 * m/z anchors); the rows the user selects here are exactly what the Assignments
 * chart plots, so the chart never renders 1000+ traces at once.
 */
const app = useApp()
const table = ref(null)
const tableHeight = inject('match-table-height', ref(300))
const computing = ref(false)

// What the tier strip above the table costs it, and the table's own height with
// that taken off. Floored at zero: the pane's height is a share of the window,
// so dragging the splitter to its minimum on a short screen leaves less than
// the strip's own height - and a negative one is not a length, so the browser
// drops the declaration and the virtual scroller sizes itself off a height the
// layout never set.
const TIER_STRIP_HEIGHT = 60
const tableScrollHeight = computed(() => Math.max(0, tableHeight.value - TIER_STRIP_HEIGHT))

const ledger = computed(() => app.data.batchPeak)

const filters = ref({
  consensus_formula: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.CONTAINS }]
  },
  consensus_tier: {
    operator: FilterOperator.AND,
    constraints: [{ value: null, matchMode: FilterMatchMode.EQUALS }]
  }
})

const breadcrumb = computed(() => {
  const batch = app.data.batch.focused
  if (!batch) return null
  return {
    items: [
      {
        icon: 'pi pi-hashtag',
        label: prettyTrim(batch.sample_batch_name, 25),
        disabled: true,
        tooltip: `Batch peaks for ${batch.sample_batch_name}`
      },
      {
        icon: 'pi ph ph-atom',
        label: `${ledger.value.list.length} batch peaks`,
        disabled: true
      }
    ]
  }
})

// --- Rows -------------------------------------------------------------------

// The ledger's records with the tier's confidence rank attached, which is what
// the tier column sorts on: the raw tier string sorts alphabetically, and
// "below_assignability" before "identified" is not an ordering anyone asked for.
//
// Ordered here by tier and then by fit descending. The table applies its own
// sort on top and Array.prototype.sort is stable, so this ordering survives as
// the tie-break: equal tiers come out ordered by the very percentage the chip
// beside them shows.
const rows = computed(() =>
  ledger.value.list
    .map((batchPeak) => ({ ...batchPeak, tierRank: tierRank(batchPeak.consensus_tier) }))
    .sort((a, b) => a.tierRank - b.tierRank || (b.best_fit_score ?? -1) - (a.best_fit_score ?? -1))
)

// Ctrl+A selects all filtered rows (the virtual scroller only holds visible rows).
const onKeyDown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
    event.preventDefault()
    const selectable = table.value?.processedData ?? rows.value
    ledger.value.selected = [...selectable]
  }
}

// --- Tier strip -------------------------------------------------------------

// The chips and the tier column's filter menu drive the same filter rather than
// two: a chip writes the constraint the menu reads, so the two controls cannot
// contradict each other and leave the table empty for a reason only one of them
// is showing. That does make the chips single-select, unlike the sample
// ledger's - one EQUALS constraint holds one tier - which is the price of the
// two staying honest about each other.
const tierConstraint = computed(() => filters.value.consensus_tier.constraints[0])
const activeTier = computed(() => tierConstraint.value.value)
const toggleTier = (tier) => {
  tierConstraint.value.value = activeTier.value === tier ? null : tier
}

// One chip per tier in confidence order, counts included. Counts come from the
// whole ledger rather than the filtered rows, as the sample pane's do: a
// histogram that reacted to its own filter would collapse to one non-zero
// bucket the moment it was used.
const tierChips = computed(() =>
  TIERS.map((tier) => ({
    key: tier,
    label: TIER_META[tier].label,
    count: ledger.value.tierCounts[tier] ?? 0
  }))
)

// --- Compute batch peaks ----------------------------------------------------

// Cleared on the notification the background task sends when it ends. A dropped
// socket would otherwise strand the button in its loading state forever, which
// is a worse failure than the premature reset this replaced, so the wait is
// bounded. Generous: the backfill folds every sample of the batch in turn.
const COMPUTE_TIMEOUT = 5 * 60 * 1000

const pendingProcessId = ref(null)
let computeTimer = null

const launchError = ref(null)
const launchRefused = ref(false)

// Writing batch peaks needs the editor role on the batch's workspace, which is
// the focused one - datasets, and so batches, load per focused workspace. The
// helper answers "yes" while the account or the workspace is still loading, so
// a slow load offers the button rather than hiding a capability the user has.
const canCompute = computed(() => canEditWorkspace(app.data.workspace.focused, app.auth.user))

// Why the button cannot run right now, or null when it can. One computed rather
// than a chain of conditions on the button, so what is disabled and the reason
// shown for it cannot disagree.
//
// "No completed assignment runs in this batch" is the condition that actually
// matters and it is not knowable here - no loaded record carries a per-sample
// run status - so the honest client-side stand-in is the batch having no
// samples at all. A batch that has samples but no completed runs still reaches
// the backend, which now reports folding nothing as a warning instead of
// announcing it green.
const blockedReason = computed(() => {
  if (!app.data.batch.focusedId) return 'Select a batch to compute its batch peaks.'
  if (!canCompute.value) {
    return 'Computing batch peaks writes to the batch, so it needs the editor role in this workspace.'
  }
  if (!app.data.sample.pending && !app.data.sample.list.length) {
    return 'This batch has no samples yet, so there is nothing to fold into batch peaks.'
  }
  return null
})

const computeTooltip = computed(
  () => blockedReason.value ?? "Build / refresh batch peaks from this batch's assignments"
)

function endComputing() {
  computing.value = false
  pendingProcessId.value = null
  clearTimeout(computeTimer)
  computeTimer = null
}

/** Backfill batch peaks from this batch's existing assignments; the ledger and
 *  chart refresh on the peak_assignment_reload event the task emits. */
async function computeBatchPeaks() {
  const batchId = app.data.batch.focusedId
  if (!batchId || computing.value || blockedReason.value) return
  computing.value = true
  launchError.value = null
  clearTimeout(computeTimer)
  computeTimer = setTimeout(endComputing, COMPUTE_TIMEOUT)
  try {
    // No `use` handler: the acknowledgement's process id rides on the
    // `Process-ID` response header (the route pops it out of the body), and
    // both the `read` and `process` handlers throw the raw response away. The
    // id is what tells this pane's completion notification apart from someone
    // else's backfill of the same batch, which lands in the same socket room.
    const response = await api.http.post(
      `/batch-peaks/batch/${batchId}/backfill`,
      {},
      // `errors: 'inline'` holds back the interceptor's toast: the failure is
      // reported once, below the menu that caused it.
      { type: 'backfill_batch_peaks', errors: 'inline' }
    )
    pendingProcessId.value = response?.headers?.['process-id'] ?? null
  } catch (error) {
    // The launch was refused or failed, so nothing is running and the button
    // goes back to offering the action rather than pretending to perform it.
    endComputing()
    // A refusal is shown as a warning rather than an error: the server decided
    // this on purpose and said why. 403 counts as one here on top of the shared
    // helper's 409/422 - it is the refusal this route actually issues, from the
    // editor-role check and the feature flag, and it is what a role revoked
    // mid-session looks like. Anything else is a fault.
    launchRefused.value = isRefusedRequest(error) || error?.response?.status === 403
    launchError.value = getApiErrorMessage(error, 'Could not start the batch peak computation.')
  }
}

// The task's own notification is the only signal that the work finished - the
// 202 says only that it started. It is named for the controller that emits it
// (compute_batch_peaks), not for the request that launched it, and it arrives
// for a failure as well as a success, so the button leaves its loading state
// either way. Registered in setup scope, so it unregisters with the pane.
app.ui.notification.on('compute_batch_peaks', (notification) => {
  if (!computing.value) return
  // This task emits no in-progress packets today, but a "still working" one
  // would say nothing about the button's state if it ever did.
  if (notification?.status === 'pending') return
  // A packet whose id we can read and that is not ours belongs to someone
  // else's backfill of this batch. One we cannot identify is accepted rather
  // than ignored: leaving the button spinning would be the worse guess.
  const id = notification?.process_id
  if (pendingProcessId.value && id && id !== pendingProcessId.value) return
  endComputing()
})

// Whatever was in flight, or went wrong, belonged to the previous batch.
watch(
  () => app.data.batch.focusedId,
  () => {
    endComputing()
    launchError.value = null
  }
)

onScopeDispose(() => clearTimeout(computeTimer))
</script>

<template>
  <BaseTabbedPanel
    :breadcrumb="breadcrumb"
    :loading="ledger.pending"
    :error="ledger.error"
    :onRetry="() => ledger.load('retry')"
    :pt="
      app.ui.help.right(
        `
        <h1>Batch Peak Ledger</h1>
        <p>
        Cross-sample m/z anchors for the batch: each row is a species seen
        across samples, with its consensus formula and tier and the number of
        samples it appears in.
        </p>
        <p>
        The rows selected here are exactly what the Assignments chart plots
        &mdash; Ctrl+A selects all filtered rows. Focus a sample to switch to
        its per-sample assignment ledger.
        </p>`,
        { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
      )
    "
  >
    <template #menu>
      <!-- The tooltip hangs off the wrapper, not the button: a disabled button
           receives no mouse events, so a reason attached to it would be
           readable only in the one state where it says nothing. -->
      <span
        class="compute-button"
        :class="{ blocked: blockedReason !== null }"
        v-tooltip.left="computeTooltip"
      >
        <Button
          label="Compute batch peaks"
          icon="ph ph-arrows-clockwise"
          size="small"
          severity="secondary"
          :loading="computing"
          :disabled="blockedReason !== null"
          @click="computeBatchPeaks"
          :pt="
            app.ui.help.left(`
              <h1>Compute Batch Peaks</h1>
              <p>
              Builds or refreshes the batch peaks from the assignment runs this
              batch's samples already have &mdash; no new assignment work. New
              runs fold in automatically; use this to populate a batch assigned
              before batch peaks existed, or to refresh after an import.
              </p>
            `)
          "
        />
      </span>
    </template>

    <div class="col" style="gap: 0.6rem; align-items: stretch">
      <!-- A launch that was refused or failed reports itself here rather than in
           a toast that has scrolled away by the time the user looks up. -->
      <Message
        v-if="launchError"
        :severity="launchRefused ? 'warn' : 'error'"
        closable
        @close="launchError = null"
      >
        {{ launchError }}
      </Message>

      <div
        class="tier-strip"
        v-help.top="{
          title: 'Confidence Tiers',
          helpKey: 'assignment-tiers',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
        }"
      >
        <button
          v-for="chip in tierChips"
          :key="chip.key"
          type="button"
          class="tier-stat"
          :class="[
            chip.key,
            { active: activeTier === chip.key, dim: activeTier && activeTier !== chip.key }
          ]"
          v-tooltip.top="
            activeTier === chip.key ? `Showing only ${chip.label}` : `Filter to ${chip.label}`
          "
          @click="toggleTier(chip.key)"
        >
          <b>{{ chip.count }}</b> {{ chip.label }}
        </button>
      </div>

      <DataTable
        ref="table"
        :value="rows"
        dataKey="batch_peak_id"
        v-model:selection="ledger.selected"
        v-model:filters="filters"
        selectionMode="multiple"
        :metaKeySelection="false"
        @keydown="onKeyDown"
        filterDisplay="menu"
        resizableColumns
        size="small"
        scrollable
        :scrollHeight="`${tableScrollHeight}px`"
        :virtualScrollerOptions="{ itemSize: 35.74 }"
        sortField="n_present"
        :sortOrder="-1"
        :pt="
          app.ui.help.top(
            { title: 'Batch Peaks', helpKey: 'batch-peaks' },
            { doc: app.ui.help.docUrl('how-it-works/peak-assignment/#batch-peaks') }
          )
        "
      >
        <template #empty>
          No batch peaks yet - run "Compute batch peaks" (or assign the batch) to populate.
        </template>

        <Column selectionMode="multiple" style="width: 3rem" />

        <Column field="mz" header="m/z" sortable style="min-width: 7rem">
          <template #body="{ data }">{{ num.mz.format(data.mz) }}</template>
        </Column>

        <Column field="consensus_formula" header="Formula" sortable style="min-width: 9rem">
          <template #body="{ data }">
            <BaseCopyableField v-if="data.consensus_formula" :field="data.consensus_formula" />
            <span v-else class="unassigned">unassigned</span>
          </template>
          <template #filter="{ filterModel, filterCallback }">
            <InputText
              v-model="filterModel.value"
              @input="filterCallback()"
              placeholder="Search formula..."
              size="small"
            />
          </template>
        </Column>

        <!-- `field` stays the tier so the filter menu keeps its constraint;
             `sortField` is what the header click sorts on, which is how the
             column orders by confidence without the filter losing its binding.

             One constraint, and no button to add a second: the menu defaults to
             offering "Add Rule" up to two rules, and a second rule is one the
             chips above can neither show nor clear - two EQUALS tiers ANDed
             together also match nothing, so the table would empty out while a
             chip still claimed to be showing a tier. -->
        <Column
          field="consensus_tier"
          sortField="tierRank"
          sortable
          style="min-width: 8rem"
          :filterMatchModeOptions="[{ label: 'Equals', value: 'equals' }]"
          :maxConstraints="1"
          :showAddButton="false"
          :showOperator="false"
        >
          <template #header>
            <span
              v-help.top="{
                title: 'Confidence Tiers',
                helpKey: 'assignment-tiers',
                doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
              }"
              >Tier</span
            >
          </template>
          <template #body="{ data }">
            <BaseTierTag :tier="data.consensus_tier" :fit-score="data.best_fit_score" />
          </template>
          <template #filter="{ filterModel, filterCallback }">
            <Select
              v-model="filterModel.value"
              @change="filterCallback()"
              :options="TIERS"
              placeholder="Any tier"
              size="small"
              :showClear="true"
            />
          </template>
        </Column>

        <Column
          field="n_present"
          header="Samples"
          sortable
          style="min-width: 6rem"
          v-tooltip="'Number of samples this species is seen in'"
        >
          <template #body="{ data }">{{ data.n_present }}</template>
        </Column>
      </DataTable>
    </div>
  </BaseTabbedPanel>
</template>

<style scoped>
.unassigned {
  color: var(--p-text-muted-color, #888);
  font-style: italic;
}

/* Take the disabled button out of hit-testing so the wrapper above it receives
   the hover that shows why it is disabled. */
.compute-button {
  display: inline-flex;
}
.compute-button.blocked :deep(.p-button) {
  pointer-events: none;
}

/* Matches the sample ledger's strip (PaneBrowserAssignment.vue): the two sit in
   the same tab position, and a reader switching between a sample and its batch
   should recognize the same control. */
.tier-strip {
  display: flex;
  flex-flow: row wrap;
  gap: 0.3rem;
  padding: 0 0.4rem;
}
.tier-stat {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 100px;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  font-variant-numeric: tabular-nums;
  /* button reset */
  background: transparent;
  color: inherit;
  font-family: inherit;
  cursor: pointer;
  transition:
    opacity 0.12s,
    border-color 0.12s,
    background 0.12s;
}
.tier-stat:hover {
  border-color: var(--p-primary-color, #6366f1);
}
.tier-stat.active {
  border-color: var(--p-primary-color, #6366f1);
  background: color-mix(in srgb, var(--p-primary-color, #6366f1) 12%, transparent);
}
.tier-stat.dim {
  opacity: 0.4;
}
.tier-stat b {
  font-weight: 700;
}
.tier-stat.identified b {
  color: var(--p-green-600, #1f9d63);
}
.tier-stat.candidate b {
  color: var(--p-amber-600, #c9861f);
}
.tier-stat.below_assignability b,
.tier-stat.unassigned b {
  color: var(--p-surface-500, #6f7889);
}
</style>
