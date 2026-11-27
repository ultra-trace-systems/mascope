<script setup>
import { computed } from 'vue'

import { num } from '@/lib/formatters'
import { formatIsotopeLabel } from '@/lib/chem'
import { noneAskedSummary, notFoundTooltip, notFoundWord } from '@/lib/predictedEnvelope'
import CardLedgerNote from './CardLedgerNote.vue'
import CardSection from './CardSection.vue'
import { useCard } from './useCard.js'

// The isotope pattern behind the assignment: a stick plot of the predicted
// lines against the measured ones, and the four-column list under it. A line
// the run asked the spectrum for and did not find, or could not test, is drawn
// hollow at its predicted height. Folded by default (section 4.1 of
// docs/dev/assignment_ledger_and_card.md).
const {
  app,
  focusedAssignment,
  familyRows,
  missingLines,
  notAskedText,
  m0Provenance,
  isoLabel,
  theoreticalRel,
  missingRel,
  formatRel,
  isPoorMatch,
  isoRowTooltip,
  focusIsotopePeak,
  previewMissingLine,
  isPreviewedLine,
  isotopeMeasure,
  ledgerServed,
  LEDGER_ISOTOPE_NOTE
} = useCard()

// A not-found line's hover text: why it reads so, and what a click does.
const missingTooltip = (line) =>
  `${notFoundTooltip(line, m0Provenance.value)}\n${
    isPreviewedLine(line)
      ? 'Click to take it off the spectrum'
      : 'Click to show where it was expected in the spectrum'
  }`

// Where the run asked no line, the summary says so in a few words, which the
// folded line has room for, and why on hover: every line beyond M0 is under
// its gate. A line the scoring found all the same is counted.
const noneAsked = computed(() => noneAskedSummary(m0Provenance.value))
const summary = computed(() => {
  const tile = isotopeMeasure.value
  if (noneAsked.value) return tile.found ? `${tile.value}, none asked` : 'M0 only: none asked'
  if (!tile.predicted) return 'M0 only'
  return `${tile.value} ${notAskedText.value ? 'asked lines' : 'lines'} found`
})

// The plot's sticks: every line at its m/z, its predicted share as the
// outline and its measured share - its intensity over the family's tallest -
// filled beside it. Heights are fractions of the plot.
const sticks = computed(() => {
  const found = familyRows.value.filter((row) => row.sample_peak_mz != null)
  const tallest = Math.max(0, ...found.map((row) => row.sample_peak_intensity ?? 0))
  const lines = [
    ...found.map((row) => ({
      key: row.peak_assignment_id,
      mz: row.sample_peak_mz,
      predicted: theoreticalRel(row),
      measured:
        tallest > 0 && row.sample_peak_intensity != null
          ? row.sample_peak_intensity / tallest
          : null,
      absent: false,
      label: isoLabel(row)
    })),
    ...missingLines.value.map((line) => ({
      key: `missing-${line.line}-${line.predicted_mz}`,
      mz: line.predicted_mz,
      predicted: missingRel(line),
      measured: null,
      absent: true,
      label: formatIsotopeLabel(line.line) || line.line
    }))
  ].filter((line) => Number.isFinite(line.mz))
  if (!lines.length) return []
  const low = Math.min(...lines.map((line) => line.mz))
  const high = Math.max(...lines.map((line) => line.mz))
  const span = high - low || 1
  // A margin of a tenth either side keeps the outermost sticks off the edges.
  return lines.map((line) => ({
    ...line,
    x: 10 + ((line.mz - low) / span) * 80,
    predictedHeight: Math.max(0, Math.min(1, line.predicted ?? 0)),
    measuredHeight: Math.max(0, Math.min(1, line.measured ?? 0))
  }))
})
const PLOT_HEIGHT = 40
const STICK = 2.4
</script>

<template>
  <CardSection
    v-if="familyRows.length"
    title="Isotope pattern"
    :summary="summary"
    :summary-tooltip="noneAsked"
    testid="section-isotopes"
  >
    <div
      class="isotopologues"
      v-help.right="{
        message: `
            <h1>Isotope pattern</h1>
            <p>
            The monoisotopic line (M0) and its isotopologues, each named by its
            heavy isotopes (<b>[13C]</b>, <b>[81Br]2</b>). The plot draws each
            line's predicted share as an outline and its measured share filled
            beside it; a line the run did not find, or could not test, is
            drawn hollow.
            </p>
            <p>
            The list gives each line's measured m/z, its m/z error and its
            predicted relative abundance (<b>abu.</b>, a fraction of the most
            abundant line). Click a line to focus its peak; a line with no
            peak moves the spectrum to where it was expected. A line the run
            expected and found no peak for reads <b>not found</b> and holds the
            assignment at <i>candidate</i> at most; one only an artifact's peak
            sits on, or beyond the ends of the spectrum, reads <b>not tested</b>.
            A line predicted too faint for this peak to show, under what the
            run asks a line to show, is <b>not asked</b>: its absence says
            nothing, and the note under the list gives the share of M0 lines
            were asked from.
            </p>`,
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-fit-score-a-pure-measurement')
      }"
    >
      <svg
        v-if="sticks.length"
        class="stick-plot"
        data-testid="stick-plot"
        :viewBox="`0 0 100 ${PLOT_HEIGHT}`"
        preserveAspectRatio="none"
        role="img"
        aria-label="Predicted isotope lines against the measured ones"
      >
        <line class="baseline" x1="0" :y1="PLOT_HEIGHT" x2="100" :y2="PLOT_HEIGHT" />
        <g v-for="stick in sticks" :key="stick.key" :data-line="stick.label">
          <rect
            :class="['stick-predicted', { hollow: stick.absent }]"
            :data-testid="stick.absent ? 'stick-absent' : 'stick-predicted'"
            :x="stick.x - STICK"
            :y="PLOT_HEIGHT * (1 - stick.predictedHeight)"
            :width="STICK"
            :height="PLOT_HEIGHT * stick.predictedHeight"
          />
          <rect
            v-if="!stick.absent"
            class="stick-measured"
            data-testid="stick-measured"
            :x="stick.x"
            :y="PLOT_HEIGHT * (1 - stick.measuredHeight)"
            :width="STICK"
            :height="PLOT_HEIGHT * stick.measuredHeight"
          />
        </g>
      </svg>
      <div class="iso-head">
        <span v-tooltip.top="'Isotope line, named by its heavy isotopes'">iso</span
        ><span v-tooltip.top="'Measured m/z of the peak'">m/z</span
        ><span v-tooltip.top="'m/z error, in ppm'">ppm</span
        ><span v-tooltip.top="'Predicted relative abundance, a fraction of the most abundant line'"
          >abu.</span
        >
      </div>
      <div class="iso-rows">
        <div
          v-for="iso in familyRows"
          :key="iso.peak_assignment_id"
          class="iso-row"
          :class="{
            current: iso.sample_peak_id === focusedAssignment.sample_peak_id,
            poor: isPoorMatch(iso)
          }"
          role="button"
          tabindex="0"
          v-tooltip.left="isoRowTooltip(iso)"
          @click="focusIsotopePeak(iso)"
          @keydown.enter.prevent="focusIsotopePeak(iso)"
          @keydown.space.prevent="focusIsotopePeak(iso)"
        >
          <span class="iso-label"
            ><span v-if="isPoorMatch(iso)" class="pi ph ph-warning poor-icon" />{{
              isoLabel(iso)
            }}</span
          >
          <span class="iso-mz">{{ num.mz.format(iso.sample_peak_mz) }}</span>
          <span class="iso-err">{{
            iso.mz_error_ppm != null ? `${num.mzError.format(iso.mz_error_ppm)}` : '—'
          }}</span>
          <span class="iso-rel">{{ formatRel(theoreticalRel(iso)) }}</span>
        </div>
        <div
          v-for="line in missingLines"
          :key="`missing-${line.line}-${line.predicted_mz}`"
          class="iso-row missing"
          :class="{ current: isPreviewedLine(line) }"
          data-testid="iso-missing"
          role="button"
          tabindex="0"
          :aria-pressed="isPreviewedLine(line) ? 'true' : 'false'"
          v-tooltip.left="missingTooltip(line)"
          @click="previewMissingLine(line)"
          @keydown.enter.prevent="previewMissingLine(line)"
          @keydown.space.prevent="previewMissingLine(line)"
        >
          <span class="iso-label">{{ formatIsotopeLabel(line.line) || line.line }}</span>
          <span class="iso-mz">{{ num.mz.format(line.predicted_mz) }}</span>
          <span class="iso-err iso-missing">{{ notFoundWord(line) }}</span>
          <span class="iso-rel">{{ formatRel(missingRel(line)) }}</span>
        </div>
      </div>
      <!-- The lines under the run's gate: its record does not list them, so
           they are named by the share of M0 it asked lines from. -->
      <p v-if="notAskedText" class="iso-not-asked" data-testid="iso-not-asked">
        {{ notAskedText }}
      </p>
      <!-- A row the batch ledger serves: its numbers are measured for the view,
           and no run asked which lines it expected, so none reads not found. -->
      <CardLedgerNote
        v-if="ledgerServed"
        class="iso-ledger-note"
        :text="LEDGER_ISOTOPE_NOTE"
        data-testid="iso-ledger-note"
      />
    </div>
  </CardSection>
</template>

<style scoped>
.iso-not-asked {
  margin: 0.3rem 0.3rem 0;
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
}
.iso-ledger-note {
  margin-top: 0.3rem;
}
.isotopologues {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.stick-plot {
  width: 100%;
  height: 3.5rem;
  margin-bottom: 0.3rem;
}
.baseline {
  stroke: var(--p-content-border-color);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.stick-predicted {
  fill: none;
  stroke: var(--p-text-color);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.stick-measured {
  fill: var(--p-primary-color);
}
/* A line asked for and not found, or not tested: hollow and fainter, as the
   prediction alone. */
.stick-predicted.hollow {
  stroke: var(--p-text-muted-color);
}
/* Four columns that share the card's width: nothing scrolls sideways, at the
   column's narrowest. */
.iso-head,
.iso-row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.6fr) minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.4rem;
  align-items: baseline;
  padding: 0.1rem 0.3rem;
  font-family: var(--font-mono);
  font-size: var(--card-text-sm);
}
.iso-head {
  color: var(--p-text-muted-color);
  font-family: var(--font-sans, inherit);
}
.iso-rows {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  max-height: 12rem;
  overflow-y: auto;
}
.iso-row {
  border-radius: 4px;
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.iso-row > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.iso-row:hover {
  background: var(--p-content-hover-background);
}
.iso-row:focus-visible {
  outline: 1px solid var(--p-focus-ring-color, var(--p-text-muted-color));
  outline-offset: -1px;
}
.iso-row.current {
  background: var(--p-highlight-background);
}
.iso-row.missing {
  color: var(--p-text-muted-color);
  font-style: italic;
}
.iso-row .iso-missing {
  font-family: var(--font-sans, inherit);
  white-space: nowrap;
}
.iso-row .iso-label {
  display: inline-flex;
  align-items: center;
  font-weight: 700;
}
.iso-row .iso-mz,
.iso-row .iso-err,
.iso-row .iso-rel,
.iso-head span:not(:first-child) {
  text-align: right;
}
.iso-row .iso-err,
.iso-row .iso-rel {
  color: var(--p-text-muted-color);
}
.iso-row.poor {
  color: var(--p-text-muted-color);
}
.poor-icon {
  margin-right: 0.2rem;
  color: var(--state-warning);
}
</style>
