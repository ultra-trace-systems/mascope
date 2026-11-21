<script setup>
import { computed } from 'vue'

import { num } from '@/lib/formatters'
import { useCard } from './useCard.js'

// The measurements: four tiles, each a value and a caption in words, where the
// card used to list eight numbers four of which were percentages that read
// alike (docs/dev/assignment_ledger_and_card.md, section 4.1). Fit,
// plausibility and the evidence they make are the Why box's product line.
const {
  app,
  evidenceRow,
  massZ,
  massZFar,
  massZTooltip,
  zFormat,
  provenance,
  ledgerServed,
  LEDGER_CONFIDENCE_TOOLTIP,
  EVIDENCE_TOOLTIPS,
  isotopeMeasure,
  batchMeasure,
  measuring,
  measured,
  formatFit
} = useCard()

const massCaption = computed(() =>
  massZ.value != null
    ? `${zFormat.format(massZ.value)} widths from the run's centre`
    : 'no calibration width recorded'
)
const confidence = computed(() => {
  const value = provenance.value?.confidence
  if (value != null) {
    return {
      value: formatFit(value),
      caption: provenance.value.is_tie
        ? 'a tie: the runner-up is too close to call'
        : 'its share of the evidence among the candidates',
      tie: Boolean(provenance.value.is_tie),
      tooltip: EVIDENCE_TOOLTIPS.confidence
    }
  }
  if (ledgerServed.value) {
    return {
      value: '—',
      caption: 'none: served from the batch ledger, with no contest',
      tie: false,
      tooltip: LEDGER_CONFIDENCE_TOOLTIP
    }
  }
  return { value: '—', caption: 'not recorded by the run', tie: false, tooltip: null }
})
</script>

<template>
  <div
    class="measurements"
    data-testid="measurements"
    v-help.right="{
      title: 'Evidence',
      helpKey: 'assignment-evidence',
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-fit-score-a-pure-measurement')
    }"
  >
    <!-- A derived row's numbers arrive a moment after the row: the family is
         measured against this sample's peaks on request. -->
    <div v-if="measuring" class="measure-note" data-testid="measure-note">
      Measuring the isotope pattern against this sample&hellip;
    </div>
    <div v-else-if="measured?.blocked_reason" class="measure-note" data-testid="measure-note">
      Not measured: {{ measured.blocked_reason }}
    </div>
    <!-- Measured, but the family's main peak paired with none of the
         predicted isotopologues: this says which prediction came nearest. -->
    <div v-else-if="measured?.main_peak_note" class="measure-note" data-testid="measure-note">
      Main peak: {{ measured.main_peak_note }}
    </div>
    <div class="tiles">
      <div class="tile" data-testid="tile-mass">
        <span class="tile-value" v-tooltip.top="EVIDENCE_TOOLTIPS.mzError">{{
          evidenceRow.mz_error_ppm != null
            ? `${num.mzError.format(evidenceRow.mz_error_ppm)} ppm`
            : '—'
        }}</span>
        <span
          class="tile-caption"
          :class="{ far: massZFar }"
          :data-testid="massZ != null ? 'mass-z' : null"
          v-tooltip.top="massZTooltip || null"
          >{{ massCaption }}</span
        >
      </div>
      <div class="tile" data-testid="tile-isotopes">
        <span class="tile-value" v-tooltip.top="EVIDENCE_TOOLTIPS.abundanceError">{{
          isotopeMeasure.value
        }}</span>
        <span class="tile-caption">{{ isotopeMeasure.caption }}</span>
      </div>
      <div class="tile" data-testid="tile-confidence">
        <span class="tile-value" v-tooltip.top="confidence.tooltip">{{ confidence.value }}</span>
        <span class="tile-caption" :class="{ tie: confidence.tie }">{{ confidence.caption }}</span>
      </div>
      <div class="tile" data-testid="tile-batch">
        <span class="tile-value">{{ batchMeasure.value }}</span>
        <span class="tile-caption">{{ batchMeasure.caption }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.measurements {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.measure-note {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  font-style: italic;
}
.tiles {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.4rem;
}
.tile {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  background: var(--p-content-hover-background);
}
.tile-value {
  font-family: var(--font-mono);
  font-size: var(--card-text-base);
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  cursor: help;
}
.tile-caption {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  line-height: 1.3;
  overflow-wrap: anywhere;
}
/* Past the distance the mass gate caps at, and a tie: in the colour the
   candidate chip wears, since either is what can hold the row there. */
.tile-caption.far,
.tile-caption.tie {
  color: var(--state-warning);
}
/* One tile a row in a narrow column. */
@container card (max-width: 340px) {
  .tiles {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
