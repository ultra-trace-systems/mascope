<script setup>
import { computed } from 'vue'

import Tag from 'primevue/tag'

// Confidence-tier chip for a peak assignment. Replaces BaseMatchTag's 0/1/2
// match_category with the four peak-centric tiers, optionally showing the fit
// score and a role marker (reagent/artifact/iso_child are orthogonal to tier).
const props = defineProps({
  tier: {
    type: String,
    default: 'unassigned'
  },
  fitScore: {
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
  // Append the fit score to the tier label.
  showFit: {
    type: Boolean,
    default: true
  },
  tooltip: {
    type: String,
    default: null
  }
})

// tier -> label, PrimeVue Tag severity, phosphor icon
const TIER_META = {
  identified: { label: 'identified', severity: 'success', icon: 'ph ph-seal-check' },
  candidate: { label: 'candidate', severity: 'warn', icon: 'ph ph-circle-half' },
  below_assignability: { label: 'below', severity: 'secondary', icon: 'ph ph-minus-circle' },
  unassigned: { label: 'unassigned', severity: 'secondary', icon: 'ph ph-circle-dashed' }
}

const meta = computed(() => TIER_META[props.tier] ?? TIER_META.unassigned)

const fitFormatter = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0
})

const fit = computed(() => {
  const value = props.fitScore
  return props.showFit && value != null && !Number.isNaN(value) ? fitFormatter.format(value) : null
})

const label = computed(() => (fit.value ? `${meta.value.label} · ${fit.value}` : meta.value.label))

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

const autoTooltip = computed(
  () =>
    props.tooltip ??
    [
      `Tier: ${props.tier}`,
      props.fitScore != null && !Number.isNaN(props.fitScore)
        ? `Fit: ${fitFormatter.format(props.fitScore)}`
        : null,
      props.source ? `Source: ${props.source}` : null,
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
</style>
