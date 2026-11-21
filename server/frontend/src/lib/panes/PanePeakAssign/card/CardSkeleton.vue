<script setup>
import Skeleton from 'primevue/skeleton'

// The card's sections while the row's detail is on its way: skeleton lines
// where the Why box, the four measurements and the folded sections will be,
// so the card keeps its shape instead of growing as each part lands
// (docs/dev/assignment_ledger_and_card.md, section 4.2).
const SECTIONS = ['Isotope pattern', 'Alternatives', 'Across the batch', 'Identification level']
</script>

<template>
  <div class="card-skeleton" aria-busy="true" aria-label="Loading the assignment's detail">
    <div class="why" data-testid="skeleton-why">
      <Skeleton width="40%" height="0.9rem" />
      <Skeleton height="0.6rem" />
      <Skeleton width="70%" height="0.8rem" />
      <Skeleton width="85%" height="0.8rem" />
    </div>
    <div class="tiles" data-testid="skeleton-measurements">
      <Skeleton v-for="n in 4" :key="n" height="2.6rem" />
    </div>
    <div v-for="title in SECTIONS" :key="title" class="section" data-testid="skeleton-section">
      <span class="section-title">{{ title }}</span>
      <Skeleton width="45%" height="0.7rem" />
    </div>
  </div>
</template>

<style scoped>
.card-skeleton {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
}
.why {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
  gap: 0.5rem;
}
.section {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.4rem 0 0.4rem 1.2rem;
  border-top: 1px solid var(--p-content-border-color);
}
.section-title {
  flex: none;
  color: var(--p-text-muted-color);
  font-weight: 500;
}
</style>
