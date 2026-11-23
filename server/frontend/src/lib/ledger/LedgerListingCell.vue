<script setup>
import {
  candidatesTooltip,
  listingName,
  listingSource,
  listingTags,
  targetMore,
  targetName
} from '@/lib/referenceListings'

import { useLedgerListing } from './useLedgerListing.js'

/**
 * What a reference list calls a row's formula: the name, its list, and the
 * list's tags (background for the siloxanes), all in one line so the row keeps
 * its height. The tooltip names every name the run matched, as the
 * inspector's does. Folded, the default, the name is cut short and the list is
 * left to the tooltip; one switch writes it out in both scopes
 * (useLedgerListing.js). A row no list names renders nothing.
 */
defineProps({
  /** The row's listing (`ledgerListing` of its `reference_listing`), or null. */
  listing: { type: Object, default: null },
  /** What the target the row was matched to declares (`targetOf`), or null. */
  target: { type: Object, default: null }
})

const listingColumn = useLedgerListing()
</script>

<template>
  <span
    v-if="listing || target"
    class="listing"
    data-testid="listed-as"
    v-tooltip.top="candidatesTooltip(target, listing)"
  >
    <!-- What the target stands for, labelled as the target's, and the list's
         name beside it; one with both labels where a list names the same
         structure. -->
    <template v-if="target">
      <template v-if="listingColumn.full">
        <span class="listing-label" data-testid="target-as">target</span>
        <span class="listing-name"
          >{{ targetName(target)
          }}<template v-if="targetMore(target, listing)">
            +{{ targetMore(target, listing) }}</template
          ></span
        >
        <span v-if="target.identity?.source" class="listing-source">{{
          target.identity.source
        }}</span>
      </template>
      <span
        v-else
        class="pi ph ph-target listing-mark"
        role="img"
        data-testid="target-as"
        :aria-label="`target: ${targetName(target)}`"
      />
      <span v-if="target.unlisted" class="listing-tag" data-testid="target-unlisted">unlisted</span>
    </template>
    <template v-if="listing && !target?.identity">
      <template v-if="listingColumn.full">
        <span class="listing-name">{{ listingName(listing) }}</span>
        <span v-if="listingSource(listing)" class="listing-source">{{
          listingSource(listing)
        }}</span>
      </template>
      <span v-else class="listing-name folded" data-testid="listing-folded">{{
        listingName(listing)
      }}</span>
    </template>
    <span
      v-for="tag in listingTags(listing)"
      :key="tag"
      class="listing-tag"
      :data-testid="`list-tag-${tag}`"
      >{{ tag }}</span
    >
  </span>
</template>

<style scoped>
/* One line, the name first and cut short rather than wrapped, so the virtual
   scroller's fixed row height holds; the list and its tags after it,
   recessive, as the inspector shows them. */
.listing {
  display: inline-flex;
  align-items: baseline;
  gap: 0.35rem;
  max-width: 18rem;
  white-space: nowrap;
}
/* Folded, the target's mark stands for its name, in the tooltip. */
.listing-mark {
  opacity: 0.75;
}
.listing-name {
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
/* Folded, the name alone, cut short: the hover gives it whole with its list. */
.listing-name.folded {
  max-width: 6rem;
}
.listing-source {
  font-size: 0.72rem;
  opacity: 0.6;
}
/* Which candidate the name is: the target's declaration, in the voice of the
   inspector's key. */
.listing-label {
  font-size: 0.7rem;
  opacity: 0.6;
}
.listing-tag {
  padding: 0 0.3rem;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  border-radius: 0.25rem;
  font-size: 0.7rem;
  opacity: 0.8;
}
</style>
