<script setup>
import { computed } from 'vue'

import { BaseLevelTag } from '@/lib/base'
import { heldByNote, isOpenRival, rivalTestLabel } from '@/lib/levels'
import CardLedgerNote from './CardLedgerNote.vue'
import CardSection from './CardSection.vue'
import { useCard } from './useCard.js'

// What a reader may state of the row, beside the tier and never in its place:
// the level, what earned it, what would raise it, and the rivals it was read
// against. An excluded rival names the test that excluded it; an open one - no
// test did - wears the warning colour, since it is what holds the level at a
// mass. Folded by default; the chip row carries the level itself.
const {
  app,
  chipRole,
  identificationLevel,
  batchLevel,
  rivalTooltip,
  ledgerServed,
  LEDGER_LEVEL_NOTE
} = useCard()

// Whose level it is: the live verdict's claim, which states it in place of the
// engine's, or the engine's where no live verdict states one.
const sourceLine = computed(() => {
  const level = identificationLevel.value
  if (!level) return ''
  if (level.source !== 'claim') return 'engine'
  return level.engineLevel ? `claim, the engine read ${level.engineLevel}` : 'claim'
})

const summary = computed(() => {
  const level = identificationLevel.value
  return level ? `${level.level} · ${level.name}` : ''
})
</script>

<template>
  <CardSection
    v-if="identificationLevel"
    title="Identification level"
    :summary="summary"
    testid="section-level"
  >
    <div
      class="identification-level"
      data-testid="identification-level"
      v-help.right="{
        title: 'Identification level',
        helpKey: 'assignment-level',
        doc: app.ui.help.docUrl('how-it-works/peak-assignment/#identification-level')
      }"
    >
      <div class="level-head">
        <BaseLevelTag
          :level="identificationLevel.level"
          :role="chipRole"
          :tooltip="identificationLevel.tooltip"
        />
        <span class="level-name">{{ identificationLevel.name }}</span>
      </div>
      <div class="level-line muted" data-testid="level-source">
        <span class="level-key">source</span>
        <span class="level-value">{{ sourceLine }}</span>
      </div>
      <div v-if="identificationLevel.evidence" class="level-line" data-testid="level-evidence">
        <span class="level-key">why</span>
        <span class="level-value">{{ identificationLevel.evidence }}</span>
      </div>
      <div v-if="identificationLevel.lift" class="level-line" data-testid="level-lift">
        <span class="level-key">would lift it</span>
        <span class="level-value">{{ identificationLevel.lift }}</span>
      </div>
      <div v-if="batchLevel" class="batch-level" data-testid="level-batch">
        <div class="level-head">
          <BaseLevelTag
            :level="batchLevel.level"
            :role="chipRole"
            :tooltip="batchLevel.tooltip"
          />
          <span class="level-name">{{ batchLevel.name }}</span>
          <span class="level-key">the batch's</span>
        </div>
        <div v-if="batchLevel.evidence" class="level-line" data-testid="level-batch-evidence">
          <span class="level-key">why</span>
          <span class="level-value">{{ batchLevel.evidence }}</span>
        </div>
        <div v-if="batchLevel.lift" class="level-line">
          <span class="level-key">would lift it</span>
          <span class="level-value">{{ batchLevel.lift }}</span>
        </div>
      </div>
      <div
        v-if="identificationLevel.inherited"
        class="level-line muted"
        data-testid="level-inherited"
      >
        An isotopologue: this is the level of its M0
      </div>
      <div
        v-if="identificationLevel.rivals.length || identificationLevel.unlisted"
        class="level-key rivals-label"
      >
        rivals
      </div>
      <ul v-if="identificationLevel.rivals.length" class="rivals">
        <li
          v-for="(rival, i) in identificationLevel.rivals"
          :key="`rival-${i}`"
          :class="['rival', { open: isOpenRival(rival) }]"
          data-testid="level-rival"
          v-tooltip.left="rivalTooltip(rival)"
        >
          <span
            :class="[
              'pi',
              'ph',
              isOpenRival(rival) ? 'ph-circle-dashed' : 'ph-x-circle',
              'rival-icon'
            ]"
          />
          <span class="rival-body">
            <span class="rival-head">
              <span class="rival-formula">{{
                rival.formula || rival.ion_formula || 'unnamed rival'
              }}</span>
              <span class="rival-test">{{ rivalTestLabel(rival.test) }}</span>
              <span v-if="heldByNote(rival.held_by)" class="rival-held">{{
                heldByNote(rival.held_by)
              }}</span>
            </span>
            <span v-if="rival.detail" class="rival-detail">{{ rival.detail }}</span>
          </span>
        </li>
      </ul>
      <div
        v-if="identificationLevel.unlisted"
        class="level-line muted rivals-unlisted"
        data-testid="rivals-unlisted"
      >
        {{ identificationLevel.unlisted }}
      </div>
    </div>
  </CardSection>
  <!-- A row the batch ledger serves states no level, whether or not the
       sample also has a run of its own (read on the ledger by choice). The
       section says so in its place, rather than going missing. -->
  <CardSection
    v-else-if="ledgerServed"
    title="Identification level"
    summary="none: the batch ledger states no level"
    testid="section-level"
  >
    <CardLedgerNote :text="LEDGER_LEVEL_NOTE" data-testid="level-ledger-note" />
  </CardSection>
</template>

<style scoped>
.identification-level {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: var(--card-text-sm);
}
.level-head {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
/* The batch's level beside the row's own: set apart as a borrowed reading. */
.batch-level {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding-left: 0.5rem;
  border-left: 2px solid var(--p-content-border-color);
}
.level-name,
.muted,
.rival-test,
.rival-detail,
.rival-held,
.rival-icon {
  color: var(--p-text-muted-color);
}
.level-line {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.level-key {
  flex: none;
  font-weight: 500;
}
.rivals-label {
  margin-top: 0.15rem;
}
.rivals {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.rival {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  line-height: 1.35;
  cursor: default;
}
.rival-icon {
  margin-top: 0.15rem;
}
.rival-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.rival-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
}
.rival-formula {
  font-family: var(--font-mono);
}
.rival.open .rival-icon,
.rival.open .rival-test {
  color: var(--state-warning);
  font-weight: 700;
}
.rival-held {
  font-style: italic;
}
.rival-detail {
  overflow-wrap: anywhere;
}
</style>
