<script setup>
import ProgressSpinner from 'primevue/progressspinner'
import Message from 'primevue/message'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Listbox from 'primevue/listbox'
import { useConfirm } from 'primevue/useconfirm'
import { num } from '@/lib/formatters'

import { computed, watch, reactive, ref, watchEffect } from 'vue'

import { api } from '@/api'
import { useApp } from '@/stores'
import { useMzFit } from '@/lib/mzFit'
import { PaneSettingsCalibration } from '@/lib/panes'
import { canCalibrateInstruments } from '@/lib/permissions'

const mzFit = useMzFit()
const confirm = useConfirm()

const app = useApp()
const layer = 'dialog_calibration' // Help-mode layer for dialog

const visible = defineModel('visible')

const props = defineProps({
  context: {
    type: Object
  }
})

watch(visible, (value) => {
  if (!value) {
    app.ui.help.set(null)
  } else {
    app.ui.help.set(layer)
  }
})

const original = computed(() => props.context ?? app.data.sample.focused)
const batch = computed(() => (original.value?.sample_item_id ? null : original.value))
const samples = ref(null)
const previewSample = ref()
watchEffect(async () => {
  samples.value =
    batch.value && visible.value
      ? await api.http.get(`/samples`, {
          params: {
            sample_batch_id: batch.value.sample_batch_id,
            batch_matches_info: false,
            sort: 'datetime_utc'
          },
          use: 'read',
          type: 'load_samples'
        })
      : null
  previewSample.value = samples.value?.length > 0 ? { ...samples.value[0] } : null
})

// Gating the action here rather than only at the context menu covers every way
// in - the sample and batch menus, and the batch status icon, which opens this
// dialog directly. For a batch the instruments come from the samples this
// dialog already loads, so the answer is exact rather than best-effort.
const canCalibrate = computed(() => {
  const instruments = batch.value
    ? (samples.value ?? []).map((sample) => sample?.instrument)
    : [original.value?.instrument]
  return canCalibrateInstruments(
    app.data.workspace.list,
    app.auth.user,
    instruments.filter(Boolean)
  )
})

const state = reactive({
  previous: null
})

// --- Skipping calibration -------------------------------------------------
// A file that will never be calibrated (a blank, or an ionization mode with no
// calibrant collection) otherwise leaves the badge on an ambiguous blank. The
// marker replaces it with an explicit, attributed statement; it is file-scoped
// and admin-gated exactly like applying a fit, so it rides on `canCalibrate`.

// Mirrors CALIBRATION_SKIP_REASON_MAX_LENGTH on the backend, which rejects a
// longer label - stopping the input here means the request is never sent.
const SKIP_REASON_MAX_LENGTH = 200

const skipState = reactive({
  open: false,
  reason: '',
  busy: false
})

// Only for a single sample: the marker is written per file, and a batch-wide
// skip would need its own confirmation over every file the batch draws on.
const skippable = computed(() => !batch.value && !!original.value?.filename)

const skipped = computed(() => original.value?.mz_calibration?.status === 'skipped')

const skippedSummary = computed(() => {
  const record = original.value?.mz_calibration
  if (!record) return ''
  const by = record.skipped_by ? ` by ${record.skipped_by}` : ''
  return `Calibration skipped${record.reason ? `: ${record.reason}` : ''}.${by ? ` Marked${by}.` : ''}`
})

watch(visible, (active) => {
  if (!active) {
    skipState.open = false
    skipState.reason = ''
  }
})

async function confirmSkip() {
  const reason = skipState.reason.trim()
  if (!reason || skipState.busy) return
  skipState.busy = true
  try {
    await mzFit.skip(original.value, reason)
    skipState.open = false
    visible.value = false
  } finally {
    skipState.busy = false
  }
}

async function clearSkip() {
  if (skipState.busy) return
  skipState.busy = true
  try {
    await mzFit.unskip(original.value)
    visible.value = false
  } finally {
    skipState.busy = false
  }
}

const title = computed(() =>
  batch.value
    ? `Calibrate batch "${original.value?.sample_batch_name}"`
    : `Calibrate sample "${original.value?.sample_item_name}"`
)

const confirmMessage = computed(() => {
  if (batch.value) {
    return `Applying calibration will remove matches for all associated samples in this and other batches. 
    This action cannot be undone. Are you sure you want to proceed?`
  }

  const batchCount = mzFit.affectedBatches?.length || 0
  const sampleCount = mzFit.affectedSamples?.length || 0

  if (batchCount > 0 || sampleCount > 0) {
    return `Applying calibration to this file will remove matches for ${sampleCount} associated 
    sample${sampleCount !== 1 ? 's' : ''} across ${batchCount} batch${batchCount !== 1 ? 'es' : ''}. 
    This action cannot be undone. Are you sure you want to proceed?`
  }

  return 'Applying calibration will affect other samples and remove existing matches. This action cannot be undone. Are you sure you want to proceed?'
})

// component initialization logic
watch(visible, init)
async function init(active) {
  if (active) {
    // Seed instrument-appropriate parameter defaults for the sample before
    // snapshotting, so the change does not read as a user edit (which would
    // trigger a second fit through the unsynced watcher).
    const sample = batch.value ? previewSample.value : original.value
    if (sample?.sample_item_id) {
      await mzFit.loadInstrumentDefaults(sample)
    }
    // reset state
    state.previous = { ...mzFit.mzCalibrationParams }
    refit()
  }
}

const unsynced = computed(() =>
  Object.keys(state.previous).some((key) => state.previous[key] !== mzFit.mzCalibrationParams[key])
)

watch(previewSample, async (sample) => {
  if (sample?.sample_item_id) {
    await mzFit.loadInstrumentDefaults(sample)
    // Absorb any default changes into the snapshot so the unsynced watcher
    // does not treat them as a user edit and fire a second fit.
    state.previous = { ...mzFit.mzCalibrationParams }
  }
  refit()
})

watch(
  () => mzFit.mzCalibrationParams,
  () => {
    if (unsynced.value && visible.value) {
      refit()
    }
  },
  { deep: true }
)

async function refit() {
  const sample = batch.value ? previewSample.value : original.value
  if (sample && visible.value) {
    await mzFit.compute(sample)
  }
  state.previous = { ...mzFit.mzCalibrationParams }
}

async function save() {
  confirm.require({
    icon: 'pi pi-exclamation-triangle',
    header: 'Confirm calibration',
    message: confirmMessage.value,
    accept: async () => {
      if (batch.value) {
        await api.http.post(
          `/calibration/mz_calibrate/batch/${original.value.sample_batch_id}`,
          mzFit.mzCalibrationParams,
          {
            use: 'process',
            type: 'recalibrate_batch'
          }
        )
      } else {
        await mzFit.apply(original.value)
      }
      visible.value = false
    },
    acceptProps: {
      label: 'Apply Calibration'
    },
    rejectProps: {
      label: 'Cancel',
      severity: 'secondary'
    }
  })
}

const calibration = computed(() => ({
  key: 0,
  rows: mzFit.stats ?? [],
  columns: [
    { field: 'mz', label: 'Isotope m/z' },
    { field: 'sample_peak_mz', label: 'Observed m/z' },
    {
      field: 'match_mz_error',
      label: 'Pre-calibration error [ppm]',
      subheading: null
    },
    { field: 'calibration_mz', label: 'Calibrated m/z' },
    {
      field: 'calibration_mz_error',
      label: 'Post-calibration error [ppm]',
      subheading: null
    },
    { field: 'mz_error_diff', label: 'Error Δ [ppm]', subheading: null },
    {
      field: 'calibrant_to_tic',
      label: 'Fraction of TIC',
      subheading: null
    }
  ]
}))

const columnFormatters = computed(() => ({
  mz: num.mz,
  sample_peak_mz: num.mz,
  calibration_mz: num.mz,
  match_mz_error: num.mzError,
  calibration_mz_error: num.mzError,
  mz_error_diff: num.mzError,
  calibrant_to_tic: num.ticFraction
}))

const formatter = new Intl.NumberFormat('en-US', {
  minimumIntegerDigits: 1,
  minimumFractionDigits: 1,
  maximumFractionDigits: 4
})
</script>

<template>
  <Dialog
    :header="title"
    v-model:visible="visible"
    :style="{ width: samples ? '1200px' : '800px' }"
  >
    <div class="dialog-content">
      <section class="settings-section">
        <h3>Calibration Settings</h3>
        <PaneSettingsCalibration v-model:mzCalibrationParams="mzFit.mzCalibrationParams" />
      </section>
      <section class="results-section">
        <div class="message-container">
          <Message
            v-if="mzFit.status === 'warning'"
            severity="warn"
            style="inline-size: 600px; overflow-wrap: anywhere; margin-bottom: 0rem"
            :closable="true"
          >
            {{ mzFit.error }}
          </Message>
        </div>
        <h3>Calibration Results</h3>
        <div class="content-wrapper" :class="{ 'batch-layout': !!samples }">
          <div v-if="samples" class="list-wrapper">
            <Listbox
              v-model:modelValue="previewSample"
              :options="samples ?? []"
              optionLabel="sample_item_name"
              dataKey="sample_item_id"
              style="height: 100%; width: 100%; border: none"
              :listStyle="{ height: '100%' }"
            />
          </div>
          <div class="table-wrapper">
            <DataTable
              v-if="calibration.rows.length > 0"
              :key="calibration.key"
              :value="
                calibration.rows.map((row, index) => ({
                  ...row,
                  type: index == calibration.rows.length - 1 ? 'summary' : 'stat'
                }))
              "
              sortField="mz"
              class="calibration-table"
              scrollable
              scrollHeight="flex"
            >
              <Column
                v-for="col of calibration.columns"
                :key="col.field"
                :field="col.field"
                :header="col.label"
              >
                <template #body="{ data }">
                  <span
                    :class="data.type == 'summary' ? 'summary-cell' : ''"
                    v-if="data[col.field] !== null && data[col.field] !== undefined"
                    >{{
                      columnFormatters[col.field]
                        ? columnFormatters[col.field].format(data[col.field])
                        : formatter.format(data[col.field])
                    }}</span
                  >
                  <span v-else>-</span>
                </template>
              </Column>
            </DataTable>
            <div
              v-else
              class="center"
              style="
                height: 100%;
                width: 100%;
                overflow: hidden;
                display: flex;
                align-items: center;
                justify-content: center;
              "
            >
              <ProgressSpinner v-if="!(mzFit.status == 'error')" />
              <Message v-else severity="error" style="inline-size: 400px; overflow-wrap: anywhere">
                {{ mzFit.error ?? 'Calibration failed due to an unknown error.' }}
              </Message>
            </div>
          </div>
        </div>
      </section>
    </div>
    <menu class="dialog-actions">
      <Message v-if="!canCalibrate" severity="warn" style="margin-inline-end: auto">
        Applying a calibration rewrites the raw file, so it needs the admin role in the instrument's
        workspace. You can review the fit here but not save it.
      </Message>
      <div v-else-if="skippable" class="skip-actions">
        <span v-if="skipped" class="skip-summary">{{ skippedSummary }}</span>
        <Button
          v-if="skipped"
          label="Clear skip"
          severity="secondary"
          outlined
          :disabled="skipState.busy"
          @click="clearSkip"
        />
        <Button
          v-else
          label="Skip calibration..."
          severity="secondary"
          outlined
          :disabled="skipState.busy"
          @click="skipState.open = true"
        />
      </div>
      <Button label="Cancel" @click="() => (visible = false)" severity="secondary" />
      <Button
        label="Save"
        :disabled="!canCalibrate || !mzFit.current || !mzFit.stats || mzFit.status === 'error'"
        @click="save"
      />
    </menu>
  </Dialog>

  <Dialog
    header="Skip m/z calibration"
    v-model:visible="skipState.open"
    modal
    :style="{ width: '480px' }"
  >
    <p class="skip-explanation">
      This records that "{{ original?.filename }}" is deliberately left uncalibrated. It applies to
      every sample referencing that file, in every workspace, and does not stop matching - it
      replaces an ambiguous blank badge with a reason someone answers for. Calibrating the file
      later, or clearing the marker, undoes it.
    </p>
    <label class="skip-label" for="calibration-skip-reason">Reason</label>
    <InputText
      id="calibration-skip-reason"
      v-model="skipState.reason"
      :maxlength="SKIP_REASON_MAX_LENGTH"
      placeholder="e.g. blank file, no calibrant collection for this mode"
      fluid
      autofocus
      @keyup.enter="confirmSkip"
    />
    <template #footer>
      <Button label="Cancel" severity="secondary" @click="skipState.open = false" />
      <Button
        label="Mark skipped"
        :disabled="!skipState.reason.trim() || skipState.busy"
        @click="confirmSkip"
      />
    </template>
  </Dialog>
</template>

<style scoped>
.dialog-content {
  display: flex;
  flex-direction: column;
  gap: 0rem;
  height: 70vh;
  overflow: hidden;
}

.settings-section {
  border-radius: 8px;
  padding: 0rem 1rem;
  flex-shrink: 0;
}

.results-section {
  border-radius: 8px;
  padding: 1rem;
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.settings-section h3,
.results-section h3 {
  margin-top: 0;
  margin-bottom: 0.3rem;
  font-weight: 600;
  color: var(--text-color, #fff);
}

.content-wrapper {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.content-wrapper.batch-layout {
  flex-direction: row;
  gap: 1rem;
}

.list-wrapper {
  width: 280px;
  flex-shrink: 0;
  border: 1px solid var(--surface-border, #3c434d);
  border-radius: 6px;
  overflow: hidden;
}

.table-wrapper {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.calibration-table {
  flex: 1;
}

.calibration-table :deep(.summary-cell) {
  font-weight: bold;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid var(--surface-border, #3c434d);
  flex-shrink: 0;
}

.message-container {
  display: flex;
  justify-content: center;
  align-items: center;
  margin-bottom: 0.3rem;
}

.skip-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-inline-end: auto;
}

.skip-summary {
  color: var(--p-text-muted-color);
  max-inline-size: 48ch;
  overflow-wrap: anywhere;
}

.skip-explanation {
  margin-top: 0;
  color: var(--p-text-muted-color);
}

.skip-label {
  display: block;
  margin-bottom: 0.35rem;
  font-weight: 600;
}
</style>
