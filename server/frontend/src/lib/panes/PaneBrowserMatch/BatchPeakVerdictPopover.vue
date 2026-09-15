<script setup>
import { ref, computed, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'

import { BaseVerdictBadge } from '@/lib/base'
import { num } from '@/lib/formatters'
import { canEditWorkspace } from '@/lib/permissions'
import { EVIDENCE_LEVELS, VERDICT_META } from '@/lib/verification'
import { useApp } from '@/stores'

/**
 * The batch-level verdict form for one batch peak, shown from the Verdict
 * column's popover: what the row claims, the live verdict on it if any (and
 * whether it is stale), Confirm / Reject / Unsure with the evidence level
 * confirming requires, a note, and Retract.
 *
 * Every write names the formula judged, so a consensus that moved under the
 * user - another sample's fold can move it between the ledger being read and
 * the click - is refused by the server and the row reloaded, rather than a
 * verdict recorded against a formula nobody saw.
 */
const props = defineProps({
  /** The ledger row: batch_peak_id, consensus_formula, mz, n_present, ... */
  row: { type: Object, required: true },
  /** The live verdict shown on the row, or null. */
  record: { type: Object, default: null },
  /** Whether that verdict is about a claim the row no longer makes. */
  stale: { type: Boolean, default: false }
})
const emit = defineEmits(['done'])

const app = useApp()
const verdicts = computed(() => app.data.batchPeakVerification)

// Same answer the compute bar gives, for the same reason: "yes" while the
// account or workspace is still loading, so a slow load offers the form rather
// than hiding a capability the user has. A 403 on the write still lands as
// `denied` below.
const canEdit = computed(() => canEditWorkspace(app.data.workspace.focused, app.auth?.user))
const judgeable = computed(() => Boolean(props.row.consensus_formula))

const evidenceLevel = ref(props.record?.evidence_level ?? null)
const note = ref(props.record?.note ?? '')
const submitting = ref(false)
const pendingVerdict = ref(null) // which button is mid-submit
const denied = ref(false) // 403: not an editor in this workspace
const claimChanged = ref(false) // 409: the consensus moved under the user

// Pointed at another row: start from that row's verdict, not the last one's.
watch(
  () => props.row.batch_peak_id,
  () => {
    evidenceLevel.value = props.record?.evidence_level ?? null
    note.value = props.record?.note ?? ''
    denied.value = false
    claimChanged.value = false
  }
)

const staleText = computed(() => {
  if (!props.stale || !props.record) return ''
  const label = VERDICT_META[props.record.verdict]?.label ?? props.record.verdict
  return `${label} as ${props.record.assigned_formula} - the consensus is now ${
    props.row.consensus_formula ?? 'unassigned'
  }. Re-judge or retract.`
})

async function submit(verdict) {
  if (verdict === 'confirmed' && !evidenceLevel.value) return
  submitting.value = true
  pendingVerdict.value = verdict
  claimChanged.value = false
  try {
    await verdicts.value.verify({
      batch_peak_id: props.row.batch_peak_id,
      verdict,
      evidence_level: evidenceLevel.value || null,
      note: note.value?.trim() || null,
      expected_formula: props.row.consensus_formula
    })
    emit('done')
  } catch (error) {
    const status = error?.response?.status
    if (status === 403) denied.value = true
    else if (status === 409) {
      // The consensus moved since the ledger was read: reload the row rather
      // than record a verdict on a formula the user never saw.
      claimChanged.value = true
      app.data.batchPeak.load('claim changed')
    }
  } finally {
    submitting.value = false
    pendingVerdict.value = null
  }
}

async function retract() {
  submitting.value = true
  pendingVerdict.value = 'retract'
  try {
    await verdicts.value.retract({ batch_peak_id: props.row.batch_peak_id })
    emit('done')
  } catch (error) {
    if (error?.response?.status === 403) denied.value = true
  } finally {
    submitting.value = false
    pendingVerdict.value = null
  }
}
</script>

<template>
  <div class="verdict-popover">
    <div class="claim">
      <span class="formula">{{ row.consensus_formula ?? 'unassigned' }}</span>
      <span class="detail">m/z {{ num.mz.format(row.mz) }}</span>
      <span class="detail">{{ row.n_present }} sample{{ row.n_present === 1 ? '' : 's' }}</span>
    </div>
    <p class="scope">
      One verdict per species at this batch peak. It covers every sample in this batch that has no
      verdict of its own; per-sample verdicts always win.
    </p>

    <div v-if="record" class="current" :class="{ stale }">
      <BaseVerdictBadge :record="record" />
      <span v-if="stale" class="stale-text">{{ staleText }}</span>
    </div>

    <div v-if="!judgeable" class="hint">
      Nothing to judge yet: this batch peak has no consensus formula.
    </div>
    <div v-else-if="denied || !canEdit" class="denied">
      <span class="pi ph ph-lock-simple" /> Recording a verdict needs the editor role in this
      workspace.
    </div>
    <template v-else>
      <div v-if="claimChanged" class="hint changed">
        The consensus changed since this row was read. The ledger has reloaded - judge the formula
        it shows now.
      </div>
      <div class="buttons">
        <Button
          label="Confirm"
          icon="pi ph ph-check-circle"
          size="small"
          severity="success"
          :disabled="submitting || !evidenceLevel"
          :loading="submitting && pendingVerdict === 'confirmed'"
          v-tooltip.top="!evidenceLevel ? 'Pick an evidence level to confirm' : ''"
          @click="submit('confirmed')"
        />
        <Button
          label="Reject"
          icon="pi ph ph-x-circle"
          size="small"
          severity="danger"
          :disabled="submitting"
          :loading="submitting && pendingVerdict === 'rejected'"
          @click="submit('rejected')"
        />
        <Button
          label="Unsure"
          icon="pi ph ph-question"
          size="small"
          severity="secondary"
          :disabled="submitting"
          :loading="submitting && pendingVerdict === 'unsure'"
          @click="submit('unsure')"
        />
      </div>
      <Select
        v-model="evidenceLevel"
        :options="EVIDENCE_LEVELS"
        optionLabel="label"
        optionValue="value"
        placeholder="Evidence level (required to confirm)"
        size="small"
        showClear
        fluid
      />
      <InputText v-model="note" placeholder="Note (optional)" size="small" fluid />
      <div v-if="record" class="retract">
        <Button
          label="Retract"
          icon="pi ph ph-arrow-counter-clockwise"
          size="small"
          text
          severity="secondary"
          :disabled="submitting"
          :loading="submitting && pendingVerdict === 'retract'"
          v-tooltip.top="
            'Withdraw the verdict: the species reads as unverified again in every sample it covered'
          "
          @click="retract"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.verdict-popover {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  width: 22rem;
  max-width: 90vw;
}
.claim {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
}
.formula {
  font-weight: 600;
}
.detail {
  font-size: 0.8rem;
  opacity: 0.75;
}
.scope {
  margin: 0;
  font-size: 0.78rem;
  opacity: 0.8;
}
.current {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
}
/* The judgment is about a formula the consensus has since left: the badge stays
   - a human label is never overwritten by a recompute - outlined in the warning
   colour, with the sentence that says what to do about it. */
.current.stale {
  padding: 0.25rem 0.4rem;
  border: 1px dashed var(--state-warning);
  border-radius: 4px;
}
.stale-text {
  font-size: 0.78rem;
  color: var(--state-warning);
}
.hint {
  font-size: 0.78rem;
  opacity: 0.8;
}
.hint.changed {
  color: var(--state-warning);
  opacity: 1;
}
.denied {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.78rem;
  opacity: 0.7;
}
.buttons {
  display: flex;
  gap: 0.4rem;
}
.buttons > :deep(.p-button) {
  flex: 1;
}
.retract {
  display: flex;
  justify-content: flex-end;
}
</style>
