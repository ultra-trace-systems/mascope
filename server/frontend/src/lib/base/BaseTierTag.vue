<script setup>
import { computed } from 'vue'

import Tag from 'primevue/tag'

import { FALLBACK_TIER, tierMeta } from '@/lib/tiers'
import { sourceLine as lineOfSource } from '@/lib/assignmentSources'

// Confidence-tier chip for a peak assignment. Replaces BaseMatchTag's 0/1/2
// match_category with the four peak-centric tiers, with a role marker
// (reagent/artifact/iso_child are orthogonal to tier).
//
// The chip names the tier alone. A percentage beside it reads as the chance
// that the assignment is right, and the number the tier is banded on is not
// one: it is the EVIDENCE, fit x chemical plausibility, a measure of how well
// the formula explains the peak. The hover text gives it, named as what it is,
// and the inspector shows it beside its two factors. The calibrated
// probability (P(correct)) is served by the API and not shown while its curve
// is provisional.
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
  },
  // What holds the tier down, as a mark on the chip: `{ label, tooltip }`,
  // the reasons named for its accessible name and the hover that says what
  // each did. The tier chip says where the row stands and the mark says it
  // was held there; the card says why at length.
  cap: {
    type: Object,
    default: null
  },
  // A second reading of the tier beside this server's - an importing
  // engine's own - drawn outlined, with no ground, so the two chips in one
  // cell never read as one claim drawn twice.
  outlined: {
    type: Boolean,
    default: false
  }
})

// Label, severity and dot come from the shared tier module, which also fixes
// the confidence order the ledgers sort by - the chip and the sort must name
// the same four tiers or a "below" chip can outrank an "assigned" one.
//
// The chip's face is a dot, not a glyph: filled for assigned, half for
// candidate, a ring for below, a dashed ring for unassigned. A glyph is a
// reading's (lib/evidence/marks.js), and the seal-check, half circle and minus
// circle the tiers used to wear were each a second meaning of a mark.
const meta = computed(() => tierMeta(props.tier))

// A reagent or an artifact peak is accounted for by what made it, not by a
// formula. The engine writes such a row at tier `unassigned`, which is true -
// no compound was assigned - but a chip reading "unassigned" says nothing
// explained the peak, the opposite of what the role says. So the role is the
// chip, and it shows no evidence: none was measured. It wears no glyph and no
// dot: the flask and the wave it once wore meant a listing and co-variation
// too, and a dot would read as a tier. Its colour, --role-reagent, says it.
const ROLE_CHIPS = Object.freeze({
  reagent: {
    label: 'reagent',
    line: 'Reagent: an ion the ionization source made, of its reagent, the air or its calibrant, or by breaking an analyte'
  },
  artifact: {
    label: 'artifact',
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

// How a stage's source reads on hover, in the words every surface shares
// (assignmentSources.js). Only the two engine stages get a line here: a reagent
// or an artifact row's role chip already names it, and a manual row gets a
// sentence of its own below.
const STAGE_SOURCES = Object.freeze(['database', 'untargeted'])

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
  return STAGE_SOURCES.includes(props.source) ? lineOfSource(props.source) : null
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
    props.role === 'iso_child' ? 'An isotopologue: it holds the tier of its M0' : null,
    // What holds the tier down, on the chip's own hover as on its mark's: a
    // reader who hovers the tier sees why it is not higher.
    props.cap?.tooltip ?? null
  ]
    .filter(Boolean)
    .join('\n')
})
</script>

<template>
  <span class="tier-tag" :class="{ outlined }" v-tooltip.top="autoTooltip">
    <Tag
      v-if="roleChip"
      :value="label"
      severity="secondary"
      :class="['role', role]"
      data-testid="role-chip"
      style="font-size: 11px"
    />
    <Tag
      v-else
      :value="label"
      :severity="meta.severity"
      :class="['tier', meta.key]"
      style="font-size: 11px"
    >
      <template #icon>
        <span
          :class="['tier-dot', meta.dot]"
          data-testid="tier-dot"
          :data-dot="meta.dot"
          aria-hidden="true"
        />
      </template>
      <!-- Inside the chip, after its word: the cap belongs to the tier it
           holds, not to the row beside it. The default slot stands in for
           Tag's own label, so the word is drawn here as Tag draws it.
           `tier-word` is the hook the narrow ledger hides it from sight by
           (lib/ledger/narrow.css), leaving the dot: a role chip, which has
           no dot, has no such word and keeps its own. -->
      <span class="p-tag-label tier-word">{{ label }}</span>
      <span
        v-if="cap"
        class="pi ph ph-arrow-down cap-mark"
        role="img"
        data-testid="tier-reason-mark"
        :aria-label="cap.label"
        v-tooltip.top="cap.tooltip"
      />
    </Tag>
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

/* The cap's mark, in the chip's own colour, a step smaller than its word. */
.cap-mark {
  margin-left: 0.15rem;
  font-size: 10px;
}

/* An engine's own tier, beside this server's: the chip's colour on its
   border and its word, with no ground. */
.tier-tag.outlined :deep(.p-tag) {
  background: transparent;
  border: 1px solid currentColor;
}

/* The source's and the instrument's peaks, in the role's own token: accounted
   for, and not the sample's. */
.role {
  color: var(--role-reagent);
  border: 1px solid color-mix(in srgb, var(--role-reagent) 45%, transparent);
  background: color-mix(in srgb, var(--role-reagent) 10%, transparent);
}

/* The tier's face, in the chip's own colour: filled, half, a ring, and the
   one dashed mark of the assignment views, the ring of the outcome not yet
   known. */
.tier-dot {
  display: inline-block;
  box-sizing: border-box;
  width: 8px;
  height: 8px;
  border: 1.5px solid currentColor;
  border-radius: 50%;
}
.tier-dot.filled {
  background: currentColor;
}
.tier-dot.half {
  background: linear-gradient(90deg, currentColor 50%, transparent 50%);
}
.tier-dot.dashed {
  border-style: dashed;
}

/* Unassigned is a first-class outcome but visually recessive: pale, with no
   ground; its dashed ring says it is not yet known. */
.tier.unassigned {
  opacity: 0.55;
  background: transparent;
}

.role-icon {
  opacity: 0.7;
  font-size: 12px;
}

/* Not recessive like the role marker: "a person decided this" is the least
   guessable thing about a row, so it reads at full strength - in the human
   tone of the evidence badges, the text colour, never the accent, which means
   Mascope and the one primary action (lib/evidence/marks.js, TONES). */
.manual-icon {
  font-size: 12px;
  color: var(--p-text-color);
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
