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

// The identity of a run this deployment produced by copying another sample's
// assignments (docs/dev/peak_assignment_copy.md section 5). Reserved server-side
// exactly as the in-app name is, so a run carrying it really was copied here -
// which is what lets this render it as first-party rather than foreign. It
// still reaches the ledger through the import channel, so it carries a
// disclosure (the copy manifest) and no calibrated P(correct), like any import.
const COPY_ENGINE = 'mascope-copy'

const engine = computed(() => props.run?.engine ?? null)
const engineKey = computed(() => engine.value?.trim().toLowerCase() ?? null)

const isCopy = computed(() => engineKey.value === COPY_ENGINE)

// Runs predating the engine column were backfilled to the in-app identity, so a
// missing engine means an old row rather than an unknown producer - read it as
// in-app rather than badging it as foreign. A copied run is this deployment's
// work too, so it is not external either, even though it was published through
// the import channel.
const isExternal = computed(
  () => !!engineKey.value && engineKey.value !== IN_APP_ENGINE && !isCopy.value
)

// The sample a copied run was copied from, named in the manifest the copy
// service discloses. Null for any other run, and for a copy whose manifest a
// future version reshapes - the label then falls back to saying only that it
// is a copy, rather than rendering "copy of undefined".
const copySource = computed(() => {
  if (!isCopy.value) return null
  const copy = props.run?.calibration?.copy
  if (!copy || typeof copy !== 'object') return null
  const name = copy.source_sample_item_name ?? copy.source_sample_item_id
  return typeof name === 'string' && name ? name : null
})

const engineLabel = computed(() => {
  if (!props.run) return ''
  if (isCopy.value) return 'Copy'
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
// Named as evidence bands rather than bare thresholds: the same 80% means one
// thing against a fit and another against fit x plausibility, and a run from an
// engine that tiers on the fit alone is exactly what this line exists to expose.
const tierBandsText = computed(() => {
  const bands = props.run?.tier_bands
  if (!bands || typeof bands !== 'object') return null
  const assigned = band(bands.assigned)
  const candidate = band(bands.candidate)
  const parts = [
    assigned ? `assigned \u2265 ${assigned}` : null,
    candidate ? `candidate \u2265 ${candidate}` : null
  ].filter(Boolean)
  return parts.length ? `Tier bands (evidence): ${parts.join(' \u00b7 ')}` : null
})

// --- Tooltips ----------------------------------------------------------------

function engineOrigin() {
  if (isCopy.value) {
    return copySource.value
      ? `Copied from sample ${copySource.value} by this Mascope deployment`
      : 'Copied from another sample of this batch by this Mascope deployment'
  }
  return isExternal.value
    ? `Published into Mascope by an external engine: ${engine.value}`
    : 'Computed by this Mascope deployment'
}

const engineTooltip = computed(() => {
  if (!props.run) return ''
  return [
    engineOrigin(),
    version.value ? `Engine version: ${version.value}` : null,
    tierBandsText.value,
    isCopy.value
      ? 'The formulas were copied; the fit, mass error and tier were re-measured against this sample. Copied rows carry no Mascope-calibrated P(correct).'
      : null,
    isExternal.value
      ? 'Imported runs carry no Mascope-calibrated P(correct); their rows show it empty.'
      : null
  ]
    .filter(Boolean)
    .join('\n')
})

// A copied run discloses its copy manifest in the same field an import
// discloses its calibration state in, so it gets the same chip - what differs
// is what the disclosure is about.
const showsDisclosure = computed(() => isExternal.value || isCopy.value)

const calibrationTooltip = computed(() => {
  if (!showsDisclosure.value) return ''
  if (!calibration.value) {
    return 'This run disclosed no calibration state.'
  }
  if (isCopy.value) {
    return [
      'How this copy was made: the source run, the per-sample mass-axis',
      'offsets it corrected for, and how many rows mapped or were dropped.',
      '',
      calibrationDump.value
    ].join('\n')
  }
  return [
    'What this engine calibrated against, as it disclosed at import.',
    'An import bypasses the server-side m/z verification gate, so this',
    'disclosure is what stands in its place.',
    '',
    calibrationDump.value
  ].join('\n')
})

const disclosureLabel = computed(() => {
  if (isCopy.value) return calibration.value ? 'copy details' : 'no copy details'
  return calibration.value ? calibrationLabel.value : 'no calibration'
})
</script>

<template>
  <span v-if="run" class="run-provenance">
    <Tag
      :value="compact || !version ? engineLabel : `${engineLabel} ${version}`"
      :severity="isExternal ? 'info' : 'secondary'"
      :icon="`pi ${isCopy ? 'pi-copy' : isExternal ? 'ph ph-upload-simple' : 'ph ph-house'}`"
      :class="['engine', isExternal ? 'external' : 'in-app']"
      style="font-size: 11px"
      v-tooltip.top="engineTooltip"
    />
    <!-- Only a run that came through the import channel has a disclosure to
         show: an in-app run's calibration state is the sample's own, which the
         engine already gates on before it will run at all. A copied run does
         come through that channel, and what it discloses is its copy
         manifest. -->
    <Tag
      v-if="showsDisclosure"
      :value="compact ? (isCopy ? 'details' : 'calibration') : disclosureLabel"
      :severity="calibration ? 'secondary' : 'warn'"
      :icon="`pi ${calibration ? (isCopy ? 'pi-copy' : 'ph ph-crosshair') : 'ph ph-warning'}`"
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
   otherwise push the row the run selector sits in out of shape. Clip the chip
   and let the tooltip carry the full name. */
.engine {
  max-width: 12rem;
}
.engine :deep(.p-tag-label) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
