<script setup>
import { computed } from 'vue'

import Button from 'primevue/button'

import { num } from '@/lib/formatters'
import { listingName, listingOf } from '@/lib/referenceListings'
import { measuredTierLabel } from '../candidateMeasure.js'
import CardSection from './CardSection.vue'
import { useCard } from './useCard.js'

// The close alternatives: each rival with its fit, its mass error and its
// kind, and *Use*, which commits it - visible on every row it can act on,
// where it used to show only on hover. Folded by default (section 4.1 of
// docs/dev/assignment_ledger_and_card.md).
const {
  app,
  alternatives,
  alternativeKind,
  altTooltip,
  altFit,
  altMzError,
  altPlausibility,
  formatFit,
  scoring,
  measuringAlt,
  canPromote,
  promoteBlocked,
  noAdductHint,
  promoteAlternative,
  curating,
  curateDenied,
  canEdit,
  canChange,
  readOnly,
  readOnlyReason,
  derivedRun,
  measuredCurrent
} = useCard()

const summary = computed(() => {
  const list = alternatives.value
  const best = list[0]
  const name = best?.assigned_formula || best?.ion_formula
  const fit = best ? altFit(best) : null
  return [
    `${list.length} rival${list.length === 1 ? '' : 's'}`,
    name ? `${name}${fit != null ? ` fit ${formatFit(fit)}` : ''}` : null
  ]
    .filter(Boolean)
    .join(' · ')
})
</script>

<template>
  <CardSection
    v-if="alternatives.length"
    title="Alternatives"
    :summary="summary"
    testid="section-alternatives"
  >
    <div
      class="alts"
      v-help.right="{
        title: 'Close Alternatives',
        helpKey: 'assignment-curation',
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/#assigning-a-peak-yourself')
      }"
    >
      <div class="alts-list">
        <div
          v-for="(alt, i) in alternatives"
          :key="i"
          class="alt"
          v-tooltip.left="altTooltip(alt, i)"
        >
          <span class="f"
            >{{ alt.assigned_formula || alt.ion_formula || '?'
            }}<span v-if="alternativeKind(alt)" class="alt-kind">{{
              alternativeKind(alt).label
            }}</span
            ><span v-if="listingOf(alt)" class="alt-listed">{{
              listingName(listingOf(alt))
            }}</span></span
          >
          <span class="s">
            <span v-if="altFit(alt) != null"
              >fit {{ formatFit(altFit(alt))
              }}<span v-if="altMzError(alt) != null">
                &middot; {{ num.mzError.format(altMzError(alt)) }} ppm</span
              ><span v-if="alt.measured?.takes_member" class="alt-measured">
                &middot; {{ measuredTierLabel(alt.measured) }}</span
              ></span
            >
            <span
              v-else-if="(scoring && !alt.scored) || (measuringAlt(alt) && !alt.measured)"
              class="scoring"
              >measuring&hellip;</span
            >
            <span v-else-if="alt.measured && !alt.measured.takes_member" class="alt-unreached"
              >not reached here</span
            >
            <span v-else-if="altPlausibility(alt) != null"
              >plaus {{ formatFit(altPlausibility(alt)) }}</span
            >
            <span v-else class="no-stats"><span class="pi ph ph-info" /></span>
          </span>
          <Button
            v-if="(canPromote(alt) || promoteBlocked(alt)) && !curateDenied && canChange"
            :class="['alt-use', { busy: curating === i, blocked: promoteBlocked(alt) }]"
            label="Use"
            size="small"
            text
            severity="secondary"
            icon="pi ph ph-push-pin"
            :disabled="curating !== null || promoteBlocked(alt)"
            :loading="curating === i"
            v-tooltip.top="
              promoteBlocked(alt)
                ? noAdductHint(alt, i)
                : derivedRun
                  ? 'Pin this identity on the batch peak'
                  : 'Assign this formula to the peak by hand'
            "
            @click="promoteAlternative(alt, i)"
          />
        </div>
      </div>
      <div v-if="derivedRun && !curateDenied && canChange" class="note">
        <span class="pi ph ph-info" /> Derived from the batch ledger: <b>Use</b> pins the identity
        on the batch peak for the whole batch and measures it in every sample. Each alternative
        shows what the pin would read in this sample<template v-if="measuredCurrent"
          >, beside what it reads now: {{ measuredCurrent }}</template
        >.
      </div>
      <div v-if="curateDenied || !canEdit" class="note" data-testid="curate-denied">
        <span class="pi ph ph-lock-simple" /> Editor access is required to change an assignment.
      </div>
      <div v-else-if="readOnly" class="note" data-testid="curate-read-only">
        <span class="pi ph ph-lock-simple" /> {{ readOnlyReason }}
      </div>
    </div>
  </CardSection>
</template>

<style scoped>
.alts {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.alts-list {
  display: flex;
  flex-direction: column;
  max-height: 14rem;
  overflow-y: auto;
}
.alt {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 0 0.5rem;
  padding: 0.15rem 0.2rem;
  border-bottom: 1px solid var(--p-content-border-color);
  font-family: var(--font-mono);
  font-size: var(--card-text-sm);
  cursor: default;
}
.alt:hover {
  background: var(--p-content-hover-background);
}
.alt .f {
  min-width: 0;
  overflow-wrap: anywhere;
}
.alt .s {
  margin-left: auto;
  color: var(--p-text-muted-color);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.alt .scoring {
  font-style: italic;
}
.alt-kind {
  margin-left: 0.4rem;
  padding: 0 0.3rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 0.25rem;
  color: var(--p-text-muted-color);
  font-family: var(--font-sans, inherit);
}
.alt-listed {
  margin-left: 0.4rem;
  color: var(--p-text-muted-color);
  font-family: var(--font-sans, inherit);
}
/* The control that is there only to say why it cannot be used: shown, so
   its reason gets read, and plainly disabled. */
.alt-use.blocked {
  opacity: 0.5;
}
.note {
  display: inline-flex;
  align-items: baseline;
  gap: 0.35rem;
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
}
</style>
