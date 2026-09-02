<script setup>
import { computed } from 'vue'

import Tag from 'primevue/tag'

import { FALLBACK_TIER, tierMeta } from '@/lib/tiers'

// Confidence-tier chip for a peak assignment. Replaces BaseMatchTag's 0/1/2
// match_category with the four peak-centric tiers, optionally showing the
// evidence and a role marker (reagent/artifact/iso_child are orthogonal to tier).
//
// The number beside the tier is the EVIDENCE (fit x chemical plausibility), not
// the fit. It was the fit until tiers were bound to evidence, and the pairing
// then became a contradiction on exactly the rows that matter most: a
// chemically implausible formula with a superb mass fit would have read
// "below assignability · 95%". The chip shows the quantity that put the row in
// that band, so the label and the number can never disagree. The raw fit is
// still served on the row and shown in the inspector as the pure measurement.
//
// Where a tier is not derived from a single number at all - the batch ledger's
// consensus tier is a weighted vote over member tiers - the caller passes no
// evidence and the chip shows the tier alone, rather than borrowing a number
// that did not produce it.
const props = defineProps({
  tier: {
    type: String,
    default: 'unassigned'
  },
  evidence: {
    type: Number,
    default: null
  },
  role: {
    type: String,
    default: null
  },
  source: {
    type: String,
    default: null
  },
  // Append the evidence to the tier label.
  showEvidence: {
    type: Boolean,
    default: true
  },
  tooltip: {
    type: String,
    default: null
  },
  // The tier the engine that produced this row reached on its own terms, when
  // it stated one. Only an imported run carries it: an in-app row's engine tier
  // IS `tier`, so it arrives null and nothing is marked.
  engineTier: {
    type: String,
    default: null
  }
})

// Label, severity and icon come from the shared tier module, which also fixes
// the confidence order the ledgers sort by - the chip and the sort must name
// the same four tiers or a "below" chip can outrank an "assigned" one.
const meta = computed(() => tierMeta(props.tier))

const percentFormatter = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0
})

const evidence = computed(() => {
  const value = props.evidence
  return props.showEvidence && value != null && !Number.isNaN(value)
    ? percentFormatter.format(value)
    : null
})

const label = computed(() =>
  evidence.value ? `${meta.value.label} · ${evidence.value}` : meta.value.label
)

const roleIcon = computed(() => {
  switch (props.role) {
    case 'reagent':
      return 'pi ph ph-flask'
    case 'artifact':
      return 'pi ph ph-warning'
    case 'iso_child':
      return 'pi ph ph-arrow-elbow-down-right'
    default:
      return null
  }
})

// A curated row is the one case where the source is not a stage but a person,
// so it gets a mark of its own rather than a line in the hover text: a reader
// scanning the ledger has to be able to see which rows a human decided without
// hovering every one of them.
const isManual = computed(() => props.source === 'manual')

// But 'manual' covers two different acts, and only one of them is a choice
// about this row. When a person reassigns a peak, the backend also strips the
// isotopologue satellites of the formula the M0 no longer holds
// (curation.py's _demote) and leaves source = 'manual' on each of them, so the
// ledger's source filter shows the whole footprint of one override. That
// produces UNASSIGNED rows a person's edit is responsible for without anyone
// having chosen a formula for them - and the hand's "a person chose this
// formula" is false twice over there, since such a row carries no formula at
// all.
//
// Told apart by the tier and not by provenance.manual.action, which is what
// actually records the demotion: the ledger serves a slim row with no
// provenance on it (PeakAssignmentRecord), so the action is unreadable on most
// of the surfaces this chip renders on. The tier is readable everywhere, and
// it is exact - both curation actions commit a formula and tier_for_evidence
// never returns 'unassigned', so a demotion is the only way a manual row ends
// up at this tier. Read off the bucketed tier the chip displays rather than
// the raw prop, so the mark can never contradict the label beside it.
const isDemoted = computed(() => isManual.value && meta.value.key === FALLBACK_TIER)

// The hover line for the row's source. A demoted row gets a sentence and a mark
// of its own rather than neither: what happened to it is the least guessable
// thing about it, and left unmarked it is indistinguishable from a peak the
// engine simply never proposed anything for - the wrong answer for the person
// hunting for where their assignment went.
const sourceLine = computed(() => {
  if (isDemoted.value) {
    return (
      'Unassigned by hand: this row was unassigned when its M0 was reassigned by hand, ' +
      'superseded by the next assignment run'
    )
  }
  if (isManual.value) {
    return 'Assigned by hand: a person chose this formula, superseded by the next assignment run'
  }
  return props.source ? `Source: ${props.source}` : null
})

// The producing engine's own verdict, when it differs from this server's.
//
// Guarded on `engineTier != null` BEFORE anything from @/lib/tiers touches it:
// tierBucket(null) answers 'unassigned', so an unguarded tierMeta() would give
// every in-app row a confident "the engine said unassigned" it never said.
//
// An agreeing verdict is not news and is left unmarked - the mark means "these
// two disagree", which is the whole reason the column exists. Compared on the
// bucketed tier so the mark can never contradict the label beside it, the same
// way `isDemoted` reads `meta.key` rather than the raw prop.
const engineTierLabel = computed(() =>
  props.engineTier != null ? tierMeta(props.engineTier) : null
)
const disagrees = computed(
  () => engineTierLabel.value != null && engineTierLabel.value.key !== meta.value.key
)

const autoTooltip = computed(
  () =>
    props.tooltip ??
    [
      `Tier: ${props.tier}`,
      props.evidence != null && !Number.isNaN(props.evidence)
        ? `Evidence: ${percentFormatter.format(props.evidence)} (fit x plausibility)`
        : null,
      disagrees.value
        ? `The engine that produced this row called it ${engineTierLabel.value.label} ` +
          'on its own terms; the tier above is this server’s banding of the evidence'
        : null,
      sourceLine.value,
      props.role ? `Role: ${props.role}` : null
    ]
      .filter(Boolean)
      .join('\n')
)
</script>

<template>
  <span class="tier-tag" v-tooltip.top="autoTooltip">
    <Tag
      :value="label"
      :severity="meta.severity"
      :icon="`pi ${meta.icon}`"
      :class="['tier', tier]"
      style="font-size: 11px"
    />
    <span v-if="roleIcon" :class="[roleIcon, 'role-icon']" />
    <span
      v-if="disagrees"
      class="pi ph ph-scales engine-tier-icon"
      data-testid="engine-tier-mark"
    />
    <span v-if="isDemoted" class="pi ph ph-eraser demoted-icon" data-testid="demoted-mark" />
    <span
      v-else-if="isManual"
      class="pi ph ph-hand-pointing manual-icon"
      data-testid="manual-mark"
    />
  </span>
</template>

<style scoped>
.tier-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  white-space: nowrap;
}

/* Unassigned is a first-class outcome but visually recessive: dashed + pale. */
.tier.unassigned {
  opacity: 0.55;
  border: 1px dashed var(--p-tag-secondary-color, currentColor);
  background: transparent;
}

.role-icon {
  opacity: 0.7;
  font-size: 12px;
}

/* Not recessive like the role marker: "a person decided this" is the least
   guessable thing about a row, so it reads at full strength. */
.manual-icon {
  font-size: 12px;
  color: var(--p-primary-color, currentColor);
}

/* Recessive where the hand is not: a demoted row is the consequence of a
   decision taken on another row, not a decision about this one, so it must not
   compete for attention with the rows a person actually chose a formula for.
   It also sits beside the deliberately pale "unassigned" chip, which the full
   strength of the hand would fight. */
.demoted-icon {
  font-size: 12px;
  opacity: 0.7;
}

/* Recessive, like the demotion mark: a disagreement between two engines is
   context for the tier beside it rather than a claim about the row, and it
   appears on whole stretches of an imported run at once - at full strength it
   would read as a warning on every second row. */
.engine-tier-icon {
  font-size: 12px;
  opacity: 0.7;
}
</style>
