<script setup>
import { computed } from 'vue'
import Tag from 'primevue/tag'

import { useApp } from '@/stores'
import { VERDICT_META, evidenceLabel, levelLabel } from '@/lib/verification'

/**
 * A verification verdict, as a tag or - compact - as a bare icon.
 *
 * `inherited` marks a verdict the row borrows from the batch level rather than
 * owns: the compact icon is parenthesised, the ledger's idiom for a value read
 * off another row, and the tooltip names the batch-level verdict and says that
 * verifying this row records an exception. `conflict` is a batch-level verdict
 * that disagrees with the owned one shown; the tooltip gets a line for it.
 *
 * A confirmation that names a structure (an identification claim) shows the
 * level it states in place of the evidence it follows from, and the
 * structure's name; the tooltip has the evidence and the InChIKey. A verdict
 * that states no level shows none: the row's level is the engine's.
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
const label = computed(() => {
  if (!meta.value) return ''
  const ev = evidenceLabel(props.record.evidence_level)
  // A level stands for the evidence it follows from, which the tooltip names.
  const level = levelLabel(props.record.level)
  const parts = [meta.value.label]
  if (level) parts.push(level)
  else if (props.record.verdict === 'confirmed' && ev) parts.push(ev)
  const structure = shortStructure(props.record)
  if (structure) parts.push(structure)
  if (props.inherited) parts.push('batch')
  return parts.join(' · ')
})
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
  }
  if (Number.isInteger(record.level)) {
    parts.push(`${levelLabel(record.level)}, stated when the verdict was recorded`)
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
    ><span class="paren">(</span><span :class="['pi', meta.icon]" /><span class="paren"
      >)</span
    ></span
  >
  <span
    v-else-if="meta && compact"
    :class="['verdict-icon', record.verdict, 'pi', meta.icon, { conflict: Boolean(conflict) }]"
    v-tooltip.top="`${label}\n${tooltip}`"
  />
  <Tag
    v-else-if="meta"
    :value="label"
    :severity="meta.severity"
    :icon="`pi ${meta.icon}`"
    :class="['verdict', record.verdict, { inherited }]"
    style="font-size: 11px"
    v-tooltip.top="tooltip"
  />
</template>

<style scoped>
.verdict {
  white-space: nowrap;
}
/* Borrowed from the batch level: dashed, the inspector's grammar for evidence
   read off another row. */
.verdict.inherited {
  border: 1px dashed currentColor;
}
.verdict-icon {
  font-size: 1rem;
  cursor: default;
}
.verdict-icon.inherited {
  display: inline-flex;
  align-items: center;
  font-size: 0.95rem;
}
.verdict-icon.inherited .paren {
  font-size: 0.85rem;
  line-height: 1;
  opacity: 0.55;
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
