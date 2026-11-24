<script setup>
import { computed } from 'vue'
import Tag from 'primevue/tag'

import { useApp } from '@/stores'
import { VERDICT_META, claimOf, evidenceLabel, levelLabel, windowText } from '@/lib/verification'

/**
 * A verification verdict, as a tag or - compact - as a bare icon. With no
 * record it draws nothing: a row nobody has judged carries no mark at all.
 *
 * `inherited` marks a verdict the row borrows from the batch level rather than
 * owns: the same icon, with the batch's own mark - the `#` the breadcrumb names
 * a batch by - small at its upper right, and the tooltip names the batch-level
 * verdict and says that verifying this row records an exception. Never
 * dashed: a dashed border means *not yet known*, and a batch-level verdict is
 * known (docs/dev/assignment_ledger_and_card.md, section 5). `conflict` is a batch-level verdict
 * that disagrees with the owned one shown; the tooltip gets a line for it.
 *
 * A confirmation that names a structure (an identification claim) shows the
 * level it states in place of the evidence it follows from, the number and the
 * scale's letter (`Level 2b`; a claim made under version 1 of the mapping has
 * no letter), and the structure's name, or at 3b the class's; the tooltip has
 * the evidence, the InChIKey, what earned the level and what would lift it,
 * and a level-1 batch claim's window. A verdict that states no level shows
 * none: the row's level is the engine's. A batch claim reaching a sample its
 * window leaves out comes with no structure and no level (`outside_window`),
 * and the tooltip says why. A rejection on a reference standard that refuted a confirmation
 * says which claim it refuted.
 */
const props = defineProps({
  record: {
    type: Object,
    default: null
  },
  compact: {
    type: Boolean,
    default: false
  },
  inherited: {
    type: Boolean,
    default: false
  },
  conflict: {
    type: Object,
    default: null
  }
})

const app = useApp()
const meta = computed(() => (props.record ? VERDICT_META[props.record.verdict] : null))
// The confirmation a rejection on a reference standard refuted, if any.
const refuted = computed(() => {
  const refutes = props.record?.context?.refutes
  return refutes && typeof refutes === 'object' ? refutes : null
})
const label = computed(() => {
  if (!meta.value) return ''
  const ev = evidenceLabel(props.record.evidence_level)
  // A level stands for the evidence it follows from, which the tooltip names.
  const level = levelLabel(props.record.level, props.record.level_letter)
  const parts = [meta.value.label]
  if (level) parts.push(level)
  else if (props.record.verdict === 'confirmed' && ev) parts.push(ev)
  else if (refuted.value) {
    const was = levelLabel(refuted.value.level, refuted.value.level_letter)
    parts.push(was ? `refutes ${was}` : 'refutes')
  }
  const structure = shortStructure(refuted.value ?? props.record)
  if (structure) parts.push(structure)
  if (props.inherited) parts.push('batch')
  return parts.join(' · ')
})
// A class claim (3b) names a class, by name alone.
const isClass = (record) =>
  record?.level === 3 && record?.level_letter === 'b' && !record?.claimed_inchikey
// The structure a claim names, short enough for a pill: a long name cut, and a
// bare InChIKey by its first block, the skeleton's. The tooltip has both whole.
const NAME_CHARS = 24
const shortStructure = (record) => {
  const name = record?.claimed_name
  if (name) return name.length > NAME_CHARS ? `${name.slice(0, NAME_CHARS - 1)}…` : name
  const key = record?.claimed_inchikey
  return key ? `${key.split('-')[0]}…` : null
}
const whoAndWhen = (record) => {
  const who =
    record.verified_by && app.auth?.user?.id === record.verified_by
      ? 'you'
      : record.verified_by
        ? `user #${record.verified_by}`
        : 'unknown'
  const when = record.verified_utc ? new Date(record.verified_utc).toLocaleString() : ''
  return `${who}${when ? ` · ${when}` : ''}`
}
const tooltip = computed(() => {
  const record = props.record
  if (!record) return ''
  const parts = [
    props.inherited
      ? `Batch-level verdict on ${record.assigned_formula}, by ${whoAndWhen(record)}`
      : `Verified by ${whoAndWhen(record)}`
  ]
  if (record.evidence_level) parts.push(`Evidence: ${evidenceLabel(record.evidence_level)}`)
  if (record.claimed_inchikey) {
    parts.push(
      `Structure: ${record.claimed_name ? `${record.claimed_name} (${record.claimed_inchikey})` : record.claimed_inchikey}`
    )
  } else if (isClass(record) && record.claimed_name) {
    parts.push(`Class: ${record.claimed_name}`)
  }
  const claim = claimOf(record)
  if (Number.isInteger(record.level)) {
    parts.push(
      `${levelLabel(record.level, record.level_letter)}, stated when the verdict was recorded`
    )
    if (claim?.evidence) parts.push(`Why: ${claim.evidence}`)
    if (claim?.lift) parts.push(`Would lift it: ${claim.lift}`)
  }
  const span = windowText(record)
  if (span) parts.push(`Level 1 for the samples acquired ${span}`)
  if (record.outside_window) {
    parts.push(
      "This sample was acquired outside the claim's window: it keeps the verdict on the formula, names no structure and reads the engine's level"
    )
  }
  if (refuted.value) {
    const was = levelLabel(refuted.value.level, refuted.value.level_letter)
    const what = refuted.value.claimed_name ?? refuted.value.claimed_inchikey
    parts.push(
      `A reference standard disagreed: refutes the confirmation${was ? ` at ${was}` : ''}${
        what ? `, ${what}` : ''
      }. The row reads the engine's level again`
    )
  }
  if (record.note) parts.push(`Note: ${record.note}`)
  if (props.inherited) {
    parts.push(
      'Covers every sample in the batch without a verdict of its own; verifying this sample records an exception.'
    )
  }
  if (props.conflict) {
    parts.push(
      `Batch-level verdict differs: ${VERDICT_META[props.conflict.verdict]?.label ?? props.conflict.verdict}`
    )
  }
  return parts.join('\n')
})
</script>

<template>
  <span
    v-if="meta && compact && inherited"
    :class="['verdict-icon', 'inherited', record.verdict]"
    v-tooltip.top="`${label}\n${tooltip}`"
    ><span :class="['pi', meta.icon]" /><span
      class="pi pi-hashtag batch-mark"
      data-testid="batch-mark"
      aria-hidden="true"
  /></span>
  <span
    v-else-if="meta && compact"
    :class="['verdict-icon', record.verdict, 'pi', meta.icon, { conflict: Boolean(conflict) }]"
    v-tooltip.top="`${label}\n${tooltip}`"
  />
  <Tag
    v-else-if="meta"
    :value="label"
    :severity="meta.severity"
    :class="['verdict', record.verdict, { inherited }]"
    style="font-size: 11px"
    v-tooltip.top="tooltip"
  >
    <template #icon>
      <span :class="['verdict-face', { inherited }]"
        ><span :class="['pi', meta.icon]" /><span
          v-if="inherited"
          class="pi pi-hashtag batch-mark"
          data-testid="batch-mark"
          aria-hidden="true"
      /></span>
    </template>
  </Tag>
</template>

<style scoped>
.verdict {
  white-space: nowrap;
}
.verdict-icon {
  font-size: 1rem;
  cursor: default;
}
/* Borrowed from the batch level: the batch's mark at the icon's upper right,
   at 9 px, the one difference from a verdict the row owns. */
.verdict-icon.inherited,
.verdict-face.inherited {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.verdict-face {
  display: inline-flex;
}
.batch-mark {
  position: absolute;
  top: -4px;
  right: -6px;
  font-size: 9px;
  line-height: 1;
}
.verdict-face.inherited {
  margin-right: 4px;
}
.verdict-icon.confirmed {
  color: var(--state-success);
}
.verdict-icon.rejected {
  color: var(--state-error);
}
.verdict-icon.unsure {
  color: var(--state-warning);
}
</style>
