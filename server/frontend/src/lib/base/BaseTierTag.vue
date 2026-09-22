<script setup>
import { computed } from 'vue'

import Tag from 'primevue/tag'

import { FALLBACK_TIER, tierMeta } from '@/lib/tiers'

// Confidence-tier chip for a peak assignment. Replaces BaseMatchTag's 0/1/2
// match_category with the four peak-centric tiers, with a role marker
// (reagent/artifact/iso_child are orthogonal to tier).
//
// The chip names the tier alone. A percentage beside it reads as the chance
// that the assignment is right, and the number the tier is banded on is not
// one: it is the EVIDENCE, fit x chemical plausibility, a measure of how well
// the formula explains the peak. The hover text gives it, named as what it is,
// and the inspector shows it beside its two factors; P(correct) is the
// calibrated probability, shown apart from the tier.
//
// Where a tier is not derived from a single number at all - the batch ledger's
// consensus tier is a weighted vote over member tiers - the caller passes no
// evidence and the hover text names the tier alone, rather than borrowing a
// number that did not produce it.
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
  tooltip: {
    type: String,
    default: null
  }
})

// Label, severity and icon come from the shared tier module, which also fixes
// the confidence order the ledgers sort by - the chip and the sort must name
// the same four tiers or a "below" chip can outrank an "assigned" one.
const meta = computed(() => tierMeta(props.tier))

// A reagent or an artifact peak is accounted for by what made it, not by a
// formula. The engine writes such a row at tier `unassigned`, which is true -
// no compound was assigned - but a chip reading "unassigned" says nothing
// explained the peak, the opposite of what the role says. So the role is the
// chip, and it shows no evidence: none was measured.
const ROLE_CHIPS = Object.freeze({
  reagent: {
    label: 'reagent',
    icon: 'ph ph-flask',
    line: 'Reagent: an ion the ionization source makes of itself'
  },
  artifact: {
    label: 'artifact',
    icon: 'ph ph-wave-sine',
    line: 'Artifact: a ringing side lobe of a very intense neighbouring peak'
  }
})
const roleChip = computed(() =>
  Object.prototype.hasOwnProperty.call(ROLE_CHIPS, props.role) ? ROLE_CHIPS[props.role] : null
)

const percentFormatter = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0
})

const label = computed(() => (roleChip.value ? roleChip.value.label : meta.value.label))

// The small mark beside the chip, for the one role that does not replace it: an
// isotopologue holds its M0's tier, and the mark says so.
const roleIcon = computed(() =>
  props.role === 'iso_child' ? 'pi ph ph-arrow-elbow-down-right' : null
)

// A curated row is the one case where the source is not a stage but a person,
// so it gets a mark of its own rather than a line in the hover text: a reader
// scanning the ledger has to be able to see which rows a human decided without
// hovering every one of them.
const isManual = computed(() => props.source === 'manual')

// But 'manual' covers two different acts, and only one of them is a choice
// about this row. When a person reassigns a peak, the backend also strips the
// isotopologues of the formula the M0 no longer holds
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

// How a stage's source reads on hover. A source this list does not name - a
// reagent or an artifact row, which its role chip already names - says nothing.
const SOURCE_LINES = Object.freeze({
  database: 'Matched from a target or reference list',
  untargeted: 'Found by the formula search'
})

// The hover line for the row's source. A demoted row gets a sentence and a mark
// of its own rather than neither: what happened to it is the least guessable
// thing about it, and left unmarked it is indistinguishable from a peak the
// engine simply never proposed anything for - the wrong answer for the person
// hunting for where their assignment went.
const sourceLine = computed(() => {
  if (isDemoted.value) {
    return 'Unassigned by hand, with its M0; the next assignment run supersedes this'
  }
  if (isManual.value) {
    return 'Assigned by hand; the next assignment run supersedes this'
  }
  return Object.prototype.hasOwnProperty.call(SOURCE_LINES, props.source)
    ? SOURCE_LINES[props.source]
    : null
})

const autoTooltip = computed(() => {
  if (props.tooltip != null) return props.tooltip
  if (roleChip.value) {
    return [roleChip.value.line, "Counted apart from the sample's compounds", sourceLine.value]
      .filter(Boolean)
      .join('\n')
  }
  return [
    meta.value.description,
    props.evidence != null && !Number.isNaN(props.evidence)
      ? `Evidence ${percentFormatter.format(props.evidence)} (fit × plausibility)`
      : null,
    sourceLine.value,
    props.role === 'iso_child' ? 'An isotopologue: it holds the tier of its M0' : null
  ]
    .filter(Boolean)
    .join('\n')
})
</script>

<template>
  <span class="tier-tag" v-tooltip.top="autoTooltip">
    <Tag
      :value="label"
      :severity="roleChip ? 'secondary' : meta.severity"
      :icon="`pi ${roleChip ? roleChip.icon : meta.icon}`"
      :class="roleChip ? ['role', role] : ['tier', tier]"
      style="font-size: 11px"
    />
    <span v-if="roleIcon" :class="[roleIcon, 'role-icon']" />
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

/* The source's and the instrument's peaks, in the colour the spectrum draws
   them in: accounted for, and not the sample's. */
.role {
  color: #8a5ed0;
  border: 1px solid color-mix(in srgb, #8a5ed0 45%, transparent);
  background: color-mix(in srgb, #8a5ed0 10%, transparent);
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
</style>
