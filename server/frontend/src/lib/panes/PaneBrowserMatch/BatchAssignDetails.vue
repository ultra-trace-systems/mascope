<script setup>
import { computed, ref, watch } from 'vue'
import Button from 'primevue/button'

import { api } from '@/api'
import { useApp } from '@/stores'
import { runLabel, runStateWords } from '@/stores/data/modules/batchPeak/run.js'

import BatchRunHistory from './BatchRunHistory.vue'
import {
  passChecklist,
  runBefore,
  settingsLines,
  settingsSource,
  tierChanges,
  tierCounts
} from './batchPass.js'
import { useBatchAssign, whenWords } from './stores/batchAssign.js'

/**
 * Assign batch's Details (docs/dev/batch_assignment_one_action.md, 3.2): the
 * pass the batch stands on - the one running, else the newest that stopped,
 * else the last that completed - as a checklist of its stages under the four
 * phases, a one-line result for each that finished, the change it made to
 * the tier counts, the settings it ran under with *Change settings*, which
 * opens Edit assignment settings, and a way into Run history.
 *
 * The stages behind the one button are visible here alone, on demand: a
 * reader who meets a record must still be able to find the stage that wrote
 * it. Everything comes from the status read and the runs listing, and while a
 * pass runs the checklist follows its packets, ahead of both.
 *
 * `view` is which of the two it shows, Details or Run history; the menu's Run
 * history opens it on the second.
 */
const view = defineModel('view', { type: String, default: 'details' })
const emit = defineEmits(['picked', 'change-settings'])

const app = useApp()
const assign = useBatchAssign()
const runs = computed(() => app.data.batchPeakRun)
const status = computed(() => assign.status)

// --- The pass --------------------------------------------------------------------

/** The running batch run is a pass, not a rebuild or an import on its own. */
const runningPass = computed(() => {
  const running = status.value?.running
  if (status.value?.state !== 'running' || !running) return null
  return running.stage != null || running.action === 'assign' ? running : null
})

const passId = computed(() => {
  const st = status.value
  return (
    runningPass.value?.batch_peak_run_id ??
    st?.stopped_pass?.batch_peak_run_id ??
    st?.last_pass?.batch_peak_run_id ??
    null
  )
})

/** The pass's run as the runs route serves it; a running one the listing lacks yet stands in. */
const pass = computed(() => {
  const id = passId.value
  if (!id) return null
  const listed = (runs.value?.list ?? []).find((run) => run.batch_peak_run_id === id) ?? null
  if (listed) return listed
  if (runningPass.value?.batch_peak_run_id === id) {
    return { batch_peak_run_id: id, action: runningPass.value.action, status: 'running' }
  }
  return null
})

const passIsRunning = computed(() => pass.value?.status === 'running')

// The running pass's own process: its stages' packets nest under it, and the
// running stage's progress is theirs.
const processId = computed(() => assign.pendingProcessId ?? runningPass.value?.process_id ?? null)
const percent = computed(() => {
  const id = processId.value
  if (!id) return null
  const child = (app.ui.notification?.progress ?? []).find(
    (proc) => proc.parent_id === id && proc.progress > 0
  )
  return child ? Math.round(child.progress) : null
})

const checklist = computed(() =>
  passChecklist({
    status: status.value,
    run: pass.value,
    live: assign.runningIsMine ? assign.progress : null,
    percent: percent.value
  })
)

const heading = computed(() => {
  const run = pass.value
  if (!run) return null
  const when = whenWords(run.batch_peak_run_utc_created ?? runningPass.value?.utc_started)
  const st = status.value
  const record = [runningPass.value, st?.stopped_pass, st?.last_pass].find(
    (entry) => entry?.batch_peak_run_id === run.batch_peak_run_id
  )
  const by = record?.started_by?.username
  return {
    ordinal: runs.value?.ordinal?.(run),
    label: runLabel(run),
    state: passIsRunning.value ? 'running…' : runStateWords(run),
    meta: [when ? `started ${when}` : null, by ? `by ${by}` : null].filter(Boolean).join(' ')
  }
})

// --- The change to the tier counts --------------------------------------------

// The ledger as the pass found it is the snapshot of the run current as it
// started; as it left it, the live ledger while nothing came after, else its
// own snapshot. The live ledger is the one the pane holds, and while an
// earlier run's is on screen instead no chips are drawn. A run's snapshot is
// captured again each time a run starts while it is current
// (batch_runs._snapshot), so a read is kept by the run and the capture's time
// together: a newer capture is read anew, never served from an older one.
const snapshots = ref({})

/** The cache key of a run's snapshot: its id and when it was captured. */
const snapshotKey = (run) => (run ? `${run.batch_peak_run_id}@${run.snapshot_utc ?? ''}` : null)

async function readSnapshot(run) {
  const key = snapshotKey(run)
  const runId = run.batch_peak_run_id
  if (snapshots.value[key] !== undefined) return
  snapshots.value = { ...snapshots.value, [key]: null }
  const batchId = app.data.batch.focusedId
  try {
    const rows = await api.http.get(`/batch-peaks/batch/${batchId}`, {
      params: { min_n_present: 1, batch_peak_run_id: runId },
      use: 'read',
      type: 'load_batch_peak_ledger',
      errors: 'inline'
    })
    snapshots.value = { ...snapshots.value, [key]: Array.isArray(rows) ? rows : null }
  } catch {
    // No chips, rather than a wrong change.
  }
}

const before = computed(() => runBefore(runs.value?.list, pass.value))
/** Which ledger is what the pass left: 'live', the pass's own run, or null where none says. */
const afterSource = computed(() => {
  const run = pass.value
  if (!run || passIsRunning.value) return null
  if (run.current) return 'live'
  const list = runs.value?.list ?? []
  const later = list.findIndex((r) => r.batch_peak_run_id === run.batch_peak_run_id) > 0
  if (run.status === 'completed') return later ? run : 'live'
  return later ? null : 'live'
})

watch(
  [
    () => snapshotKey(before.value),
    () => (afterSource.value === 'live' ? 'live' : snapshotKey(afterSource.value))
  ],
  () => {
    const after = afterSource.value
    if (!before.value || !after) return
    readSnapshot(before.value)
    if (after !== 'live') readSnapshot(after)
  },
  { immediate: true }
)

const tierChips = computed(() => {
  const beforeRows = before.value ? snapshots.value[snapshotKey(before.value)] : null
  const after = afterSource.value
  let afterRows = null
  if (after === 'live') {
    afterRows = runs.value?.viewingCurrent === false ? null : (app.data.batchPeak?.list ?? null)
  } else if (after) {
    afterRows = snapshots.value[snapshotKey(after)]
  }
  if (!beforeRows || !afterRows) return null
  return tierChanges(tierCounts(beforeRows), tierCounts(afterRows))
})

// --- The settings ----------------------------------------------------------------

const settings = computed(() => settingsLines(pass.value?.config))
const source = computed(() => settingsSource(pass.value?.config))

// --- Words -----------------------------------------------------------------------

const STATE_WORDS = {
  done: 'Done',
  running: 'Running',
  failed: 'Failed',
  cancelled: 'Cancelled',
  stale: 'Out of date',
  waiting: 'Waiting',
  not_run: 'Not run'
}
const STATE_ICONS = {
  done: 'ph ph-check-circle',
  running: 'pi pi-spin pi-spinner',
  failed: 'ph ph-x-circle',
  cancelled: 'ph ph-prohibit',
  stale: 'ph ph-warning-circle',
  waiting: 'ph ph-circle',
  not_run: 'ph ph-minus-circle'
}

function stateWords(row) {
  if (row.state === 'running' && row.percent != null) return `Running · ${row.percent}%`
  if (row.skipped) return 'No rebuild needed'
  return STATE_WORDS[row.state] ?? row.state
}
</script>

<template>
  <div class="assign-details" data-testid="assign-details">
    <template v-if="view === 'details'">
      <div v-if="heading" class="details-heading" data-testid="details-heading">
        <span class="heading-title">
          <span v-if="heading.ordinal != null" class="heading-ordinal">#{{ heading.ordinal }}</span>
          {{ heading.label }} · {{ heading.state }}
        </span>
        <span v-if="heading.meta" class="heading-meta">{{ heading.meta }}</span>
      </div>
      <p v-else class="details-empty">No pass has run on this batch yet.</p>

      <div v-if="heading" class="checklist" data-testid="checklist">
        <section v-for="group in checklist" :key="group.phase" class="phase">
          <div class="phase-name">{{ group.phase }}</div>
          <ul class="stages">
            <li
              v-for="row in group.stages"
              :key="row.stage"
              class="stage"
              :class="`state-${row.state}`"
              :data-stage="row.stage"
              :data-state="row.state"
            >
              <span class="stage-icon" :class="STATE_ICONS[row.state]" aria-hidden="true" />
              <span class="stage-body">
                <span class="stage-line">
                  <span class="stage-label">{{ row.label }}</span>
                  <span class="stage-state" data-testid="stage-state">{{ stateWords(row) }}</span>
                </span>
                <span v-if="row.result" class="stage-note" data-testid="stage-result">
                  {{ row.result }}
                </span>
                <span v-if="row.error" class="stage-note error" data-testid="stage-error">
                  {{ row.error }}
                </span>
                <span v-if="row.reason" class="stage-note" data-testid="stage-reason">
                  {{ row.reason }}
                </span>
              </span>
            </li>
          </ul>
        </section>
      </div>

      <div v-if="tierChips?.length" class="tier-changes" data-testid="tier-changes">
        <span
          v-for="chip in tierChips"
          :key="chip.tier"
          class="tier-change"
          :class="[chip.tier, chip.delta > 0 ? 'up' : 'down']"
        >
          {{ chip.label }}
        </span>
      </div>
      <p v-else-if="tierChips" class="tier-changes none" data-testid="tier-changes">
        No change in the tier counts
      </p>

      <div v-if="settings.length" class="settings" data-testid="pass-settings">
        <div class="settings-heading">
          Settings
          <span v-if="source" class="settings-source">{{ source }}</span>
          <Button
            class="change-settings"
            data-testid="change-settings"
            label="Change settings"
            link
            size="small"
            @click="emit('change-settings')"
          />
        </div>
        <dl>
          <template v-for="line in settings" :key="line.label">
            <dt>{{ line.label }}</dt>
            <dd>{{ line.value }}</dd>
          </template>
        </dl>
      </div>

      <div class="details-footer">
        <Button
          class="to-history"
          data-testid="to-history"
          label="Run history"
          link
          size="small"
          @click="view = 'history'"
        />
      </div>
    </template>

    <template v-else>
      <Button
        class="to-details"
        data-testid="to-details"
        label="Details"
        icon="pi ph ph-caret-left"
        link
        size="small"
        @click="view = 'details'"
      />
      <BatchRunHistory @picked="(run) => emit('picked', run)" />
    </template>
  </div>
</template>

<style scoped>
.assign-details {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  min-width: 22rem;
  max-width: 30rem;
}
.details-heading {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.heading-title {
  font-weight: 600;
}
.heading-ordinal,
.heading-meta,
.details-empty {
  color: var(--p-text-muted-color);
  font-size: 0.85rem;
}
.details-empty {
  margin: 0;
}
.checklist {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.phase-name {
  font-size: 0.8rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--p-text-muted-color);
}
.stages {
  list-style: none;
  margin: 0.15rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.stage {
  display: flex;
  align-items: flex-start;
  gap: 0.45rem;
}
.stage-icon {
  flex: none;
  margin-top: 0.15rem;
}
.stage-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}
.stage-line {
  display: flex;
  justify-content: space-between;
  gap: 0.6rem;
}
.stage-state {
  font-size: 0.85rem;
  color: var(--p-text-muted-color);
  white-space: nowrap;
}
.stage-note {
  font-size: 0.8rem;
  color: var(--p-text-muted-color);
}
.stage-note.error {
  color: var(--p-red-500);
  overflow-wrap: anywhere;
}
.state-done .stage-icon {
  color: var(--p-green-500);
}
.state-failed .stage-icon,
.state-cancelled .stage-icon,
.state-stale .stage-icon {
  color: var(--p-orange-500);
}
.state-waiting,
.state-not_run {
  color: var(--p-text-muted-color);
}
.tier-changes {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin: 0;
}
.tier-changes.none {
  font-size: 0.85rem;
  color: var(--p-text-muted-color);
}
.tier-change {
  font-size: 0.8rem;
  padding: 0.1rem 0.45rem;
  border-radius: 999px;
  background: var(--p-content-hover-background);
}
.tier-change.assigned.up {
  color: var(--p-green-600);
}
.settings-heading {
  display: flex;
  align-items: baseline;
  font-weight: 600;
  font-size: 0.9rem;
}
.settings-heading .change-settings {
  margin-left: auto;
  padding: 0;
}
.settings-source {
  font-weight: normal;
  color: var(--p-text-muted-color);
  margin-left: 0.4rem;
  font-size: 0.8rem;
}
.settings dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.1rem 0.8rem;
  margin: 0.2rem 0 0;
  font-size: 0.85rem;
}
.settings dt {
  color: var(--p-text-muted-color);
}
.settings dd {
  margin: 0;
}
.details-footer {
  display: flex;
  justify-content: flex-end;
}
.to-details {
  align-self: flex-start;
}
</style>
