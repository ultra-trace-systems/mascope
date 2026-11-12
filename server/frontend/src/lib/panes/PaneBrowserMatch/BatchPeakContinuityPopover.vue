<script setup>
import { computed } from 'vue'

import {
  CONTINUITY_DISPLAY_ONLY,
  CONTINUITY_PARTIAL,
  GRADE_META,
  continuityPartial,
  continuityWarning,
  pairLine,
  pairsShownNote
} from '@/lib/batchContinuity'

/**
 * The pairs behind a batch peak's continuity grade, shown from the Continuity
 * column's popover: each partner with its grade, the samples both peaks are
 * in, the median ratio, its spread and its drift, and what the co-variation
 * reading said of two channels. The anchor's warnings - partial, behind the
 * ledger, not graded - head the list, and the display-only sentence ends it.
 */
const props = defineProps({
  /** The ledger row: continuity_grade, continuity, continuity_stale, ... */
  row: { type: Object, required: true },
  /** Whether the row is an isotope line folded under a main peak. */
  isotopeLine: { type: Boolean, default: false }
})

const grade = computed(() => GRADE_META[props.row.continuity_grade] ?? null)
// The meaning is a clause in the tooltip and a sentence of its own here.
const sentence = (clause) => `${clause[0].toUpperCase()}${clause.slice(1)}.`
const lines = computed(() =>
  (props.row.continuity?.pairs ?? []).map((pair) => pairLine(pair, props.isotopeLine))
)
const shownNote = computed(() => pairsShownNote(props.row))
const partial = computed(() => continuityPartial(props.row))
// Not time-ordered, or behind the ledger: the tooltip's sentence, kept in one wording.
const warning = computed(() => continuityWarning(props.row))
</script>

<template>
  <div class="continuity-popover" data-testid="continuity-popover">
    <div class="heading">
      <span class="title">Ratio stability</span>
      <span v-if="grade" :class="['grade', row.continuity_grade]">{{ grade.label }}</span>
    </div>
    <p v-if="grade" class="meaning">{{ sentence(grade.meaning) }}</p>
    <p v-if="warning" class="warning" data-testid="continuity-warning">{{ warning }}</p>
    <p v-if="partial" class="warning" data-testid="continuity-partial">
      {{ CONTINUITY_PARTIAL }}
    </p>
    <table v-if="lines.length" class="pairs">
      <thead>
        <tr>
          <th>Pair</th>
          <th>Grade</th>
          <th>Samples</th>
          <th>Ratio</th>
          <th>Spread</th>
          <th>Drift</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="line in lines" :key="line.key" data-testid="continuity-pair">
          <td>
            {{ line.partner }} <span class="mz">{{ line.mz }}</span>
            <div v-if="line.coVariation" class="co-variation">{{ line.coVariation }}</div>
          </td>
          <td :class="['grade-cell', line.grade]">{{ line.gradeLabel }}</td>
          <td>{{ line.samples }}</td>
          <td>{{ line.ratio }}</td>
          <td>{{ line.spread }}</td>
          <td>{{ line.drift }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="shownNote" class="note">{{ shownNote }}</p>
    <p class="note">{{ CONTINUITY_DISPLAY_ONLY }}</p>
  </div>
</template>

<style scoped>
.continuity-popover {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  width: 34rem;
  max-width: 90vw;
}
.heading {
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
}
.title {
  font-weight: 600;
}
.meaning,
.warning,
.note {
  margin: 0;
  font-size: 0.78rem;
}
.note {
  opacity: 0.75;
}
.warning {
  padding: 0.25rem 0.4rem;
  border: 1px dashed var(--state-warning);
  border-radius: 4px;
}
.pairs {
  border-collapse: collapse;
  font-size: 0.8rem;
}
.pairs th {
  text-align: left;
  font-weight: 600;
  opacity: 0.75;
  padding: 0 0.6rem 0.2rem 0;
}
.pairs td {
  padding: 0.15rem 0.6rem 0.15rem 0;
  vertical-align: top;
  white-space: nowrap;
}
.mz,
.co-variation {
  font-family: var(--font-mono, ui-monospace, monospace);
  opacity: 0.75;
}
.co-variation {
  font-size: 0.72rem;
}
.grade-cell.red,
.grade.red {
  color: var(--state-error);
}
.grade-cell.drift,
.grade.drift {
  color: var(--state-info);
}
.grade-cell.stable,
.grade.stable {
  color: var(--state-success);
}
.grade-cell.inconclusive,
.grade-cell.insufficient {
  opacity: 0.6;
}
</style>
