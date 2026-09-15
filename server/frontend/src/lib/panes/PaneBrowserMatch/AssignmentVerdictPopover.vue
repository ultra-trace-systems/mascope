<script setup>
import { ref, computed, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'

import { BaseVerdictBadge } from '@/lib/base'
import { num } from '@/lib/formatters'
import { canEditWorkspace } from '@/lib/permissions'
import { EVIDENCE_LEVELS } from '@/lib/verification'
import { useApp } from '@/stores'

/**
 * The per-sample verdict form for one ledger row, shown from the Verdict
 * column's popover: the sample ledger's counterpart of the batch ledger's
 * BatchPeakVerdictPopover, and the same form the peak inspector carries, so
 * an unverified row can be judged where it is read.
 *
 * A verdict is about the compound, so it is recorded on the family's M0
 * whichever member the row is - the verification store redirects, as it does
 * for the inspector - and it wins over a batch-level verdict reaching the
 * sample. There is no retract here: a per-sample verdict is superseded by the
 * next one recorded, as in the inspector.
 */
const props = defineProps({
  /** The ledger row: peak_assignment_id, sample_peak_mz, assigned_formula, role ... */
  row: { type: Object, required: true },
  /** The family's own live verdict, or null. */
  record: { type: Object, default: null },
  /** The batch-level verdict reaching the row when it has none of its own. */
  overlay: { type: Object, default: null }
})
const emit = defineEmits(['done'])

const app = useApp()

// Same answer the batch popover gives, for the same reason: "yes" while the
// account or workspace is still loading, so a slow load offers the form rather
// than hiding a capability the user has. A 403 on the write still lands as
// `denied` below.
const canEdit = computed(() => canEditWorkspace(app.data.workspace?.focused, app.auth?.user))

// Judged through the family's M0, as the inspector judges it.
const target = computed(() => app.data.peakAssignment.peak.m0Of(props.row) ?? props.row)
const judgeable = computed(() => Boolean(target.value?.assigned_formula))
const throughM0 = computed(
  () => target.value && target.value.peak_assignment_id !== props.row.peak_assignment_id
)

const evidenceLevel = ref(props.record?.evidence_level ?? null)
const note = ref(props.record?.note ?? '')
const submitting = ref(false)
const pendingVerdict = ref(null) // which button is mid-submit
const denied = ref(false) // 403: not an editor on this sample

// Pointed at another row: start from that row's verdict, not the last one's.
watch(
  () => props.row.peak_assignment_id,
  () => {
    evidenceLevel.value = props.record?.evidence_level ?? null
    note.value = props.record?.note ?? ''
    denied.value = false
  }
)

async function submit(verdict) {
  // Confirm requires an evidence level (also enforced server-side).
  if (verdict === 'confirmed' && !evidenceLevel.value) return
  submitting.value = true
  pendingVerdict.value = verdict
  try {
    await app.data.peakAssignment.verification.verify({
      peak_assignment_id: target.value.peak_assignment_id,
      verdict,
      evidence_level: evidenceLevel.value || null,
      note: note.value?.trim() || null
    })
    emit('done')
  } catch (error) {
    // The http layer already toasts; only 403 changes the UI.
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
      <span class="formula">{{ target?.assigned_formula ?? 'unassigned' }}</span>
      <span class="detail">m/z {{ num.mz.format(row.sample_peak_mz) }}</span>
      <span v-if="throughM0" class="detail">judged on its M0</span>
    </div>
    <p class="scope">
      One verdict per compound in this sample, recorded on the family's M0. It wins over a
      batch-level verdict reaching this sample.
    </p>

    <div v-if="record" class="current">
      <BaseVerdictBadge :record="record" />
    </div>
    <div v-else-if="overlay" class="current inherited">
      <BaseVerdictBadge :record="overlay" inherited />
      <span class="detail">batch-level, until this sample has one of its own</span>
    </div>

    <div v-if="!judgeable" class="hint">Nothing to judge: this peak carries no formula.</div>
    <div v-else-if="denied || !canEdit" class="denied">
      <span class="pi ph ph-lock-simple" /> Recording a verdict needs the editor role in this
      workspace.
    </div>
    <template v-else>
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
.hint {
  font-size: 0.78rem;
  opacity: 0.8;
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
</style>
