<script setup>
import { computed, ref, watch } from 'vue'

import Button from 'primevue/button'
import Select from 'primevue/select'
import InputText from 'primevue/inputtext'

import { useApp } from '@/stores'
import { BaseTierTag, BaseVerdictBadge } from '@/lib/base'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula } from '@/lib/chem'
import { EVIDENCE_LEVELS } from '@/lib/verification'

const app = useApp()

// Toggles the Sample view's bottom pane between the time series (default) and
// the Re-search panel. Owned by the parent (PaneTabSample); the inspector only
// flips it on.
const showSearch = defineModel('showSearch', { type: Boolean, default: false })

// The committed assignment for the focused peak (from the latest run).
const focusedAssignment = computed(() =>
  app.data.peakAssignment.peak.forPeak(app.data.peak.focused?.peak_id)
)

// The ledger rows are a slim projection; the full record (alternatives +
// provenance) is fetched per assignment when its peak is focused. Until it
// arrives the inspector renders the slim fields and the detail sections fill
// in, so a slow fetch degrades to less detail rather than an empty inspector.
const focusedDetail = computed(() =>
  app.data.peakAssignment.peak.detailOf(focusedAssignment.value?.peak_assignment_id)
)
watch(
  focusedAssignment,
  (assignment) => {
    // Failures already toast via the http layer; the inspector just stays slim.
    if (assignment) app.data.peakAssignment.peak.loadDetail(assignment).catch(() => {})
  },
  { immediate: true }
)

// What the card is about when there is no formula to name it by: an unassigned
// ledger row, or a focused peak with no assignment row at all. Both otherwise
// read "Unassigned" over an empty evidence grid - identical for every
// unassigned peak in the sample, so the card never says which peak it is.
// The ledger row calls the intensity `sample_peak_intensity`; the bare peak
// record calls it `height`.
const peakSummary = computed(() => {
  const mz = focusedAssignment.value?.sample_peak_mz ?? app.data.peak.focused?.mz
  const intensity = focusedAssignment.value?.sample_peak_intensity ?? app.data.peak.focused?.height
  return [
    mz != null ? `m/z ${num.mz.format(mz)}` : null,
    intensity != null ? `intensity ${num.peakIntensity.format(intensity)}` : null
  ]
    .filter(Boolean)
    .join(' · ')
})

// --- Verification (labelling) capture -------------------------------------
// The current verdict for the focused assignment (by stable identity), plus a
// small confirm/reject/unsure form. See docs/dev/verification_capture_frontend.md.
const verification = computed(() =>
  app.data.peakAssignment.verification.forAssignment(focusedAssignment.value)
)

// What a verdict captured here is about: the compound, which is the family's M0
// even when the focused peak is one of its isotopologues. Judging an M+1 apart from
// its M0 is not a thing a chemist does - it is the same compound - so the form
// reads and writes through the M0 whichever family member is in view.
const verifyTarget = computed(() => app.data.peakAssignment.peak.m0Of(focusedAssignment.value))

// Whether the card is judging a row other than the one it is showing - the one
// case worth saying out loud (see the template). Keyed on whether the resolution
// actually redirected, not on the role: an isotopologue whose ion lost its M0 peak
// carries no owner, stands for itself, and gets a verdict covering nothing but
// itself. Announcing a family there would promise a scope the write does not
// have.
const verifyingFamily = computed(
  () => Boolean(verifyTarget.value) && verifyTarget.value !== focusedAssignment.value
)

// Only a real assignment can be judged. A formula-less row is a placeholder for
// a peak nothing explained, so a verdict on it is an opinion about nothing: it
// is stored and listed as a hand label, but carries no evidence for the
// confidence calibration to learn from, and its stable identity
// (`sample_peak_id|assigned_formula|ionization_mechanism_id`) is degenerate
// without a formula. Read off the M0, since that is the row being judged.
const verifiable = computed(() => Boolean(verifyTarget.value?.assigned_formula))

const editing = ref(false) // form open despite an existing verdict (re-verify)
const evidenceLevel = ref(null)
const note = ref('')
const submitting = ref(false)
const pendingVerdict = ref(null) // which button is mid-submit
const denied = ref(false) // 403: not an editor on this sample

// Show the capture form when there is no verdict yet, or the user chose to edit.
const showVerifyForm = computed(
  () => verifiable.value && !denied.value && (!verification.value || editing.value)
)

function startEdit() {
  evidenceLevel.value = verification.value?.evidence_level ?? null
  note.value = verification.value?.note ?? ''
  editing.value = true
}

async function submitVerdict(verdict) {
  // Confirm requires an evidence level (also enforced server-side).
  if (verdict === 'confirmed' && !evidenceLevel.value) return
  submitting.value = true
  pendingVerdict.value = verdict
  try {
    await app.data.peakAssignment.verification.verify({
      peak_assignment_id: verifyTarget.value.peak_assignment_id,
      verdict,
      evidence_level: evidenceLevel.value || null,
      note: note.value?.trim() || null
    })
    editing.value = false
    note.value = ''
  } catch (error) {
    // The http layer already toasts; only 403 changes the UI (hide the control).
    if (error?.response?.status === 403) denied.value = true
  } finally {
    submitting.value = false
    pendingVerdict.value = null
  }
}

// Fresh form per compound, not per peak: the form judges the family's M0, so
// stepping from an isotopologue to its own M0 (or between two isotopologues) is still
// the same judgment and must not throw away a half-written note. Keyed on the
// peak, an in-progress verdict was wiped by a click inside the family table the
// card itself renders. Editor access is re-evaluated per sample.
watch(
  () => verifyTarget.value?.peak_assignment_id,
  () => {
    editing.value = false
    evidenceLevel.value = null
    note.value = ''
  }
)
watch(
  () => app.data.sample.focusedId,
  () => {
    denied.value = false
    curateDenied.value = false
  }
)

// Arbitration / chemistry provenance: chemical plausibility (Seven Golden
// Rules), arbitration confidence, calibrated P(correct), and a tie flag.
// From the detail fetch; the fallback covers pre-slim rows that carry it.
const provenance = computed(
  () => focusedDetail.value?.provenance ?? focusedAssignment.value?.provenance ?? null
)

// Close alternatives (runner-ups), same detail-then-fallback resolution, with
// the committed assignment screened out of its own shortlist.
//
// The engine no longer writes the winner into `alternatives`, but every run
// stored before it learned not to still carries it, and only re-running the
// sample rewrites those rows - so the card filters as well rather than showing
// the analyst the peak's own answer as an alternative to itself.
//
// An entry restates the assignment when it names the same formula through the
// same ionization. The untargeted shortlist is formula-only, so an entry with
// no `ion_formula` cannot be evidence of a different mechanism: a missing one
// counts as the same, and only a present-and-different one keeps the entry.
const alternatives = computed(() => {
  const stored = focusedDetail.value?.alternatives ?? focusedAssignment.value?.alternatives ?? []
  const committed = focusedAssignment.value
  if (!committed?.assigned_formula) return stored
  return stored.filter(
    (alt) =>
      alt?.assigned_formula !== committed.assigned_formula ||
      (alt?.ion_formula != null && alt.ion_formula !== committed.ion_formula)
  )
})

const fitPercent = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0
})
const formatFit = (value) =>
  value != null && !Number.isNaN(value) ? fitPercent.format(value) : '-'

// The isotopologue family (M0 + M+1, M+2 ...) of the focused assignment.
const family = computed(() => app.data.peakAssignment.peak.familyOf(focusedAssignment.value))

// Main isotope (M0) of the family; theoretical abundances are relative to it.
const m0 = computed(
  () => family.value.find((f) => f.role === 'M0' || f.isotope_label === 'M0') ?? null
)

// Adduct-corroboration signal (P3): present only when the compound was seen via
// several adducts (co-occurrence) -- winner-only, calibrated assignments. The
// boost is already folded into p_correct, so the badge is purely informational.
//
// The engine writes `provenance.corroboration` onto the M0 winner alone: an
// isotopologue is the same ion measured at another isotope, not a second sighting
// of the compound. The evidence is about the formula the family shares, so a
// focused isotopologue shows its M0's count, flagged inherited. Only the count
// carries across - the corroborating adducts are named in the M0's provenance,
// and detail is fetched for the focused assignment alone.
const corroboration = computed(() => {
  const own = provenance.value?.corroboration
  if (own?.n_adducts != null) return { ...own, inherited: false }
  // The slim ledger row carries the count flattened, so the badge is there
  // before the detail fetch lands - just without the adduct names.
  const flat = focusedAssignment.value?.corroboration_adducts
  if (flat != null) return { n_adducts: flat, adducts: [], inherited: false }
  // Same two-step as the ledger's, so the two panes agree about a family whose
  // rows carry provenance inline (a backend predating the slim projection).
  const fromM0 = m0.value?.corroboration_adducts ?? m0.value?.provenance?.corroboration?.n_adducts
  return fromM0 != null ? { n_adducts: fromM0, adducts: [], inherited: true } : null
})

// The badge says "via M0" on its face, not only on hover: the count is the same
// number the M0 shows, and an isotopologue that displayed it unqualified would read
// as a peak seen through several adducts in its own right.
const corroborationLabel = computed(() => {
  const c = corroboration.value
  if (!c) return ''
  return `Supported by ${c.n_adducts} adducts${c.inherited ? ' via M0' : ''}`
})

// The boost is folded into the record that carries the corroboration - the M0's
// p_correct - and never into a child's, which stays calibrated on its own
// evidence (engine.py::_fold_adduct_corroboration rewrites M0 winners only). So
// an inherited badge must not claim the number beside it already accounts for
// this, which is the one thing the M0's wording does say.
const corroborationTooltip = computed(() => {
  const c = corroboration.value
  if (!c) return ''
  if (c.inherited) {
    return (
      `The M0 of this isotopologue family was seen via ${c.n_adducts} adducts. ` +
      "Independent corroborating evidence for the formula, folded into the M0's " +
      "P(correct) - not into this isotopologue's, which is calibrated on its own."
    )
  }
  const adducts = (c.adducts ?? []).join(', ')
  return (
    `Seen via ${c.n_adducts} adducts${adducts ? ` (${adducts})` : ''}. ` +
    'Independent corroborating evidence, already folded into P(correct).'
  )
})

// Compact substitution label (e.g. "[15N]", "[81Br][2H]") from the full
// isotopologue formula; falls back to the M0/M+1 offset label.
const isoLabel = (iso) =>
  iso.isotope_formula ? formatIsotopeFormula(iso.isotope_formula) : iso.isotope_label || '-'

// Theoretical (predicted) relative abundance of an isotopologue as a fraction
// of M0, recovered from the stored errors:
//   theoretical_rel = observed_rel / (1 + abundance_error),  observed_rel = I / I(M0)
const theoreticalRel = (iso) => {
  const base = m0.value?.sample_peak_intensity
  if (!base || base <= 0 || iso.sample_peak_intensity == null) return null
  const observed = iso.sample_peak_intensity / base
  const denom = 1 + (iso.abundance_error ?? 0)
  return denom > 0 ? observed / denom : null
}
const relAbuFmt = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 0,
  maximumFractionDigits: 1
})
const formatRel = (value) => (value != null ? relAbuFmt.format(value) : '-')

// Focus the peak behind an isotopologue row. The engine stringifies peak_id
// into sample_peak_id, so the join has to coerce both sides -- and a miss must
// leave the focus alone rather than clear it, which is what focus() would do
// with an id that resolves to nothing.
const focusIsotopePeak = (iso) => {
  const peak = app.data.peak.list.find((p) => String(p.peak_id) === String(iso.sample_peak_id))
  if (peak) app.data.peak.focus(peak)
}

// Per-isotopologue match quality; M0 is the reference and never "poor".
const isPoorMatch = (iso) => {
  if (iso.role === 'M0' || iso.isotope_label === 'M0') return false
  const ab = iso.abundance_error != null ? 1 - Math.min(1, Math.abs(iso.abundance_error)) : 1
  const mz = iso.mz_error_ppm != null ? Math.max(0, 1 - 0.01 * Math.abs(iso.mz_error_ppm)) : 1
  return ab * mz < 0.5
}

// Stats for a close alternative (runner-up), surfaced on hover. Scored
// runner-ups (from either stage) carry fit + m/z error + plausibility; entries
// from the untargeted finder's formula-only shortlist have no per-candidate
// fit, so fit reads "not scored" for them.
const altTooltip = (alt) => {
  const lines = [
    `fit: ${alt.fit_score != null ? formatFit(alt.fit_score) : '— not scored (untargeted)'}`
  ]
  if (alt.mz_error_ppm != null) {
    lines.push(`m/z error: ${num.mzError.format(alt.mz_error_ppm)} ppm`)
  }
  lines.push(`plausibility: ${alt.plausibility != null ? formatFit(alt.plausibility) : '—'}`)
  if (alt.source) lines.push(`source: ${alt.source}`)
  return lines.join('\n')
}

// --- Manual curation ------------------------------------------------------
// Commit a runner-up as this peak's assignment. The row is edited in place and
// marked as human-made; the winner it replaces becomes the first close
// alternative, so the same control undoes the change.
//
// Deliberately about THIS row, not the family M0 a verdict is redirected to: an
// index into `alternatives` only means anything against the list the card is
// showing.
const curating = ref(null) // index of the alternative being committed
const curateDenied = ref(false) // 403: not an editor on this sample

// The untargeted finder's formula-only shortlist entries are promotable - a
// formula with no fit is still a formula. An entry that names none is not:
// there would be nothing to commit, and the server refuses it.
const canPromote = (alt) => Boolean(alt?.assigned_formula)

async function promoteAlternative(alt, index) {
  if (!canPromote(alt) || curating.value !== null) return
  curating.value = index
  try {
    await app.data.peakAssignment.peak.curate(focusedAssignment.value.peak_assignment_id, {
      action: 'promote_alternative',
      alternative_index: index,
      // Checked server-side against the list as it stands now, so a click on a
      // card another curator has already changed underneath is refused rather
      // than committing whichever candidate now holds that position.
      expected_formula: alt.assigned_formula
    })
  } catch (error) {
    // The http layer already toasts; only 403 changes the UI (hide the control).
    if (error?.response?.status === 403) curateDenied.value = true
  } finally {
    curating.value = null
  }
}

// What an override says about itself. `source` is on the slim ledger row, so
// the note appears at once; the formula it replaced arrives with the detail.
const manualOverride = computed(() => provenance.value?.manual ?? null)
</script>

<template>
  <div
    v-if="app.data.peak.list.length > 0"
    class="assign-root col"
    style="gap: 1rem; align-items: stretch; width: 100%"
    v-help.top="{
      message: `
        <h1>Peak Inspector</h1>
        <p>
        The committed assignment for the selected peak: its fitted composition,
        confidence tier, evidence, isotopologue family and close alternatives.
        </p>
        <p>
        Select peaks by clicking them in the spectrum chart, or via the
        <b>Assignments</b> ledger. Use <b>Re-search</b> to search compositions
        for the peak on demand.
        </p>`,
      doc: app.ui.help.docUrl('how-it-works/peak-assignment/')
    }"
  >
    <section v-if="focusedAssignment" class="inspector">
      <div class="insp-head">
        <div class="insp-formula">
          {{ focusedAssignment.assigned_formula || 'Unassigned' }}
        </div>
        <BaseTierTag
          :tier="focusedAssignment.tier"
          :fit-score="focusedAssignment.fit_score"
          :role="focusedAssignment.role"
          :source="focusedAssignment.source"
          v-help.right="{
            title: 'Confidence Tiers',
            helpKey: 'assignment-tiers',
            doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
          }"
        />
      </div>
      <!-- With no formula the headline is the word "Unassigned" and the
           evidence grid below is empty, so the peak itself has to name the
           card. -->
      <div class="insp-sub" v-if="!focusedAssignment.assigned_formula">{{ peakSummary }}</div>
      <div
        class="insp-sub"
        v-if="
          focusedAssignment.ion_formula ||
          focusedAssignment.isotope_label ||
          focusedAssignment.source
        "
      >
        <span v-if="focusedAssignment.ion_formula">{{ focusedAssignment.ion_formula }}</span>
        <span v-if="focusedAssignment.isotope_label">
          &middot; {{ focusedAssignment.isotope_label }}</span
        >
        <span v-if="focusedAssignment.source" class="src">
          &middot; {{ focusedAssignment.source }}</span
        >
      </div>
      <div
        class="evidence"
        v-help.right="{
          title: 'Evidence',
          helpKey: 'assignment-evidence',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-fit-score-a-pure-measurement')
        }"
      >
        <div class="ev">
          <span class="k">fit</span>
          <span class="v">{{ formatFit(focusedAssignment.fit_score) }}</span>
        </div>
        <div class="ev" v-if="focusedAssignment.mz_error_ppm != null">
          <span class="k">m/z error</span>
          <span class="v">{{ num.mzError.format(focusedAssignment.mz_error_ppm) }} ppm</span>
        </div>
        <div class="ev" v-if="focusedAssignment.abundance_error != null">
          <span class="k">abund. error</span>
          <span class="v">{{
            num.relativeAbundanceError.format(focusedAssignment.abundance_error)
          }}</span>
        </div>
        <div class="ev" v-if="focusedAssignment.isotope_label">
          <span class="k">isotope</span>
          <span class="v">{{ focusedAssignment.isotope_label }}</span>
        </div>
        <div class="ev" v-if="provenance?.plausibility != null">
          <span class="k" v-tooltip.top="'Chemical plausibility (Seven Golden Rules)'"
            >plausibility</span
          >
          <span class="v">{{ formatFit(provenance.plausibility) }}</span>
        </div>
        <div class="ev" v-if="provenance?.confidence != null">
          <span
            class="k"
            v-tooltip.top="'Arbitration confidence: winner share of fit x plausibility'"
            >confidence</span
          >
          <span class="v"
            >{{ formatFit(provenance.confidence)
            }}<span
              v-if="provenance.is_tie"
              class="tie-flag"
              v-tooltip.top="'Runner-up too close to call'"
              >&nbsp;tie</span
            ></span
          >
        </div>
        <div class="ev" v-if="provenance && provenance.calibrated !== undefined">
          <span class="k" v-tooltip.top="'Calibrated probability the assignment is correct'"
            >P(correct)</span
          >
          <span class="v" v-if="provenance.p_correct != null">
            {{ formatFit(provenance.p_correct)
            }}<span
              v-if="provenance.calibration?.provisional"
              class="prov-flag"
              v-tooltip.top="'Provisional calibration curve - directionally right, not hardened'"
              >&nbsp;prov.</span
            ></span
          >
          <span class="v uncal" v-else v-tooltip.top="'No calibration curve for this instrument'"
            >uncalibrated</span
          >
        </div>
      </div>
      <div
        v-if="corroboration && corroboration.n_adducts > 1"
        class="corroboration"
        :class="{ inherited: corroboration.inherited }"
        v-tooltip.top="corroborationTooltip"
      >
        <span class="pi ph ph-link-simple" />
        {{ corroborationLabel }}
      </div>
      <div
        v-if="family.length > 1"
        class="isotopologues"
        v-help.right="{
          message: `
              <h1>Isotopologues</h1>
              <p>
              The isotope pattern behind this assignment: the main peak (M0)
              and its isotopologues (M+1, M+2 ...), each with its m/z error and its
              estimated relative abundance (<b>abu.</b>, as a fraction of M0).
              </p>
              <p>
              Click a row to focus that peak. Greyed rows with a warning icon are
              isotopologues whose abundance or m/z fit the prediction poorly.
              </p>`,
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#the-fit-score-a-pure-measurement')
        }"
      >
        <div class="alts-label">Isotopologues</div>
        <div class="iso-head">
          <span>iso</span><span>m/z</span><span>ppm</span
          ><span v-tooltip.top="'Estimated relative abundance (fraction of M0)'">abu.</span>
        </div>
        <div class="iso-rows">
          <div
            v-for="iso in family"
            :key="iso.peak_assignment_id"
            class="iso-row"
            :class="{
              current: iso.sample_peak_id === focusedAssignment.sample_peak_id,
              poor: isPoorMatch(iso)
            }"
            v-tooltip.left="
              isPoorMatch(iso)
                ? 'Poorly matched isotopologue (abundance / m/z off) - click to focus'
                : 'Focus this isotopologue peak'
            "
            @click="focusIsotopePeak(iso)"
          >
            <span class="iso-label" v-tooltip.left="iso.isotope_formula || iso.isotope_label"
              ><span v-if="isPoorMatch(iso)" class="pi ph ph-warning poor-icon" />{{
                isoLabel(iso)
              }}</span
            >
            <span class="iso-mz">{{ num.mz.format(iso.sample_peak_mz) }}</span>
            <span class="iso-err">{{
              iso.mz_error_ppm != null ? `${num.mzError.format(iso.mz_error_ppm)}` : '—'
            }}</span>
            <span class="iso-rel">{{ formatRel(theoreticalRel(iso)) }}</span>
          </div>
        </div>
      </div>
      <div
        v-if="alternatives.length"
        class="alts"
        v-help.right="{
          message: `
              <h1>Close Alternatives</h1>
              <p>
              Runner-up candidates that also fit this peak but lost the arbitration
              to the committed assignment. Scored runner-ups show their fit; some
              untargeted candidates come from the composition finder's formula-only
              shortlist and show chemical plausibility only, or read as not scored.
              </p>
              <p>
              <b>Use this</b> commits a candidate as the peak's assignment. The row is
              marked as assigned by hand and the assignment it replaces becomes the
              first alternative here, so the same button undoes the change.
              Re-assigning the sample recomputes the ledger and replaces it.
              </p>`,
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#assigning-a-peak-yourself')
        }"
      >
        <div class="alts-label">
          Close alternatives
          <span class="alts-count">{{ alternatives.length }}</span>
        </div>
        <div class="alts-list">
          <div
            v-for="(alt, i) in alternatives"
            :key="i"
            class="alt"
            v-tooltip.left="altTooltip(alt)"
          >
            <span class="f">{{ alt.assigned_formula || alt.ion_formula || '?' }}</span>
            <span class="s">
              <span v-if="alt.fit_score != null"
                >fit {{ formatFit(alt.fit_score)
                }}<span v-if="alt.mz_error_ppm != null">
                  &middot; {{ num.mzError.format(alt.mz_error_ppm) }} ppm</span
                ></span
              >
              <span v-else-if="alt.plausibility != null"
                >plaus {{ formatFit(alt.plausibility) }}</span
              >
              <span v-else class="no-stats"><span class="pi ph ph-info" /></span>
            </span>
            <Button
              v-if="canPromote(alt) && !curateDenied"
              :class="['alt-use', { busy: curating === i }]"
              label="use this"
              size="small"
              text
              severity="secondary"
              icon="pi ph ph-hand-pointing"
              :disabled="curating !== null"
              :loading="curating === i"
              @click="promoteAlternative(alt, i)"
            />
          </div>
        </div>
        <div v-if="curateDenied" class="verify-denied">
          <span class="pi ph ph-lock-simple" /> Editor access is required to change an
          assignment.
        </div>
      </div>
      <div v-if="focusedAssignment.source === 'manual'" class="manual-note">
        <span class="pi ph ph-hand-pointing" />
        <span>
          Assigned by hand<template v-if="manualOverride?.previous_formula">
            in place of {{ manualOverride.previous_formula }}, which is now the first close
            alternative - "use this" on it to undo</template
          >. The next assignment run for this sample recomputes the ledger and supersedes the
          change; record a verification to keep the judgement.
        </span>
      </div>
      <div
        v-if="verifiable"
        class="verify"
        v-help.right="{
          title: 'Verification',
          helpKey: 'assignment-verification',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#verifying-assignments')
        }"
      >
        <div class="alts-label">Verification</div>
        <div v-if="verifyingFamily" class="verify-family">
          <span class="pi ph ph-arrow-bend-up-left" />
          One verdict per compound: recorded on the M0 ({{ verifyTarget.assigned_formula }}) and
          shown on every isotopologue in this family.
        </div>
        <div v-if="verification && !editing" class="verify-current">
          <BaseVerdictBadge :record="verification" />
          <Button
            v-if="!denied"
            label="edit"
            size="small"
            text
            severity="secondary"
            icon="pi ph ph-pencil-simple"
            v-tooltip.top="'Change the verdict'"
            @click="startEdit"
          />
        </div>
        <template v-else-if="showVerifyForm">
          <div class="verify-buttons">
            <Button
              label="Confirm"
              icon="pi ph ph-check-circle"
              size="small"
              severity="success"
              :disabled="submitting || !evidenceLevel"
              :loading="submitting && pendingVerdict === 'confirmed'"
              v-tooltip.top="!evidenceLevel ? 'Pick an evidence level to confirm' : ''"
              @click="submitVerdict('confirmed')"
            />
            <Button
              label="Reject"
              icon="pi ph ph-x-circle"
              size="small"
              severity="danger"
              :disabled="submitting"
              :loading="submitting && pendingVerdict === 'rejected'"
              @click="submitVerdict('rejected')"
            />
            <Button
              label="Unsure"
              icon="pi ph ph-question"
              size="small"
              severity="secondary"
              :disabled="submitting"
              :loading="submitting && pendingVerdict === 'unsure'"
              @click="submitVerdict('unsure')"
            />
          </div>
          <Select
            v-model="evidenceLevel"
            :options="EVIDENCE_LEVELS"
            optionLabel="label"
            optionValue="value"
            placeholder="Evidence level (required to confirm)"
            size="small"
            showClear
            fluid
          />
          <InputText v-model="note" placeholder="Note (optional)" size="small" fluid />
          <div v-if="editing" class="verify-edit-actions">
            <Button
              label="Cancel"
              size="small"
              text
              severity="secondary"
              @click="editing = false"
            />
          </div>
        </template>
        <div v-else-if="denied" class="verify-denied">
          <span class="pi ph ph-lock-simple" /> Editor access is required to verify.
        </div>
      </div>
      <div class="insp-actions">
        <Button
          :label="showSearch ? 'Hide search' : 'Re-search'"
          size="small"
          text
          :severity="showSearch ? 'primary' : 'secondary'"
          icon="pi ph ph-magnifying-glass"
          v-tooltip.top="'Search compositions for this peak in the panel below'"
          @click="showSearch = !showSearch"
        />
      </div>
    </section>
    <section v-else-if="app.data.peak.focused" class="inspector">
      <div class="insp-head">
        <div class="insp-formula">Unassigned</div>
        <BaseTierTag tier="unassigned" />
      </div>
      <div class="insp-sub">{{ peakSummary }}</div>
      <div class="insp-actions">
        <Button
          :label="showSearch ? 'Hide search' : 'Re-search'"
          size="small"
          text
          :severity="showSearch ? 'primary' : 'secondary'"
          icon="pi ph ph-magnifying-glass"
          v-tooltip.top="'Search compositions for this peak in the panel below'"
          @click="showSearch = !showSearch"
        />
      </div>
    </section>
    <div v-else class="center no-peak">
      <div class="col" style="gap: 0.75rem; max-width: 40ch; text-align: center; opacity: 0.6">
        <span class="pi ph ph-cursor-click" style="font-size: 1.4rem" />
        <i>Select a peak in the spectrum or ledger to inspect its assignment.</i>
      </div>
    </div>
  </div>
</template>

<style scoped>
.assign-root {
  /* Breathing room from the splitter gutter on the right. */
  padding: 0 0.75rem 0 0;
}

/* Peak inspector: the committed assignment for the focused peak. */
.inspector {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  padding: 0.9rem 1rem;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  border-radius: 8px;
  background: var(--p-content-background, transparent);
  width: 100%;
}
.insp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.insp-formula {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 1.35rem;
  font-weight: 700;
}
.insp-sub {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.9rem;
  opacity: 0.7;
}
.insp-sub .src {
  text-transform: capitalize;
}
.evidence {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.4rem 1rem;
}
.ev {
  display: flex;
  flex-direction: column;
  font-family: var(--font-mono, ui-monospace, monospace);
}
.ev .k {
  font-size: 0.68rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  opacity: 0.55;
}
.ev .v {
  font-size: 0.98rem;
  font-variant-numeric: tabular-nums;
}
.alts {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.alts-label {
  font-size: 0.7rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  opacity: 0.55;
}
.alt {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.6rem;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.86rem;
  padding: 0.15rem 0.2rem;
  border-bottom: 1px solid var(--p-content-border-color, #eef0f4);
  border-radius: 3px;
  cursor: default;
}
.alt:hover {
  background: var(--p-content-hover-background, rgba(127, 127, 127, 0.12));
}
.alt .s {
  opacity: 0.6;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.alt .no-stats {
  opacity: 0.5;
}
/* The action is the row's, not the pane's: it stays out of the way until the
   pointer is on the candidate it would commit, so the list still reads as a
   list of evidence rather than a row of buttons. */
.alt-use {
  opacity: 0;
  transition: opacity 0.12s ease-in-out;
}
.alt:hover .alt-use,
.alt-use:focus-visible,
.alt-use.busy {
  opacity: 1;
}
.manual-note {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  font-size: 0.78rem;
  line-height: 1.35;
  opacity: 0.75;
}
.manual-note > .pi {
  color: var(--p-primary-color, currentColor);
  margin-top: 0.1rem;
}
.insp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

/* Verification (labelling) capture. Confirm / Reject / Unsure share equal width
   -> equal prominence (reject is a first-class negative label, not an
   afterthought). */
.verify {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.verify-current {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.verify-buttons {
  display: flex;
  gap: 0.4rem;
}
.verify-buttons > :deep(.p-button) {
  flex: 1;
}
.verify-edit-actions {
  display: flex;
  justify-content: flex-end;
}
.verify-denied {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.78rem;
  opacity: 0.7;
}
/* Says which row a verdict captured from an isotopologue is really about. Quiet:
   it explains the control rather than competing with it. */
.verify-family {
  display: flex;
  align-items: baseline;
  gap: 0.35rem;
  font-size: 0.78rem;
  opacity: 0.7;
}

/* Isotopologue envelope of the focused assignment (M0 + M+1, M+2 ...). */
.isotopologues {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.iso-head,
.iso-row {
  display: grid;
  /* Fixed content tracks + a trailing spacer so the numeric columns stay snug
     instead of the m/z column stretching across the full-width card. */
  grid-template-columns: 4.5rem 6rem 3.5rem 3.5rem 1fr;
  gap: 0.5rem;
  align-items: baseline;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.85rem;
  padding: 0.15rem 0.3rem;
}
.iso-head {
  font-size: 0.68rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  opacity: 0.5;
}
.iso-rows {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  max-height: 12rem;
  overflow-y: auto;
}
.iso-row {
  border-radius: 4px;
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}
.iso-row:hover {
  background: var(--p-content-hover-background, rgba(127, 127, 127, 0.12));
}
.iso-row.current {
  background: color-mix(in srgb, var(--p-primary-color, #6366f1) 14%, transparent);
}
.iso-row .iso-label {
  font-weight: 600;
  display: inline-flex;
  align-items: center;
}
.iso-row .iso-err,
.iso-row .iso-rel {
  opacity: 0.7;
  text-align: right;
}
/* Right-align the numeric columns (m/z, ppm, abu.) and their headers so the
   values form a tidy block instead of drifting apart. */
.iso-row .iso-mz,
.iso-head span:not(:first-child) {
  text-align: right;
}
.iso-row.poor {
  color: var(--p-surface-400, #9aa2b1);
}
.poor-icon {
  color: var(--p-orange-500, #f59e0b);
  font-size: 0.68rem;
  margin-right: 0.2rem;
}
.tie-flag {
  color: var(--p-orange-500, #f59e0b);
  font-weight: 600;
  font-size: 0.72rem;
}
.prov-flag {
  color: var(--p-orange-500, #f59e0b);
  font-size: 0.66rem;
}
/* Adduct-corroboration badge: a real compound seen via several adducts. */
.corroboration {
  align-self: start;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.74rem;
  padding: 0.12rem 0.55rem;
  border-radius: 100px;
  color: var(--p-teal-600, #0d9488);
  background: color-mix(in srgb, var(--p-teal-500, #14b8a6) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--p-teal-500, #14b8a6) 32%, transparent);
  cursor: default;
}
.corroboration .pi {
  font-size: 0.8rem;
}
/* Corroboration read off the family's M0 rather than measured on this peak. The
   badge says so in words too - dimming it instead would borrow the "no value
   here" idiom the uncalibrated states use, and cost contrast the pill needs. */
.corroboration.inherited {
  border-style: dashed;
}
.ev .v.uncal {
  opacity: 0.55;
  font-style: italic;
}
.alts-list {
  display: flex;
  flex-direction: column;
  max-height: 11rem;
  overflow-y: auto;
}
.alts-count {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.6rem;
  opacity: 0.6;
  border: 1px solid var(--p-content-border-color, #e3e6ec);
  border-radius: 100px;
  padding: 0 0.35rem;
  margin-left: 0.2rem;
}
.no-peak {
  display: grid;
  place-items: center;
  min-height: 8rem;
}
</style>
