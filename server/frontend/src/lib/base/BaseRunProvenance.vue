<script setup>
import { computed } from 'vue'

import Tag from 'primevue/tag'

// Provenance chips for one peak-assignment run: which engine produced it, at
// which version, and - for a run published from outside - what that engine
// disclosed about its calibration.
//
// Why this exists at all: an imported run is first-class (same tables, same
// read model, same batch fold-in) and the ledger read defaults to the latest
// completed run whatever its engine, so a fresh import is what a reader sees by
// default. It also bypasses the server-side m/z verification gate that an
// in-app run must pass, because an external engine calibrates client-side and
// discloses what it calibrated against instead. Both of those are only honest
// if a reader can see whose judgement a ledger carries before trusting a tier -
// which is this component. See docs/dev/sdk_peak_assignment.md section 8.2.
const props = defineProps({
  // PeakAssignmentRunRecord (or null while none is focused).
  run: {
    type: Object,
    default: null
  },
  // Chip-only rendering for the dense dropdown rows; the tooltip still carries
  // the full disclosure.
  compact: {
    type: Boolean,
    default: false
  }
})

// The in-app engine's identity, mirrored from the backend's IN_APP_ENGINE. It
// is reserved server-side (an import claiming it is a 422), which is what lets
// this comparison decide "ours" vs "published here" rather than merely
// "differently named". Compared case-insensitively, the same way the server
// reserves it.
const IN_APP_ENGINE = 'mascope'

const engine = computed(() => props.run?.engine ?? null)

// Runs predating the engine column were backfilled to the in-app identity, so a
// missing engine means an old row rather than an unknown producer - read it as
// in-app rather than badging it as foreign.
const isExternal = computed(
  () => !!engine.value && engine.value.trim().toLowerCase() !== IN_APP_ENGINE
)

const engineLabel = computed(() => {
  if (!props.run) return ''
  if (!isExternal.value) return 'Mascope'
  return engine.value
})

const version = computed(() => props.run?.engine_version || null)

// --- Calibration disclosure --------------------------------------------------

const calibration = computed(() => {
  const value = props.run?.calibration
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  // An empty object is not a disclosure. The server accepts it - it only
  // refuses a null - but it tells a reader nothing about what was calibrated,
  // which is the whole reason this is shown, so it is badged as the absence it
  // is rather than as a reassuring "disclosed".
  return Object.keys(value).length ? value : null
})

// `calibration` is stored verbatim as the engine sent it and the server never
// reads into it, so nothing guarantees any particular key. Surface `method`
// when it is a scalar - the shape the contract describes - and otherwise say
// only that a disclosure exists, with the whole of it on hover. Guessing more
// keys would render confidently wrong labels for an engine that named things
// differently.
const calibrationMethod = computed(() => {
  const method = calibration.value?.method
  return typeof method === 'string' || typeof method === 'number' ? String(method) : null
})

const calibrationLabel = computed(() =>
  calibrationMethod.value ? `calibration · ${calibrationMethod.value}` : 'calibration disclosed'
)

// The full disclosure, pretty-printed for the tooltip. Bounded: an engine may
// send an arbitrarily large object and a tooltip is not a JSON viewer.
const MAX_DISCLOSURE_CHARS = 600
const calibrationDump = computed(() => {
  if (!calibration.value) return null
  let text
  try {
    text = JSON.stringify(calibration.value, null, 2)
  } catch {
    // Circular or otherwise unserializable: say so rather than rendering
    // "[object Object]" as though it were the disclosure.
    return '(could not be displayed)'
  }
  return text.length > MAX_DISCLOSURE_CHARS ? `${text.slice(0, MAX_DISCLOSURE_CHARS)}\u2026` : text
})

// --- Tier bands --------------------------------------------------------------

const pctFmt = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 0 })
const band = (value) =>
  typeof value === 'number' && !Number.isNaN(value) ? pctFmt.format(value) : null

// "assigned" means nothing comparable across engines until the thresholds
// that produced it are visible, so the bands travel with the engine name.
const tierBandsText = computed(() => {
  const bands = props.run?.tier_bands
  if (!bands || typeof bands !== 'object') return null
  const assigned = band(bands.assigned)
  const candidate = band(bands.candidate)
  const parts = [
    assigned ? `assigned \u2265 ${assigned}` : null,
    candidate ? `candidate \u2265 ${candidate}` : null
  ].filter(Boolean)
  return parts.length ? `Tier bands: ${parts.join(' \u00b7 ')}` : null
})

// --- Tooltips ----------------------------------------------------------------

const engineTooltip = computed(() => {
  if (!props.run) return ''
  return [
    isExternal.value
      ? `Published into Mascope by an external engine: ${engine.value}`
      : 'Computed by this Mascope deployment',
    version.value ? `Engine version: ${version.value}` : null,
    tierBandsText.value,
    isExternal.value
      ? 'Imported runs carry no Mascope-calibrated P(correct); their rows show it empty.'
      : null
  ]
    .filter(Boolean)
    .join('\n')
})

const calibrationTooltip = computed(() => {
  if (!isExternal.value) return ''
  if (!calibration.value) {
    return 'This run disclosed no calibration state.'
  }
  return [
    'What this engine calibrated against, as it disclosed at import.',
    'An import bypasses the server-side m/z verification gate, so this',
    'disclosure is what stands in its place.',
    '',
    calibrationDump.value
  ].join('\n')
})
</script>

<template>
  <span v-if="run" class="run-provenance">
    <Tag
      :value="compact || !version ? engineLabel : `${engineLabel} ${version}`"
      :severity="isExternal ? 'info' : 'secondary'"
      :icon="`pi ${isExternal ? 'ph ph-upload-simple' : 'ph ph-house'}`"
      :class="['engine', isExternal ? 'external' : 'in-app']"
      style="font-size: 11px"
      v-tooltip.top="engineTooltip"
    />
    <!-- Only an imported run has a disclosure to show: an in-app run's
         calibration state is the sample's own, which the engine already gates
         on before it will run at all. -->
    <Tag
      v-if="isExternal"
      :value="calibration ? (compact ? 'calibration' : calibrationLabel) : 'no calibration'"
      :severity="calibration ? 'secondary' : 'warn'"
      :icon="`pi ${calibration ? 'ph ph-crosshair' : 'ph ph-warning'}`"
      class="calibration"
      style="font-size: 11px"
      v-tooltip.top="calibrationTooltip"
    />
  </span>
</template>

<style scoped>
.run-provenance {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  white-space: nowrap;
}

/* An in-app run is the unremarkable case: present, so that absence of a badge
   is never the only signal, but visually quiet next to a published one. */
.engine.in-app {
  opacity: 0.7;
}

/* An engine name is client-supplied and may be up to 64 characters, which would
   otherwise push the run selector's toolbar out of shape. Clip the chip and let
   the tooltip carry the full name. */
.engine {
  max-width: 12rem;
}
.engine :deep(.p-tag-label) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
