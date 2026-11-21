<script setup>
import { BaseTierTag } from '@/lib/base'
import CardAcrossBatch from './card/CardAcrossBatch.vue'
import CardAlternatives from './card/CardAlternatives.vue'
import CardAnswer from './card/CardAnswer.vue'
import CardIsotopePattern from './card/CardIsotopePattern.vue'
import CardLevel from './card/CardLevel.vue'
import CardMeasurements from './card/CardMeasurements.vue'
import CardNotes from './card/CardNotes.vue'
import CardVerdictBar from './card/CardVerdictBar.vue'
import CardWhy from './card/CardWhy.vue'
import { useAssignmentCard } from './card/useAssignmentCard.js'
import { provideCard } from './card/useCard.js'

// The peak inspector: the focused peak's assignment, as one card that answers
// before it explains (docs/dev/assignment_ledger_and_card.md, section 4.1).
// From the top: the answer; why its tier; the four measurements; the notes of
// a change by hand; the sections, folded; and the verdict bar, pinned at the
// foot. Each part is a component under card/, and all of them read the one
// model this file makes (card/useAssignmentCard.js).

// Toggles the Sample view's pane under the spectrum between the time series
// (default) and the composition search. Owned by the parent (PaneTabSample);
// the inspector only flips it on.
const showSearch = defineModel('showSearch', { type: Boolean, default: false })

const card = useAssignmentCard({ showSearch })
provideCard(card)
// The model's state, read and set by a parent or a test as the card's own
// (its refs unwrap on the component's public instance).
defineExpose(card)
const { app, focusedAssignment, peakSummary, showWhy, showMeasurements } = card
</script>

<template>
  <div
    v-if="app.data.peak.list.length > 0"
    class="assign-root col"
    v-help.top="{
      message: `
        <h1>Peak Inspector</h1>
        <p>
        The selected peak's assignment: its formula and ionization, why it
        holds its tier, four measurements, and folded sections for the isotope
        pattern, the alternatives, the batch's readings and the identification
        level. The verdict bar sits at the foot.
        </p>
        <p>
        Select a peak in the spectrum or the <b>Assignments</b> ledger.
        <b>Find other formulas for this peak</b> searches other compositions
        for it.
        </p>`,
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/')
    }"
  >
    <section v-if="focusedAssignment" class="inspector">
      <CardAnswer />
      <CardWhy v-if="showWhy" />
      <CardMeasurements v-if="showMeasurements" />
      <CardNotes />
      <div class="sections">
        <CardIsotopePattern />
        <CardAlternatives />
        <CardAcrossBatch />
        <CardLevel />
      </div>
      <CardVerdictBar />
    </section>
    <section v-else-if="app.data.peak.focused" class="inspector">
      <div class="answer">
        <div class="insp-formula">Unassigned</div>
        <div class="chips"><BaseTierTag tier="unassigned" /></div>
        <div class="insp-sub" data-testid="peak-summary">{{ peakSummary }}</div>
      </div>
    </section>
    <div v-else class="center no-peak">
      <div class="col no-peak-text">
        <span class="pi ph ph-cursor-click" />
        <i>Select a peak in the spectrum or ledger to inspect its assignment.</i>
      </div>
    </div>
  </div>
</template>

<style scoped>
.assign-root {
  gap: 1rem;
  align-items: stretch;
  width: 100%;
  /* Breathing room from the splitter gutter on the right. */
  padding: 0 0.75rem 0 0;
}

/* The card's type: three sizes, until the app's type tokens (MAS-160,
   frontend_design_system.md section 1) land on :root and replace them. Every
   section under card/ sets its sizes from these and nothing else. */
.inspector {
  --card-text-lg: 1.15rem;
  --card-text-base: 1rem;
  --card-text-sm: 0.85rem;
  container: card / inline-size;
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  width: 100%;
  padding: 0.9rem 1rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 8px;
  background: var(--p-content-background);
  font-size: var(--card-text-base);
}
.sections {
  display: flex;
  flex-direction: column;
}
.sections:empty {
  display: none;
}
.answer {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.insp-formula {
  font-family: var(--font-mono);
  font-size: var(--card-text-lg);
  font-weight: 700;
}
.insp-sub {
  color: var(--p-text-muted-color);
  font-family: var(--font-mono);
  font-size: var(--card-text-sm);
}
.no-peak {
  display: grid;
  place-items: center;
  min-height: 8rem;
}
.no-peak-text {
  gap: 0.75rem;
  max-width: 40ch;
  color: var(--p-text-muted-color);
  text-align: center;
}
</style>
