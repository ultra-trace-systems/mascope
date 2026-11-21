<script setup>
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Popover from 'primevue/popover'
import RadioButton from 'primevue/radiobutton'

import { BaseClaimStructure, BaseVerdictBadge } from '@/lib/base'
import { EVIDENCE_LEVELS, VERDICT_META } from '@/lib/verification'
import { useCard } from './useCard.js'

// The verdict bar, pinned at the card's foot so it is on the first screen
// whatever the sections above hold: Confirm, Reject and Unsure, or the
// recorded verdict with Change; under it, the composition search
// (docs/dev/assignment_ledger_and_card.md, section 4.1). What a verdict
// records, and through which endpoint, is as it was.
const {
  app,
  showSearch,
  toggleSearch,
  verifiable,
  verification,
  anchorVerdict,
  anchorConflict,
  anchorVerdictLabel,
  anchorVerdictTooltip,
  editing,
  startEdit,
  cancelEdit,
  showVerifyForm,
  denied,
  submitting,
  pendingVerdict,
  openVerdict,
  verdictDialog,
  dialogMeta,
  dialogId,
  confirming,
  evidenceLevel,
  autofocusLevel,
  verifyTarget,
  claim,
  claimOptions,
  note,
  submitDialog,
  canConfirm
} = useCard()
</script>

<template>
  <div class="verdict-bar" data-testid="verdict-bar">
    <div
      v-if="verifiable"
      class="verify"
      v-help.right="{
        title: 'Verification',
        helpKey: 'assignment-verification',
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/#verifying-assignments')
      }"
    >
      <!-- Borrowed from the batch level: the verdict's own icon with the
           batch's mark at its upper right, as the ledger draws it. Never
           dashed: a dashed border means not yet known. -->
      <div
        v-if="anchorVerdict && !verification"
        class="anchor-verdict"
        v-tooltip.top="anchorVerdictTooltip"
      >
        <span class="anchor-verdict-face"
          ><span :class="['pi', VERDICT_META[anchorVerdict.verdict].icon]" /><span
            class="pi pi-hashtag batch-mark"
            data-testid="batch-mark"
            aria-hidden="true"
        /></span>
        <span
          >{{ anchorVerdictLabel }} at batch level &mdash; a verdict here records a per-sample
          exception</span
        >
      </div>
      <div v-if="verification && !editing" class="verify-current" data-testid="verdict-recorded">
        <BaseVerdictBadge :record="verification" :conflict="anchorConflict" />
        <Button
          v-if="!denied"
          label="Change"
          size="small"
          text
          severity="secondary"
          icon="pi ph ph-pencil-simple"
          data-testid="verdict-change"
          v-tooltip.top="'Change the verdict'"
          @click="startEdit"
        />
      </div>
      <div v-else-if="showVerifyForm" class="verify-buttons">
        <Button
          label="Confirm"
          icon="pi ph ph-check-circle"
          size="small"
          severity="success"
          class="verdict-button"
          aria-haspopup="dialog"
          :disabled="submitting"
          :loading="submitting && pendingVerdict === 'confirmed'"
          v-tooltip.top="'The assignment is right'"
          @click="openVerdict('confirmed', $event)"
        />
        <Button
          label="Reject"
          icon="pi ph ph-x-circle"
          size="small"
          severity="danger"
          class="verdict-button"
          aria-haspopup="dialog"
          :disabled="submitting"
          :loading="submitting && pendingVerdict === 'rejected'"
          v-tooltip.top="'The assignment is wrong'"
          @click="openVerdict('rejected', $event)"
        />
        <Button
          label="Unsure"
          icon="pi ph ph-question"
          size="small"
          severity="secondary"
          class="verdict-button"
          aria-haspopup="dialog"
          :disabled="submitting"
          :loading="submitting && pendingVerdict === 'unsure'"
          v-tooltip.top="'The evidence at hand cannot tell'"
          @click="openVerdict('unsure', $event)"
        />
        <Button
          v-if="editing"
          label="Cancel"
          size="small"
          text
          severity="secondary"
          v-tooltip.top="'Keep the verdict as it is'"
          @click="cancelEdit"
        />
      </div>
      <div v-else-if="denied" class="verify-denied">
        <span class="pi ph ph-lock-simple" /> Editor access is required to verify.
      </div>
      <!-- No help card in here: the card above covers it, and one inside a
           popover re-registers on every open. Radio buttons rather than a
           Select, which would swallow the Escape that closes the dialog. -->
      <Popover ref="verdictDialog" :aria-label="`${dialogMeta.verb} the assignment`">
        <div class="verdict-dialog" data-testid="verdict-dialog">
          <div class="dialog-claim">
            {{ dialogMeta.verb }}
            <span class="dialog-formula">{{ verifyTarget?.assigned_formula }}</span>
          </div>
          <div
            v-if="confirming"
            class="dialog-levels"
            role="radiogroup"
            :aria-labelledby="`${dialogId}-levels`"
          >
            <span :id="`${dialogId}-levels`" class="eyebrow">Evidence level</span>
            <div v-for="level in EVIDENCE_LEVELS" :key="level.value" class="dialog-level">
              <RadioButton
                v-model="evidenceLevel"
                :value="level.value"
                :name="`${dialogId}-level`"
                :inputId="`${dialogId}-${level.value}`"
                :pt="{ input: { autofocus: level.value === autofocusLevel } }"
              />
              <label :for="`${dialogId}-${level.value}`">{{ level.label }}</label>
            </div>
          </div>
          <BaseClaimStructure
            v-if="confirming && evidenceLevel"
            :key="verifyTarget?.peak_assignment_id"
            v-model="claim"
            :candidates="claimOptions"
            :evidence="evidenceLevel"
            :initial="verification"
          />
          <InputText
            v-model="note"
            placeholder="Note (optional)"
            aria-label="Note"
            size="small"
            fluid
            :autofocus="!confirming"
            @keydown.enter="submitDialog"
          />
          <div class="dialog-actions">
            <Button
              label="Cancel"
              size="small"
              text
              severity="secondary"
              @click="verdictDialog?.hide()"
            />
            <Button
              :label="dialogMeta.label"
              :icon="dialogMeta.icon"
              size="small"
              :severity="dialogMeta.severity"
              data-testid="verdict-submit"
              :disabled="submitting || (confirming && !canConfirm)"
              :loading="submitting"
              @click="submitDialog"
            />
          </div>
        </div>
      </Popover>
    </div>
    <div class="insp-actions">
      <Button
        :label="showSearch ? 'Hide the formula search' : 'Find other formulas for this peak'"
        size="small"
        text
        :severity="showSearch ? 'primary' : 'secondary'"
        icon="pi ph ph-magnifying-glass"
        data-testid="find-other-formulas"
        v-tooltip.top="'Search other compositions for this peak'"
        @click="toggleSearch"
      />
    </div>
  </div>
</template>

<style scoped>
/* Pinned at the foot of the scrolling card, on the card's own ground, so the
   sections scroll under it. */
.verdict-bar {
  position: sticky;
  bottom: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  margin: 0 -1rem -0.9rem;
  padding: 0.5rem 1rem 0.6rem;
  border-top: 1px solid var(--p-content-border-color);
  border-radius: 0 0 8px 8px;
  background: var(--p-content-background);
}
.verify {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.verify-current {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.anchor-verdict {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  align-self: flex-start;
  padding: 0.1rem 0.5rem;
  border: 1px solid color-mix(in srgb, var(--state-info) 45%, transparent);
  border-radius: 100px;
  background: color-mix(in srgb, var(--state-info) 10%, transparent);
  color: var(--state-info);
  font-size: var(--card-text-sm);
  cursor: default;
}
.anchor-verdict-face {
  position: relative;
  display: inline-flex;
  margin-right: 4px;
}
.anchor-verdict-face .batch-mark {
  position: absolute;
  top: -4px;
  right: -6px;
  font-size: 9px;
  line-height: 1;
}
/* Confirm, Reject and Unsure share equal width: equal prominence, since a
   rejection is a first-class label, not an afterthought. */
.verify-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.verify-buttons > .verdict-button {
  flex: 1;
}
.verdict-dialog {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  width: 17rem;
  max-width: 85vw;
}
.dialog-claim {
  font-size: var(--card-text-base);
}
.dialog-formula {
  font-family: var(--font-mono);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.eyebrow {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  font-weight: 500;
}
.dialog-levels {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.dialog-level {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.dialog-level label {
  cursor: pointer;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.4rem;
}
.verify-denied {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
}
.insp-actions {
  display: flex;
  justify-content: flex-start;
}
</style>
