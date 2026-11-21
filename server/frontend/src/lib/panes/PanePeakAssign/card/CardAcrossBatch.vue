<script setup>
import { computed } from 'vue'

import { BaseEvidenceBadge } from '@/lib/base'
import { checksSummary, checksTooltip } from '@/lib/batchChecks'
import { coVariationSummary, coVariationTooltip } from '@/lib/batchCoVariation'
import { flatSeriesKind, flatSeriesSummary, flatSeriesTooltip } from '@/lib/batchFlatSeries'
import {
  isotopologuesTooltip,
  sampleIsotopologuesSummary,
  sampleStampSummary,
  sampleStampTooltip
} from '@/lib/batchIsotopologues'
import { persistenceSummary, persistenceTooltip } from '@/lib/batchPersistence'
import CardSection from './CardSection.vue'
import { useCard } from './useCard.js'

// What the batch read of the batch peak this row's peak sits in: every
// reading as the evidence badge the ledgers draw, with its line in words.
// Shown, never weighed. Folded by default, its badges on the folded line
// (docs/dev/assignment_ledger_and_card.md, section 4.1).
const {
  batchStamp,
  batchStampParent,
  focusStampParent,
  batchIsotopologues,
  batchCoVariation,
  batchCoVariationR,
  batchChecks,
  batchPersistence,
  batchPersistenceHold,
  batchPersistenceNote,
  batchFlatSeries,
  extraReadings,
  batchBadges,
  batchReadingCount
} = useCard()

const summary = computed(
  () => `${batchReadingCount.value} reading${batchReadingCount.value === 1 ? '' : 's'}`
)
</script>

<template>
  <CardSection
    v-if="batchReadingCount"
    title="Across the batch"
    :summary="summary"
    testid="section-batch"
  >
    <template #summary>
      <span class="badges" data-testid="batch-badges">
        <BaseEvidenceBadge
          v-for="(badge, i) in batchBadges"
          :key="`${badge.id}-${i}`"
          :mark="badge.id"
          :record="badge.record"
          quiet
        />
      </span>
      {{ summary }}
    </template>
    <!-- The batch's stamp on this peak: another batch peak's isotope line,
         whatever the row reads it as. A link to that peak where this sample
         holds it. -->
    <button
      v-if="batchStamp && batchStampParent"
      type="button"
      class="line opens batch-stamp"
      :class="batchStamp.stamp_source"
      data-testid="batch-stamp"
      v-tooltip.top="sampleStampTooltip(batchStamp, { linked: true })"
      @click="focusStampParent"
    >
      <BaseEvidenceBadge mark="isotopes" :record="{ stampedAs: batchStamp }" quiet />
      <span>{{ sampleStampSummary(batchStamp) }}</span>
    </button>
    <div
      v-else-if="batchStamp"
      class="line batch-stamp"
      :class="batchStamp.stamp_source"
      data-testid="batch-stamp"
      v-tooltip.top="sampleStampTooltip(batchStamp)"
    >
      <BaseEvidenceBadge mark="isotopes" :record="{ stampedAs: batchStamp }" quiet />
      <span>{{ sampleStampSummary(batchStamp) }}</span>
    </div>
    <div
      v-if="batchIsotopologues"
      class="line batch-isotopologues"
      :class="{ stamped: batchIsotopologues.n_stamped > 0 }"
      data-testid="batch-isotopologues"
      v-tooltip.top="isotopologuesTooltip(batchIsotopologues)"
    >
      <BaseEvidenceBadge mark="isotopes" :record="{ stamping: batchIsotopologues }" quiet />
      <span>{{ sampleIsotopologuesSummary(batchIsotopologues) }}</span>
    </div>
    <div
      v-if="batchCoVariation"
      class="line co-variation"
      :class="batchCoVariation.verdict"
      data-testid="co-variation"
      v-tooltip.top="coVariationTooltip(batchCoVariation)"
    >
      <BaseEvidenceBadge mark="coVariation" :record="batchCoVariation" quiet />
      <span
        >{{ coVariationSummary(batchCoVariation)
        }}<span v-if="batchCoVariationR" class="co-variation-r"
          >, {{ batchCoVariationR }}</span
        ></span
      >
    </div>
    <div
      v-if="checksSummary(batchChecks)"
      class="line batch-checks"
      data-testid="batch-checks"
      v-tooltip.top="checksTooltip(batchChecks)"
    >
      <BaseEvidenceBadge mark="facts" :record="batchChecks" quiet />
      <span>{{ checksSummary(batchChecks) }}</span>
    </div>
    <div
      v-if="batchPersistence"
      class="line batch-persistence"
      :class="batchPersistenceHold"
      data-testid="batch-persistence"
      v-tooltip.top="persistenceTooltip(batchPersistence)"
    >
      <BaseEvidenceBadge mark="persistence" :record="batchPersistence" quiet />
      <span
        >{{ persistenceSummary(batchPersistence)
        }}<span v-if="batchPersistenceNote">, {{ batchPersistenceNote }}</span></span
      >
    </div>
    <div
      v-if="batchFlatSeries"
      class="line batch-flat-series"
      :class="flatSeriesKind(batchFlatSeries)"
      data-testid="batch-flat-series"
      v-tooltip.top="flatSeriesTooltip(batchFlatSeries)"
    >
      <BaseEvidenceBadge mark="flatSeries" :record="batchFlatSeries" quiet />
      <span>{{ flatSeriesSummary(batchFlatSeries) }}</span>
    </div>
    <!-- The series, the ratio stability, the reagent decisions and the blend
         flag, as the batch ledger draws them. -->
    <div
      v-for="reading in extraReadings"
      :key="reading.id"
      class="line"
      :data-testid="`batch-${reading.id}`"
      v-tooltip.top="reading.tooltip"
    >
      <BaseEvidenceBadge :mark="reading.id" :record="reading.record" quiet />
      <span>{{ reading.text }}</span>
    </div>
  </CardSection>
</template>

<style scoped>
.badges {
  display: inline-flex;
  gap: 0.2rem;
  margin-right: 0.3rem;
  vertical-align: middle;
}
.line {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: var(--card-text-sm);
  text-align: left;
  cursor: default;
}
.line.opens {
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  font-size: var(--card-text-sm);
  cursor: pointer;
}
.line.opens:hover,
.line.opens:focus-visible {
  text-decoration: underline;
}
/* A reading that moves nothing is muted rather than coloured. */
.co-variation.not_co_varying,
.co-variation.untestable,
.batch-isotopologues:not(.stamped) {
  color: var(--p-text-muted-color);
}
.co-variation-r {
  font-family: var(--font-mono);
  white-space: nowrap;
}
</style>
