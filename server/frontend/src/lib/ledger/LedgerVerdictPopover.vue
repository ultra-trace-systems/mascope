<script setup>
import { ref, computed, watch } from 'vue'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Select from 'primevue/select'

import {
  BaseClaimEvidence,
  BaseClaimStructure,
  BaseClaimWindow,
  BaseVerdictBadge
} from '@/lib/base'
import { num } from '@/lib/formatters'
import { canEditWorkspace } from '@/lib/permissions'
import { ledgerListing, targetOf } from '@/lib/referenceListings'
import {
  EVIDENCE_LEVELS,
  VERDICT_META,
  acquisitionRange,
  claimCandidates,
  claimStatement
} from '@/lib/verification'
import { useApp } from '@/stores'

/**
 * The verdict form for one ledger row, shown from the Verdict column's
 * popover, in either scope: what the row claims, the verdict on it if any,
 * Confirm / Reject / Unsure with the evidence level confirming requires, and
 * a note. Each scope keeps its own words and its own endpoint.
 *
 * - **sample**: the per-sample verdict the peak inspector records. A verdict is
 *   about the compound, so it is recorded on the family's M0 whichever member
 *   the row is - the verification store redirects, as it does for the
 *   inspector - and it wins over a batch-level verdict reaching the sample.
 *   There is no retract: a per-sample verdict is superseded by the next one.
 * - **batch**: the batch-level verdict on a batch peak's species. Every write
 *   names the formula judged, so a consensus that moved under the user is
 *   refused by the server (409) and the ledger reloaded, rather than a verdict
 *   recorded against a formula nobody saw. A stale verdict - about a formula
 *   the consensus has since left - says so, and Retract withdraws a verdict.
 *
 * A confirmation may name the structure it identifies, from the compound the
 * row's reference listing names or an InChIKey of the user's own; one that
 * states level 1, 2 or a 3 naming a candidate must, and a class claim (3b)
 * names its class (BaseClaimStructure). What the evidence showed decides the
 * level (BaseClaimEvidence). A level-1 claim on a batch peak names the time
 * window its standard covers (BaseClaimWindow). A rejection on a reference
 * standard refutes the live confirmation, which the server records; not a
 * stale one, which the Reject does not reach.
 */
const props = defineProps({
  /** Which ledger the row is from. */
  scope: {
    type: String,
    required: true,
    validator: (scope) => scope === 'sample' || scope === 'batch'
  },
  /** The ledger row: an assignment row, or a batch peak. */
  row: { type: Object, required: true },
  /** The row's own live verdict, or null. */
  record: { type: Object, default: null },
  /** Sample scope: the batch-level verdict reaching the row when it has none of its own. */
  overlay: { type: Object, default: null },
  /** Batch scope: whether the verdict is about a claim the row no longer makes. */
  stale: { type: Boolean, default: false }
})
const emit = defineEmits(['done'])

const app = useApp()
const batch = computed(() => props.scope === 'batch')

// "Yes" while the account or workspace is still loading, so a slow load offers
// the form rather than hiding a capability the user has. A 403 on the write
// still lands as `denied` below.
const canEdit = computed(() => canEditWorkspace(app.data.workspace?.focused, app.auth?.user))

// The row a verdict is recorded on: a batch peak itself, or a sample row's
// family M0, as the inspector judges it.
const target = computed(() =>
  batch.value ? props.row : (app.data.peakAssignment.peak.m0Of(props.row) ?? props.row)
)
const rowId = computed(() => (batch.value ? props.row.batch_peak_id : props.row.peak_assignment_id))
const formula = computed(() =>
  batch.value ? props.row.consensus_formula : target.value?.assigned_formula
)
const judgeable = computed(() => Boolean(formula.value))
const throughM0 = computed(
  () => target.value && target.value.peak_assignment_id !== props.row.peak_assignment_id
)

const evidenceLevel = ref(props.record?.evidence_level ?? null)
const note = ref(props.record?.note ?? '')
// The structures the row's candidates name; the claim the form picks of them.
const candidates = computed(() =>
  claimCandidates({
    target: targetOf(target.value?.reference_listing),
    identities: ledgerListing(target.value?.reference_listing)?.identities
  })
)
const claim = ref(null)
// What the evidence showed, and the statement it makes.
const shown = ref(null)
const statement = computed(() =>
  claimStatement(evidenceLevel.value, shown.value?.evidence_detail, shown.value?.independent_line)
)
// A level-1 claim on a batch peak is scoped to the window its standard covers.
const scoped = computed(() => batch.value && statement.value?.level === 1)
const claimWindow = ref(null)
const range = computed(() => acquisitionRange(app.data.sample?.list))
// The structure picker waits for the statement it asks about; a visual look
// states none and still may name one.
const asksStructure = computed(
  () =>
    Boolean(evidenceLevel.value) && (evidenceLevel.value === 'visual' || Boolean(statement.value))
)
const confirmProblem = computed(() => {
  if (!evidenceLevel.value) return 'Pick an evidence level to confirm'
  if (shown.value?.ready === false) return shown.value.problem || 'Say what the evidence showed'
  if (asksStructure.value && claim.value?.ready === false)
    return claim.value.problem || 'Name the structure this evidence identifies'
  if (scoped.value && claimWindow.value?.ready === false)
    return claimWindow.value.problem || 'Give the window the standard covers'
  return ''
})
const canConfirm = computed(() => !confirmProblem.value)
// A standard that disagrees refutes the live confirmation. A stale one is about
// a formula the consensus has left: a Reject names the formula the row claims
// now, so the server refutes nothing and the stale verdict stays as it is.
const refutes = computed(
  () =>
    evidenceLevel.value === 'reference_standard' &&
    props.record?.verdict === 'confirmed' &&
    !props.stale
)
const submitting = ref(false)
const pendingVerdict = ref(null) // which button is mid-submit
const denied = ref(false) // 403: not an editor in this workspace
const claimChanged = ref(false) // batch, 409: the consensus moved under the user

// Pointed at another row: start from that row's verdict, not the last one's.
watch(rowId, () => {
  evidenceLevel.value = props.record?.evidence_level ?? null
  note.value = props.record?.note ?? ''
  denied.value = false
  claimChanged.value = false
})

const staleText = computed(() => {
  if (!props.stale || !props.record) return ''
  const label = VERDICT_META[props.record.verdict]?.label ?? props.record.verdict
  return `${label} as ${props.record.assigned_formula} - the consensus is now ${
    props.row.consensus_formula ?? 'unassigned'
  }. Re-judge or retract.`
})

const SCOPE_TEXT = {
  sample:
    "One verdict per compound in this sample, recorded on the family's M0. It wins over a " +
    'batch-level verdict reaching this sample.',
  batch:
    'One verdict per species at this batch peak. It covers every sample in this batch that has ' +
    'no verdict of its own; per-sample verdicts always win.'
}
const NOTHING_TO_JUDGE = {
  sample: 'Nothing to judge: this peak carries no formula.',
  batch: 'Nothing to judge yet: this batch peak has no consensus formula.'
}

function payload(verdict) {
  const confirm = verdict === 'confirmed'
  const named = confirm && asksStructure.value ? claim.value : null
  const common = {
    verdict,
    evidence_level: evidenceLevel.value || null,
    note: note.value?.trim() || null,
    ...(named?.claimed_inchikey ? { claimed_inchikey: named.claimed_inchikey } : {}),
    ...(named?.claimed_name ? { claimed_name: named.claimed_name } : {}),
    ...(confirm && shown.value?.evidence_detail
      ? { evidence_detail: shown.value.evidence_detail }
      : {}),
    ...(confirm && shown.value?.independent_line
      ? { independent_line: shown.value.independent_line }
      : {}),
    ...(confirm && scoped.value
      ? {
          window_start_utc: claimWindow.value?.window_start_utc,
          window_end_utc: claimWindow.value?.window_end_utc
        }
      : {})
  }
  return batch.value
    ? {
        batch_peak_id: props.row.batch_peak_id,
        ...common,
        expected_formula: props.row.consensus_formula
      }
    : { peak_assignment_id: target.value.peak_assignment_id, ...common }
}

async function submit(verdict) {
  // Confirm requires an evidence level, and a structure on structure evidence
  // (both also enforced server-side).
  if (verdict === 'confirmed' && !canConfirm.value) return
  submitting.value = true
  pendingVerdict.value = verdict
  claimChanged.value = false
  try {
    const store = batch.value
      ? app.data.batchPeakVerification
      : app.data.peakAssignment.verification
    await store.verify(payload(verdict))
    emit('done')
  } catch (error) {
    // The http layer already toasts; only these change the form.
    const status = error?.response?.status
    if (status === 403) denied.value = true
    else if (status === 409 && batch.value) {
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
    await app.data.batchPeakVerification.retract({ batch_peak_id: props.row.batch_peak_id })
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
      <span class="formula">{{ formula ?? 'unassigned' }}</span>
      <span class="detail">m/z {{ num.mz.format(batch ? row.mz : row.sample_peak_mz) }}</span>
      <span v-if="batch" class="detail"
        >{{ row.n_present }} sample{{ row.n_present === 1 ? '' : 's' }}</span
      >
      <span v-if="throughM0" class="detail">judged on its M0</span>
    </div>
    <p class="scope">{{ SCOPE_TEXT[scope] }}</p>

    <div v-if="record" class="current" :class="{ stale }">
      <BaseVerdictBadge :record="record" />
      <span v-if="stale" class="stale-text">{{ staleText }}</span>
    </div>
    <div v-else-if="overlay && !batch" class="current inherited">
      <BaseVerdictBadge :record="overlay" inherited />
      <span class="detail">batch-level, until this sample has one of its own</span>
    </div>

    <div v-if="!judgeable" class="hint">{{ NOTHING_TO_JUDGE[scope] }}</div>
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
          :disabled="submitting || !canConfirm"
          :loading="submitting && pendingVerdict === 'confirmed'"
          v-tooltip.top="confirmProblem"
          @click="submit('confirmed')"
        />
        <Button
          label="Reject"
          icon="pi ph ph-x-circle"
          size="small"
          severity="danger"
          :disabled="submitting"
          :loading="submitting && pendingVerdict === 'rejected'"
          v-tooltip.top="refutes ? 'A standard that disagrees: refutes the confirmation' : ''"
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
      <BaseClaimEvidence
        v-if="evidenceLevel"
        :key="`${rowId}-${evidenceLevel}`"
        v-model="shown"
        :evidence="evidenceLevel"
        :initial="stale ? null : record"
      />
      <!-- A stale verdict's structure is one of a formula the consensus has
           moved off, so a change starts from none. -->
      <BaseClaimStructure
        v-if="asksStructure"
        :key="rowId"
        v-model="claim"
        :candidates="candidates"
        :evidence="evidenceLevel"
        :detail="shown?.evidence_detail ?? null"
        :line="shown?.independent_line ?? null"
        :initial="stale ? null : record"
      />
      <BaseClaimWindow
        v-if="scoped"
        :key="`${rowId}-window`"
        v-model="claimWindow"
        :initial="stale ? null : record"
        :range="range"
      />
      <p v-if="refutes" class="hint">
        Rejecting on a reference standard refutes the confirmation: the record names the claim it
        took back, and the row reads the engine's level again.
      </p>
      <InputText v-model="note" placeholder="Note (optional)" size="small" fluid />
      <div v-if="batch && record" class="retract">
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
  font-family: var(--font-mono);
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
  border: 1px solid var(--state-warning);
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
