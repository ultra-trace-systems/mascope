<script setup>
import { SOURCE_TILE_TITLES } from './sourceReadings.js'
import { useCard } from './useCard.js'

// The card of a row the source or the detector made: a reagent row, an ion of
// the run's chemistry, and an artifact row, a peak that is no ion. Neither
// commits a formula, so the Why box and the four measurements of an analyte
// have nothing to read on it. This says what the row is and which claim took
// the peak, shows what was measured of it, and gives one line for each reading
// an analyte's card has and this row has none of, in place of a blank.
const { app, sourceWhy, sourceMeasures, sourceNotes, batchMeasure } = useCard()
</script>

<template>
  <section
    class="source"
    data-testid="source-card"
    v-help.right="{
      message: `
        <h1>Reagent and artifact rows</h1>
        <p>
        A <b>reagent</b> row is an ion of the run's chemistry, and an
        <b>artifact</b> row a peak the detector made. Neither commits a
        formula, so the card says what the row is and which claim took the
        peak, shows what was measured of it, and says which readings of an
        analyte it has none of.
        </p>`,
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-peak-inspector')
    }"
  >
    <div v-if="sourceWhy" class="source-why" data-testid="source-why">
      <h3 class="source-title" data-testid="source-title">{{ sourceWhy.title }}</h3>
      <p class="source-lead" data-testid="source-lead">{{ sourceWhy.lead }}</p>
      <p class="source-claim" data-testid="source-claim">{{ sourceWhy.claim }}</p>
      <p v-for="note in sourceWhy.notes" :key="note" class="source-note" data-testid="source-note">
        {{ note }}
      </p>
    </div>
    <div class="tiles">
      <div
        v-for="tile in sourceMeasures"
        :key="tile.key"
        class="tile"
        :data-testid="`source-tile-${tile.key}`"
      >
        <span class="tile-title">{{ tile.title }}</span>
        <span class="tile-value" v-tooltip.top="tile.tooltip || null">{{ tile.value }}</span>
        <span class="tile-caption">{{ tile.caption }}</span>
      </div>
      <div class="tile" data-testid="source-tile-batch">
        <span class="tile-title">{{ SOURCE_TILE_TITLES.batch }}</span>
        <template v-if="batchMeasure.lines">
          <div
            v-for="line in batchMeasure.lines"
            :key="line.key"
            class="tile-line"
            :data-testid="`batch-${line.key}`"
          >
            <span class="tile-value" v-tooltip.top="line.tooltip">{{ line.value }}</span>
            <span class="tile-caption">{{ line.label }}</span>
          </div>
        </template>
        <template v-else>
          <span class="tile-value" v-tooltip.top="batchMeasure.tooltip">{{
            batchMeasure.value
          }}</span>
          <span class="tile-caption">{{ batchMeasure.caption }}</span>
        </template>
      </div>
    </div>
    <!-- What an analyte's card reads and this row has nothing of: said once
         each, so the card never reads as one that failed to load. -->
    <ul class="absent" data-testid="source-absent">
      <li
        v-for="line in sourceNotes"
        :key="line.key"
        class="absent-line"
        :data-testid="`source-absent-${line.key}`"
      >
        <span class="absent-title">{{ line.title }}.</span> {{ line.text }}
      </li>
    </ul>
  </section>
</template>

<style scoped>
.source {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.source-why {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 6px;
}
.source-title {
  margin: 0;
  font-size: var(--card-text-sm);
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--p-text-muted-color);
}
.source-lead,
.source-claim,
.source-note {
  margin: 0;
  line-height: 1.35;
}
.source-lead {
  font-weight: 600;
}
.source-note {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
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
.tile-title {
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.tile-line {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  min-width: 0;
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
.absent {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin: 0;
  padding: 0;
  list-style: none;
  color: var(--p-text-muted-color);
  font-size: var(--card-text-sm);
  line-height: 1.35;
}
.absent-title {
  font-weight: 600;
}
@container card (max-width: 340px) {
  .tiles {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
