<script setup>
import { computed } from 'vue'

import Button from 'primevue/button'

import { BaseLevelTag, BaseTierTag, BaseVerdictBadge } from '@/lib/base'
import { num } from '@/lib/formatters'
import { formatIsotopeLabel } from '@/lib/chem'
import { sourceLabel, sourceLine } from '@/lib/assignmentSources'
import {
  candidatesTooltip,
  listingName,
  listingSource,
  listingTags,
  listingTooltip,
  targetMore,
  targetName,
  targetTooltip
} from '@/lib/referenceListings'
import { TIERING_PROVISIONAL } from '@/lib/tiers'
import { useCard } from './useCard.js'

// The answer: what the peak is, before anything says why. The listed name and
// its list, the formula, the ion line and one row of chips - the tier, the
// level and the verdict (docs/dev/assignment_ledger_and_card.md, section 4.1).
const {
  app,
  focusedAssignment,
  headlineIsIon,
  peakSummary,
  ionization,
  ionizationTooltip,
  evidenceRow,
  isFamilyHead,
  listedAs,
  targetAs,
  engineTierTooltip,
  engineLevel,
  engineLevelTooltip,
  identificationLevel,
  verification,
  verifiable,
  anchorConflict,
  batchOpened,
  nextSample,
  tierChipCap
} = useCard()

// Which sample the card shows when the batch ledger opened the peak: the
// brightest of those that hold it, or the one *Other samples* stepped to.
const batchOpenedLine = computed(() => {
  const opened = batchOpened.value
  if (!opened) return ''
  const place =
    opened.index === 0
      ? `the brightest of ${opened.count} samples`
      : `${opened.index + 1} of ${opened.count} samples, by intensity`
  return `In ${opened.name}: ${place} that hold this batch peak`
})

// The ion line: how the ion was made and what it is, where its peak sits, and
// which line of the ion the peak is - "[M+HCOO]- → C3H3O4- · m/z 103.0035 · M0" -
// then where the row came from. The mechanism leads it as a chemist writes the
// pair; the ion that heads the card is not repeated.
const ionParts = computed(() => {
  const row = focusedAssignment.value
  if (!row) return []
  const isotope = evidenceRow.value?.isotope_label
  const line = isotope ? formatIsotopeLabel(isotope) : isFamilyHead(row) ? 'M0' : null
  return [
    row.ion_formula && !headlineIsIon.value
      ? { key: 'ion', text: row.ion_formula, tooltip: 'Ion formula' }
      : null,
    row.sample_peak_mz != null
      ? { key: 'mz', text: `m/z ${num.mz.format(row.sample_peak_mz)}`, tooltip: 'Measured m/z' }
      : null,
    line
      ? {
          key: 'isotope',
          text: line,
          tooltip: 'The isotope line of the ion this peak is; M0 is the monoisotopic line'
        }
      : null,
    row.source
      ? { key: 'source', text: sourceLabel(row.source), tooltip: sourceLine(row.source), src: true }
      : null
  ].filter(Boolean)
})

const provisional = TIERING_PROVISIONAL
</script>

<template>
  <div class="answer" data-testid="answer">
    <!-- A list's name for the formula is the first thing a reader checks the
         assignment against, so it is named outright, over the formula. -->
    <div v-if="listedAs || targetAs" class="identity" data-testid="identity">
      <!-- What the target stands for, labelled as the target's: where a list
           names the same structure it is one candidate with both labels. -->
      <div
        v-if="targetAs"
        class="ev listed"
        data-testid="target-as"
        v-tooltip.top="
          targetAs.identity ? candidatesTooltip(targetAs, listedAs) : targetTooltip(targetAs)
        "
      >
        <span class="v"
          ><span class="pi ph ph-target" /> {{ targetName(targetAs)
          }}<template v-if="targetMore(targetAs, listedAs)">
            +{{ targetMore(targetAs, listedAs) }}</template
          ></span
        >
        <span class="k"
          >target<template v-if="targetAs.identity">
            &middot; reference list<span v-if="!listedAs.matched" class="potential">
              &middot; potential</span
            ></template
          ></span
        >
        <span v-if="targetAs.identity?.source" class="list-source">{{
          targetAs.identity.source
        }}</span>
        <span v-if="targetAs.unlisted" class="list-tags">
          <span class="list-tag" data-testid="target-unlisted">unlisted</span>
        </span>
        <span v-if="targetAs.identity && listingTags(listedAs).length" class="list-tags">
          <span
            v-for="tag in listingTags(listedAs)"
            :key="tag"
            class="list-tag"
            :data-testid="`list-tag-${tag}`"
            >{{ tag }}</span
          >
        </span>
      </div>
      <div
        v-if="listedAs && !targetAs?.identity"
        class="ev listed"
        data-testid="listed-as"
        v-tooltip.top="listingTooltip(listedAs)"
      >
        <span class="v">{{ listingName(listedAs) }}</span>
        <span class="k"
          >reference list<span v-if="!listedAs.matched" class="potential">
            &middot; potential</span
          ></span
        >
        <span v-if="listingSource(listedAs)" class="list-source">{{
          listingSource(listedAs)
        }}</span>
        <!-- How the list reads its compounds, background for the siloxanes:
             said beside the name, explained in the tooltip, never weighed. -->
        <span v-if="listingTags(listedAs).length" class="list-tags">
          <span
            v-for="tag in listingTags(listedAs)"
            :key="tag"
            class="list-tag"
            :data-testid="`list-tag-${tag}`"
            >{{ tag }}</span
          >
        </span>
      </div>
    </div>
    <div class="insp-title">
      <span
        class="insp-formula"
        v-tooltip.top="
          focusedAssignment.assigned_formula
            ? 'Neutral formula'
            : headlineIsIon
              ? 'Ion formula: the peak names an ion, and no compound of the sample'
              : null
        "
        >{{
          focusedAssignment.assigned_formula || focusedAssignment.ion_formula || 'Unassigned'
        }}</span
      >
    </div>
    <!-- With no formula the headline is the word "Unassigned", or the ion a
         peak the source made is, so the peak itself has to name the card. -->
    <div v-if="!focusedAssignment.assigned_formula" class="insp-sub" data-testid="peak-summary">
      {{ peakSummary }}
    </div>
    <div v-if="ionization || ionParts.length" class="insp-sub ion-line" data-testid="ion-line">
      <span
        v-if="ionization"
        class="ion-mechanism"
        data-testid="ionization"
        v-tooltip.top="ionizationTooltip"
        >{{ ionization }}</span><template v-for="(part, index) in ionParts" :key="part.key"
        ><span v-if="index === 0 && ionization && part.key === 'ion'" class="sep"> → </span
        ><span v-else-if="index || ionization" class="sep"> &middot; </span
        ><span :class="{ src: part.src }" :data-part="part.key" v-tooltip.top="part.tooltip">{{
          part.text
        }}</span></template
      >
    </div>
    <!-- One row of chips: this server's tier, the producing engine's beside
         it where the run was imported, the identification level and the
         engine's level beside it, and the verdict. It wraps in a narrow column. -->
    <div class="insp-tiers chips" data-testid="chips">
      <BaseTierTag
        :tier="focusedAssignment.tier"
        :evidence="focusedAssignment.evidence"
        :role="focusedAssignment.role"
        :source="focusedAssignment.source"
        :cap="tierChipCap"
        v-help.right="{
          title: 'Confidence Tiers',
          helpKey: 'assignment-tiers',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
        }"
      />
      <!-- The producing engine's own verdict, spelled out: the disagreement is
           the reason to open an imported row at all. It is given no evidence -
           the evidence is this server's product and did not produce it. -->
      <BaseTierTag
        v-if="focusedAssignment.engine_tier"
        :tier="focusedAssignment.engine_tier"
        :tooltip="engineTierTooltip"
      />
      <BaseLevelTag
        v-if="identificationLevel"
        :level="identificationLevel.level"
        :role="focusedAssignment.role"
        :tooltip="identificationLevel.tooltip"
      />
      <!-- The producing engine's own level, beside the run's as its tier is
           beside the tier. An imported row has no level of this server's, so
           this is the only level chip it shows. -->
      <BaseLevelTag
        v-if="engineLevel"
        class="engine-level"
        :level="engineLevel"
        :tooltip="engineLevelTooltip"
      />
      <BaseVerdictBadge
        v-if="verification && verifiable"
        :record="verification"
        :conflict="anchorConflict"
        compact
      />
    </div>
    <!-- Opened from the batch ledger: the sample this is, and the way to the
         next one that holds the batch peak. -->
    <div v-if="batchOpened" class="batch-opened" data-testid="batch-opened">
      <span class="pi ph ph-stack" aria-hidden="true" />
      <span class="batch-opened-text">{{ batchOpenedLine }}</span>
      <Button
        v-if="batchOpened.next"
        label="Other samples"
        size="small"
        text
        icon="pi ph ph-arrow-right"
        icon-pos="right"
        class="batch-step"
        data-testid="batch-other-samples"
        v-tooltip.top="`Open the next sample that holds this batch peak: ${batchOpened.next}`"
        @click="nextSample"
      />
    </div>
    <!-- The tiering is still being built: said as a caption under the chips,
         not as a chip of its own. -->
    <div
      v-if="provisional"
      class="caption provisional"
      data-testid="tiering-provisional"
      v-tooltip.top="provisional.tooltip"
    >
      tier rules {{ provisional.label }}
    </div>
  </div>
</template>

<style scoped>
.answer {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.identity {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.listed {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0 0.4rem;
  cursor: default;
}
/* The listed name, then its list: what a list calls the formula, and whose
   word it is. */
.listed .v {
  flex-basis: 100%;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.listed .k,
.listed .list-source {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  overflow-wrap: anywhere;
}
.list-tags {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
.list-tag {
  padding: 0 0.3rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 0.25rem;
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
}
.insp-title {
  min-width: 0;
}
/* The formula, large: the one thing the card is about. */
.insp-formula {
  font-family: var(--font-mono);
  font-size: var(--card-text-lg);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.insp-sub {
  color: var(--p-text-muted-color);
  font-family: var(--font-mono);
  font-size: var(--card-text-sm);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.ion-mechanism {
  color: var(--p-text-color);
  cursor: default;
}
.ion-line .src {
  font-family: var(--font-sans, inherit);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem;
}
/* One run of text: the icon, the sentence and its action wrap as words do. */
.batch-opened {
  font-size: var(--card-text-sm);
  overflow-wrap: anywhere;
}
.batch-opened > .pi {
  margin-right: 0.35rem;
}
.batch-step {
  display: inline-flex;
  padding: 0 0.25rem;
  font-size: inherit;
  vertical-align: baseline;
}
.caption {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  cursor: help;
}
</style>
