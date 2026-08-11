<script setup>
import { computed } from 'vue'

import Tag from 'primevue/tag'

import { useApp } from '@/stores'
import { VERDICT_META, evidenceLabel } from '@/lib/verification'

// Compact chip for an assignment's current verification verdict. Renders nothing
// when the assignment has no verdict yet. Confirmed shows its evidence level;
// hover reveals who/when + note.
const props = defineProps({
  // AssignmentVerificationRecord (or null when unverified)
  record: {
    type: Object,
    default: null
  },
  // Icon-only rendering for dense contexts (ledger rows); full info on hover.
  compact: {
    type: Boolean,
    default: false
  }
})

const app = useApp()

const meta = computed(() => (props.record ? VERDICT_META[props.record.verdict] : null))

const label = computed(() => {
  if (!meta.value) return ''
  const ev = evidenceLabel(props.record.evidence_level)
  return props.record.verdict === 'confirmed' && ev
    ? `${meta.value.label} · ${ev}`
    : meta.value.label
})

const tooltip = computed(() => {
  const record = props.record
  if (!record) return ''
  const who =
    record.verified_by && app.auth?.user?.id === record.verified_by
      ? 'you'
      : record.verified_by
        ? `user #${record.verified_by}`
        : 'unknown'
  const when = record.verified_utc ? new Date(record.verified_utc).toLocaleString() : ''
  const parts = [`Verified by ${who}${when ? ` · ${when}` : ''}`]
  if (record.evidence_level) parts.push(`Evidence: ${evidenceLabel(record.evidence_level)}`)
  if (record.note) parts.push(`Note: ${record.note}`)
  return parts.join('\n')
})
</script>

<template>
  <span
    v-if="meta && compact"
    :class="['verdict-icon', record.verdict, 'pi', meta.icon]"
    v-tooltip.top="`${label}\n${tooltip}`"
  />
  <Tag
    v-else-if="meta"
    :value="label"
    :severity="meta.severity"
    :icon="`pi ${meta.icon}`"
    :class="['verdict', record.verdict]"
    style="font-size: 11px"
    v-tooltip.top="tooltip"
  />
</template>

<style scoped>
.verdict {
  white-space: nowrap;
}
.verdict-icon {
  font-size: 1rem;
  cursor: default;
}
.verdict-icon.confirmed {
  color: var(--p-green-600, #1f9d63);
}
.verdict-icon.rejected {
  color: var(--p-red-500, #ef4444);
}
.verdict-icon.unsure {
  color: var(--p-amber-500, #f59e0b);
}
</style>
