<script setup>
import { computed, nextTick, ref, useId, watch } from 'vue'

import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Popover from 'primevue/popover'
import RadioButton from 'primevue/radiobutton'

import { useApp } from '@/stores'
import { BaseTierTag, BaseVerdictBadge } from '@/lib/base'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula, neutralKey } from '@/lib/chem'
import {
  LEDGER_CONFIDENCE_TOOLTIP,
  LEDGER_P_CORRECT_TOOLTIP,
  P_CORRECT_TOOLTIP,
  uncalibratedReason
} from '@/lib/pCorrect'
import { listingName, listingOf, listingSource, listingTooltip } from '@/lib/referenceListings'
import { holdsTierDown, reasonIcon, reasonTooltip, tierReasonsOf } from '@/lib/tierReasons'
import { EVIDENCE_LEVELS, VERDICT_META } from '@/lib/verification'
import { useBatchPeakCuration } from './stores/batchPeakCuration.js'

const app = useApp()
const curation = useBatchPeakCuration()

// Toggles the Sample view's pane under the spectrum between the time series
// (default) and the Re-search panel. Owned by the parent (PaneTabSample); the
// inspector only flips it on.
const showSearch = defineModel('showSearch', { type: Boolean, default: false })

// The committed assignment for the focused peak (from the latest run).
const focusedAssignment = computed(() =>
  app.data.peakAssignment.peak.forPeak(app.data.peak.focused?.peak_id)
)

// Names the chip beside it, because two tier chips in a row are otherwise a
// puzzle: they are not two readings of one scale but two different judgements,
// and only one of them is this server's.
const engineTierTooltip = computed(() => {
  const tier = focusedAssignment.value?.engine_tier
  if (!tier) return null
  const agrees = tier === focusedAssignment.value?.tier
  return (
    `The producing engine's own tier: ${tier}\n` +
    (agrees
      ? 'It agrees with this server’s banding of the evidence.'
      : 'It differs from this server’s banding of the evidence — the engine ' +
        'reached this on its own terms (arbitration, corroboration, degeneracy), ' +
        'which the tier beside it does not account for.')
  )
})

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

// The batch-level verdict on the species this peak folded into, when the
// judgment is about this peak's own claim. Shown as borrowed evidence above the
// capture controls: a verdict recorded here is a per-sample exception to it.
const anchorVerdict = computed(() =>
  verifyTarget.value ? app.data.peakAssignment.anchorContext.overlayFor(verifyTarget.value) : null
)
const anchorConflict = computed(() =>
  anchorVerdict.value &&
  verification.value &&
  anchorVerdict.value.verdict !== verification.value.verdict
    ? anchorVerdict.value
    : null
)
const anchorVerdictLabel = computed(() =>
  anchorVerdict.value
    ? (VERDICT_META[anchorVerdict.value.verdict]?.label ?? anchorVerdict.value.verdict)
    : ''
)
const anchorVerdictTooltip = computed(() => {
  const record = anchorVerdict.value
  if (!record) return ''
  const who =
    record.verified_by && app.auth?.user?.id === record.verified_by
      ? 'you'
      : record.verified_by
        ? `user #${record.verified_by}`
        : 'unknown'
  const when = record.verified_utc ? new Date(record.verified_utc).toLocaleString() : ''
  return [
    `Batch-level verdict on ${record.assigned_formula}, by ${who}${when ? ` · ${when}` : ''}`,
    'It covers every sample in this batch that has no verdict of its own.'
  ].join('\n')
})

// Only a real assignment can be judged. A formula-less row is a placeholder for
// a peak nothing explained, so a verdict on it is an opinion about nothing: it
// is stored and listed as a hand label, but carries no evidence for the
// confidence calibration to learn from, and its stable identity
// (`sample_peak_id|assigned_formula|ionization_mechanism_id`) is degenerate
// without a formula. Read off the M0, since that is the row being judged.
const verifiable = computed(() => Boolean(verifyTarget.value?.assigned_formula))

const editing = ref(false) // verdict buttons shown despite an existing verdict (re-verify)
const evidenceLevel = ref(null)
const note = ref('')
const submitting = ref(false)
const pendingVerdict = ref(null) // which button is mid-submit
const denied = ref(false) // 403: not an editor on this sample

// Show the verdict buttons when there is no verdict yet, or the user chose to
// change it.
const showVerifyForm = computed(
  () => verifiable.value && !denied.value && (!verification.value || editing.value)
)

function startEdit() {
  evidenceLevel.value = verification.value?.evidence_level ?? null
  note.value = verification.value?.note ?? ''
  editing.value = true
}

// Each verdict is recorded from a small dialog rather than the card carrying
// its fields for every peak: any verdict takes a note, and a confirmation also
// names the evidence level behind it.
const verdictDialog = ref(null)
const dialogVerdict = ref(null) // the verdict the open dialog records
const dialogId = useId()
// The dialog's wording and submit button, matching the button that opened it.
const VERDICT_DIALOGS = {
  confirmed: {
    verb: 'Confirm',
    label: 'Confirm',
    icon: 'pi ph ph-check-circle',
    severity: 'success'
  },
  rejected: {
    verb: 'Reject',
    label: 'Reject',
    icon: 'pi ph ph-x-circle',
    severity: 'danger'
  },
  unsure: {
    verb: 'Unsure about',
    label: 'Unsure',
    icon: 'pi ph ph-question',
    severity: 'secondary'
  }
}
const dialogMeta = computed(() => VERDICT_DIALOGS[dialogVerdict.value] ?? VERDICT_DIALOGS.confirmed)
const confirming = computed(() => dialogVerdict.value === 'confirmed')
// Where focus lands as the dialog opens: on a confirmation, the level already
// picked when the verdict being changed carries one, else the strongest; on
// the others, the note. The dialog focuses nothing on its own (Popover only
// looks for an `[autofocus]` child).
const autofocusLevel = computed(() => evidenceLevel.value ?? EVIDENCE_LEVELS[0].value)

function openVerdict(verdict, event) {
  const dialog = verdictDialog.value
  if (!dialog) return
  const wasOpen = dialog.visible
  dialogVerdict.value = verdict
  dialog.show(event)
  // Open already, for another verdict: it moves to the button just clicked, and
  // focus to what this verdict asks for first.
  if (wasOpen) {
    nextTick(() => {
      dialog.alignOverlay?.()
      dialog.focus?.()
    })
  }
}

// Resolves whether the verdict was recorded.
async function submitVerdict(verdict) {
  const confirm = verdict === 'confirmed'
  // Confirm requires an evidence level (also enforced server-side).
  if (confirm && !evidenceLevel.value) return false
  submitting.value = true
  pendingVerdict.value = verdict
  try {
    await app.data.peakAssignment.verification.verify({
      peak_assignment_id: verifyTarget.value.peak_assignment_id,
      verdict,
      evidence_level: confirm ? evidenceLevel.value : null,
      note: note.value?.trim() || null
    })
    editing.value = false
    note.value = ''
    return true
  } catch (error) {
    // The http layer already toasts; only 403 changes the UI (hide the control).
    if (error?.response?.status === 403) denied.value = true
    return false
  } finally {
    submitting.value = false
    pendingVerdict.value = null
  }
}

// The dialog's own submit. It stays open on a failure, with what was entered,
// so the verdict can be sent again; a refusal closes it, since the buttons it
// opened from give way to the note that says editor access is required.
async function submitDialog() {
  if (submitting.value || !dialogVerdict.value) return
  const recorded = await submitVerdict(dialogVerdict.value)
  if (recorded || denied.value) verdictDialog.value?.hide()
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
    // An open dialog judges the compound it was opened on, and no other.
    verdictDialog.value?.hide()
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
const storedAlternatives = computed(() => {
  const stored = focusedDetail.value?.alternatives ?? focusedAssignment.value?.alternatives ?? []
  const committed = focusedAssignment.value
  if (!committed?.assigned_formula) return stored
  const committedNeutral = neutralKey(committed.assigned_formula)
  return stored.filter(
    (alt) =>
      neutralKey(alt?.assigned_formula) !== committedNeutral ||
      (alt?.ion_formula != null && alt.ion_formula !== committed.ion_formula)
  )
})

// The untargeted finder's shortlist entries carry no fit and no adduct: they
// are compositions whose mass fits the peak, listed before the run picked a
// winner, and the run does not measure them (one isotope-envelope match per
// candidate per peak is a whole-sample cost). The server measures them for one
// peak on request; the results arrive here and are matched to their entries by
// formula rather than by position, because the list rendered above is filtered
// and the server indexes the stored one.
//
// A score is session data, not part of the run - see `promoteAlternative` for
// what committing one therefore is.
const altScores = computed(() =>
  app.data.peakAssignment.peak.altScoresOf(focusedAssignment.value?.peak_assignment_id)
)
const scoring = computed(() =>
  app.data.peakAssignment.peak.altScoresPending(focusedAssignment.value?.peak_assignment_id)
)
const scoreByFormula = computed(() => {
  const map = new Map()
  for (const score of altScores.value ?? []) map.set(score.assigned_formula, score)
  return map
})

// Each entry with whatever the server measured for it hung off it. `scored` is
// null for an entry the run already scored (it needs nothing) and for one the
// measurement has not reached yet.
//
// And null for the undo entry, deliberately, however well it measures. That
// row's control is the undo, and the undo is what a measurement cannot make
// possible: the isotopologues this override cleared are restored by compound AND
// adduct, and the archive recorded no adduct for them, so an adduct found now
// will never match the one they were archived under. Committing it would put
// the formula back on the M0 alone and leave its family unassigned - under a
// note on this same card that says the assignment cannot be put back by hand.
const alternatives = computed(() =>
  storedAlternatives.value.map((alt, index) => ({
    ...alt,
    scored:
      alt?.assigned_formula && !isUndoEntry(alt, index)
        ? (scoreByFormula.value.get(alt.assigned_formula) ?? null)
        : null
  }))
)

// Whether this row has entries worth measuring at all: the ones with a formula
// and no adduct, which is exactly what the server would score. Checked here so
// the request is only made for rows that have something to gain from it.
const hasUnscored = computed(() =>
  storedAlternatives.value.some(
    (alt) => alt?.assigned_formula && !alt?.ionization_mechanism_id && !alt?.target_ion_id
  )
)

// Fired off the detail rather than off the focus, because the shortlist only
// arrives with the detail - and only for rows that actually have one. Failures
// already toast via the http layer; the cards just keep reading "not measured".
watch(
  [focusedDetail, hasUnscored],
  () => {
    if (!hasUnscored.value) return
    const assignment = focusedAssignment.value
    if (assignment) app.data.peakAssignment.peak.loadAltScores(assignment).catch(() => {})
  },
  { immediate: true }
)

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

// Corroboration signal: present only when the same neutral was committed through
// more than one ionization channel, which is independent evidence for the
// formula rather than for this peak.
//
// Two counts feed it and the run may carry either. `cross_channel` is the one
// the finished ledger measures - every committed row is grouped by neutral
// across the channels the run searched - and `corroboration` (P3) is the older
// per-compound count, which reaches only rows a curated identity claimed and is
// therefore absent from most of a ledger. Where both exist the first is the
// second plus whatever the untargeted stage committed of the same neutral, so it
// is never the smaller number and is preferred. `scored` is the difference that
// matters on screen: only the curated count is folded into p_correct.
//
// Neither is written onto an isotopologue: it is the same ion measured at
// another isotope, not a second sighting of the compound. The evidence is about
// the formula the family shares, so a focused isotopologue shows its M0's count,
// flagged inherited. Only the count carries across - the channels are named in
// the M0's provenance, and detail is fetched for the focused assignment alone.
const corroboration = computed(() => {
  const channels = provenance.value?.cross_channel?.channels
  if (channels?.length) {
    return { n: channels.length, names: channels, scored: false, inherited: false }
  }
  const own = provenance.value?.corroboration
  if (own?.n_adducts != null) {
    return { n: own.n_adducts, names: own.adducts ?? [], scored: true, inherited: false }
  }
  // The slim ledger row carries both counts flattened, so the badge is there
  // before the detail fetch lands - just without the channel names.
  const flatChannels = focusedAssignment.value?.corroboration_channels
  if (flatChannels != null) {
    return { n: flatChannels, names: [], scored: false, inherited: false }
  }
  const flat = focusedAssignment.value?.corroboration_adducts
  if (flat != null) return { n: flat, names: [], scored: true, inherited: false }
  // Same two-step as the ledger's, so the two panes agree about a family whose
  // rows carry provenance inline (a backend predating the slim projection).
  const m0Channels =
    m0.value?.corroboration_channels ?? m0.value?.provenance?.cross_channel?.channels?.length
  if (m0Channels != null) return { n: m0Channels, names: [], scored: false, inherited: true }
  const fromM0 = m0.value?.corroboration_adducts ?? m0.value?.provenance?.corroboration?.n_adducts
  return fromM0 != null ? { n: fromM0, names: [], scored: true, inherited: true } : null
})

// The badge says "via M0" on its face, not only on hover: the count is the same
// number the M0 shows, and an isotopologue that displayed it unqualified would read
// as a peak seen through several channels in its own right.
//
// "channels" rather than "adducts", which is what this said while the count was
// the curated per-compound one: protonation is in the count and is not an
// adduct, so the older word named the number wrongly as soon as it changed.
const corroborationLabel = computed(() => {
  const c = corroboration.value
  if (!c) return ''
  return `Supported by ${c.n} channels${c.inherited ? ' via M0' : ''}`
})

// What the badge must not do is claim the number beside it accounts for this.
// The P3 boost is folded into the record that carries the corroboration - the
// M0's p_correct - and never into a child's, which stays calibrated on its own
// evidence (engine.py::_fold_adduct_corroboration rewrites M0 winners only); and
// the ledger-measured count is not folded into anything at all, being evidence
// the run recorded rather than a score it applied. So the sentence about
// P(correct) is written from `scored` and `inherited` rather than assumed.
const corroborationTooltip = computed(() => {
  const c = corroboration.value
  if (!c) return ''
  if (c.inherited) {
    return (
      `The M0 of this isotopologue family was seen through ${c.n} channels. ` +
      'Independent corroborating evidence for the formula, ' +
      (c.scored
        ? "folded into the M0's P(correct) - not into this isotopologue's, which is " +
          'calibrated on its own.'
        : 'not included in the P(correct) beside it.')
    )
  }
  const channels = (c.names ?? []).join(', ')
  return (
    `Seen through ${c.n} channels${channels ? ` (${channels})` : ''}. ` +
    'Independent corroborating evidence, ' +
    (c.scored ? 'already folded into P(correct).' : 'not included in the P(correct) beside it.')
  )
})

// Compact substitution label (e.g. "[13C]", "[81Br]2") from the full
// isotopologue formula; falls back to the M0/M+1 offset label.
//
// Counted from the family's M0, which for a labelled ion is a bracketed line
// itself: the ion formula names the labels, so the 15N-nitrate ion's labelled
// line reads "M0" and the reagent's unlabelled remainder below it "[14N]". Every
// row the engine writes carries its ion formula; the M0's stands in for a row
// that recorded none, and the measurement's for a derived family whose rows name
// no ion.
const isoLabel = (iso) =>
  iso.isotope_formula
    ? formatIsotopeFormula(
        iso.isotope_formula,
        iso.ion_formula ?? m0.value?.ion_formula ?? measured.value?.ion_formula
      )
    : iso.isotope_label || '-'

// Theoretical (predicted) relative abundance of an isotopologue, as a fraction
// of the family's most abundant isotopologue - the way an isotope table gives
// it, so nothing reads above 100 %. A measured row (a ledger-served family)
// brings the prediction itself, already on that scale. A run's row recovers
// its prediction from the stored errors, relative to the M0,
//   theoretical_rel = observed_rel / (1 + abundance_error),  observed_rel = I / I(M0)
// and the family's largest value is then the reference: the M0 itself for most
// ions, a heavier isotopologue for a bromine- or chlorine-rich one, whose M0 is
// the lightest peak of the cluster and not the tallest.
const relativeToM0 = (iso) => {
  if (iso.relative_abundance != null) return iso.relative_abundance
  const base = m0.value?.sample_peak_intensity
  if (!base || base <= 0 || iso.sample_peak_intensity == null) return null
  const observed = iso.sample_peak_intensity / base
  const denom = 1 + (iso.abundance_error ?? 0)
  return denom > 0 ? observed / denom : null
}
const theoreticalRel = (iso) => {
  const rel = relativeToM0(iso)
  if (rel == null) return null
  const top = familyRelReference.value
  return top && top > 0 ? rel / top : rel
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

// The numbers an entry was scored on, whichever of the two ways it got them.
// A runner-up the run competed carries its own; a finder shortlist entry gets
// them from the on-demand measurement, which also names the adduct it found.
// Falls back to the entry's own fields so a run stored before this existed,
// and an imported one, read exactly as they did.
const altFit = (alt) => alt?.fit_score ?? alt?.scored?.fit_score ?? null
const altMzError = (alt) => alt?.mz_error_ppm ?? alt?.scored?.mz_error_ppm ?? null
const altAdduct = (alt) => alt?.scored?.ionization_mechanism ?? null

// Stats for a close alternative (runner-up), surfaced on hover. Entries the
// run scored carry fit + m/z error; the finder's shortlist entries read
// "measuring" until their measurement lands, then either show one or say why
// there is none.
const altTooltip = (alt, index) => {
  const fit = altFit(alt)
  const measuring = fit == null && !alt?.scored && scoring.value
  const kind = alternativeKind(alt)
  const lines = [
    ...(kind ? [kind.tooltip] : []),
    `fit: ${fit != null ? formatFit(fit) : measuring ? '— measuring' : '— not measured'}`
  ]
  const mzError = altMzError(alt)
  if (mzError != null) {
    lines.push(`m/z error: ${num.mzError.format(mzError)} ppm`)
  }
  lines.push(`plausibility: ${alt.plausibility != null ? formatFit(alt.plausibility) : '—'}`)
  // Named only when the measurement supplied it: an entry that arrived with an
  // adduct already shows it as its ion formula in the row above.
  if (altAdduct(alt)) lines.push(`adduct: ${altAdduct(alt)}`)
  if (alt.source) lines.push(`source: ${alt.source}`)
  const listing = listingOf(alt)
  if (listing) lines.push(listingTooltip(listing))
  // Why this one carries no usable "use this". Said here as well as on the
  // control because the row is where the pointer actually is: the control only
  // fades in on hover and is disabled, and a disabled button dispatches no
  // mouse events, so a tooltip bound to it alone would seldom be read.
  if (promoteBlocked(alt)) lines.push(noAdductHint(alt, index))
  return lines.join('\n')
}

// --- Manual curation ------------------------------------------------------
// Commit a runner-up as this peak's assignment. The row is edited in place and
// marked as human-made; the winner it replaces becomes the first close
// alternative, so the same control undoes the change - and the undo puts the
// replaced compound's isotopologues back with it, since they were
// unassigned only because the compound they belonged to was.
//
// Deliberately about THIS row, not the family M0 a verdict is redirected to: an
// index into `alternatives` only means anything against the list the card is
// showing.
const curating = ref(null) // index of the alternative being committed
const curateDenied = ref(false) // 403: not an editor on this sample
// A ledger derived from the batch peaks (run engine 'batch') has no rows of its
// own: curation edits a peak_assignment row, and there is none behind these.
// The server answers 409; the control is withheld rather than offered to fail.
const derivedRun = computed(() => app.data.peakAssignment.peak.run?.engine === 'batch')

// A derived row with a formula. The rows a run fills - arbitration confidence,
// P(correct) - are kept on the card for it, with what the ledger has or a
// placeholder that says why not, so the card reads the same for every sample.
const ledgerServed = computed(
  () => derivedRun.value && Boolean(focusedAssignment.value?.assigned_formula)
)
// The probability itself: in the detail's provenance on a run's row, on the row
// itself for a derived one (member_row carries it at the top level).
const pCorrect = computed(
  () => provenance.value?.p_correct ?? focusedAssignment.value?.p_correct ?? null
)

// --- On-demand evidence for a derived row -----------------------------------
// A row derived from the batch ledger carries its fit and its tier, but not what
// a run would have stored beside them: the m/z and abundance error of each
// isotopologue, the isotope labels, the plausibility, the evidence the tier was
// read off. The server measures the family's composition against this sample's
// peaks on request - through the M0, since the envelope is the compound's - and
// the numbers are hung onto the rows here. Session data, never written back,
// exactly like the alternative scores above.
const measuredKey = computed(
  () => (m0.value ?? focusedAssignment.value)?.peak_assignment_id ?? null
)
const measured = computed(() =>
  derivedRun.value ? app.data.peakAssignment.peak.evidenceOf(measuredKey.value) : null
)
const measuring = computed(
  () => derivedRun.value && app.data.peakAssignment.peak.evidencePending(measuredKey.value)
)
watch(
  [measuredKey, derivedRun],
  () => {
    if (!derivedRun.value) return
    const target = m0.value ?? focusedAssignment.value
    if (target?.assigned_formula) {
      app.data.peakAssignment.peak.loadEvidence(target).catch(() => {})
    }
  },
  { immediate: true }
)
// The measured isotopologues by the peak each paired to.
const measuredByPeak = computed(() => {
  const map = new Map()
  for (const iso of measured.value?.isotopologues ?? []) {
    if (iso.sample_peak_id != null) map.set(String(iso.sample_peak_id), iso)
  }
  return map
})
// A row with the measured numbers filled in where the row itself has none. The
// stored value always wins, so a run's own row reads exactly as it did.
const withMeasured = (row) => {
  const record = measured.value
  if (!row || !record) return row
  const iso = measuredByPeak.value.get(String(row.sample_peak_id))
  const isM0 = row.peak_assignment_id === record.peak_assignment_id
  return {
    ...row,
    mz_error_ppm: row.mz_error_ppm ?? (isM0 ? record.mz_error_ppm : iso?.mz_error_ppm) ?? null,
    abundance_error:
      row.abundance_error ?? (isM0 ? record.abundance_error : iso?.abundance_error) ?? null,
    isotope_label: row.isotope_label ?? iso?.isotope_label ?? null,
    isotope_formula: row.isotope_formula ?? iso?.isotope_formula ?? null,
    relative_abundance: iso?.relative_abundance ?? null,
    evidence: row.evidence ?? record.evidence ?? null
  }
}
const evidenceRow = computed(() => withMeasured(focusedAssignment.value))
const familyRows = computed(() => family.value.map(withMeasured))
// The family's largest predicted abundance relative to the M0 - what the
// abundance column is a fraction of (see theoreticalRel).
const familyRelReference = computed(() => {
  const values = familyRows.value.map(relativeToM0).filter((v) => v != null && v > 0)
  return values.length ? Math.max(...values) : null
})
const plausibility = computed(
  () => provenance.value?.plausibility ?? measured.value?.plausibility ?? null
)

// --- Why this tier ------------------------------------------------------------
// Every row a run commits carries what the tiering pass decided about it
// (`provenance.tier_reasons`): what took its top tier, or what it kept its tier
// on. The server writes each reason's sentence and the card shows it as
// written, with the rule named beside it. A row with none was judged by no such
// pass - a run from before it, an imported run, a row assigned by hand - and
// shows nothing rather than a placeholder.
//
// A derived row's provenance is its anchor's consensus record, and its tier is
// a vote across the batch that no rule judged, so it has nothing to show here.
const tierReasons = computed(() => (derivedRun.value ? [] : tierReasonsOf(provenance.value)))

// An isotopologue carries one reason of its own, that it follows its M0: every
// question the pass asks was asked of the M0's row, and an isotopologue goes
// down with it. That answer is what a reader opening the isotopologue wants, so
// the M0's reasons are shown beneath, marked as the M0's. The M0 is the owner
// the reason is about, resolved by id, and its detail is fetched like the
// focused row's own - the store caches it, so stepping around a family fetches
// it once.
const reasonsOwner = computed(() => {
  const own = focusedAssignment.value
  const owner = verifyTarget.value
  if (!own || !owner || owner.peak_assignment_id === own.peak_assignment_id) return null
  return tierReasons.value.some((reason) => reason.rule === 'inherited_from_owner') ? owner : null
})
watch(
  reasonsOwner,
  (owner) => {
    // Failures already toast via the http layer; the card keeps its own line.
    if (owner) app.data.peakAssignment.peak.loadDetail(owner).catch(() => {})
  },
  { immediate: true }
)
const ownerReasons = computed(() => {
  const owner = reasonsOwner.value
  if (!owner) return []
  const detail = app.data.peakAssignment.peak.detailOf(owner.peak_assignment_id)
  return tierReasonsOf(detail?.provenance ?? owner.provenance)
})

// --- Where the mass error sits -------------------------------------------------
// The ppm error is a distance with no scale. The run measures its own mass
// calibration over the rows it committed and corroborated, and records every
// committed row's distance from it in that calibration's widths (`mass_z`):
// the number its mass gate judged, and what "off calibration" among the
// reasons is about. Flattened onto the ledger row, so it shows before the
// detail lands; a derived row and an imported one have none.
const massZ = computed(() => {
  const z = focusedAssignment.value?.mass_z ?? provenance.value?.mass_z
  return typeof z === 'number' && Number.isFinite(z) ? z : null
})
// What the distance is measured in, recorded once on the run.
const massCalibration = computed(() => {
  const record = app.data.peakAssignment.peak.run?.config?.mass_calibration
  return record && typeof record === 'object' && !Array.isArray(record) ? record : null
})
// Past the distance the gate caps a row at: marked, since that is what a reader
// scanning the card needs to see. Whether the cap applied is the reasons' to
// say - an isotopologue that tracks the row lifts it.
const massZFar = computed(() => {
  const cap = massCalibration.value?.cap_z
  return massZ.value != null && typeof cap === 'number' && Math.abs(massZ.value) > cap
})
const zFormat = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: 'exceptZero'
})
const massZTooltip = computed(() => {
  if (massZ.value == null) return ''
  const lines = [
    "How far this row's mass error sits from the run's own mass calibration at " +
      "its m/z, counted in the calibration's widths."
  ]
  const calibration = massCalibration.value
  const width = calibration?.gate_sigma_ppm ?? calibration?.sigma_ppm
  if (typeof width === 'number') {
    const centre =
      calibration.centre === 'trend'
        ? 'a centre that follows m/z'
        : typeof calibration.mu_ppm === 'number'
          ? `a centre of ${num.mzError.format(calibration.mu_ppm)} ppm`
          : 'no centre'
    lines.push(`The run measured ${centre} and a width of ${num.mzError.format(width)} ppm.`)
  }
  const cap = calibration?.cap_z
  const floor = calibration?.floor_z
  if (typeof cap === 'number') {
    lines.push(
      `Beyond ${cap} widths a row nothing corroborates is held at candidate` +
        (typeof floor === 'number' ? `, beyond ${floor} below assignability.` : '.')
    )
  }
  return lines.join('\n')
})

// --- The other readings of this ion ---------------------------------------------
// Many ions split two ways between a neutral and an adduct - dimethylformamide
// with a proton is acrolein with ammonium - and no mass, envelope or fit tells
// the splits apart. The run keeps the readings it did not commit among the
// alternatives, flagged `same_ion`, and the nitrogen rule reads them. They are
// listed beside the reasons, which is where "ambiguous nitrogen" points.
const notationById = computed(() => {
  const map = new Map()
  for (const mechanism of app.data.ionization?.mechanism?.list ?? []) {
    map.set(mechanism.ionization_mechanism_id, mechanism.ionization_mechanism)
  }
  return map
})
const channelOf = (entry) =>
  entry?.ionization_mechanism_id != null
    ? (notationById.value.get(entry.ionization_mechanism_id) ?? null)
    : null
// A reading of the row's own neutral is the row's reading again, whatever
// channel it names - a run that searched a channel twice stored one - so it is
// left out however it is spelled.
const sameIonReadings = computed(() => {
  const own = neutralKey(focusedAssignment.value?.assigned_formula)
  return alternatives.value.filter(
    (alt) =>
      alt?.same_ion === true && alt.assigned_formula && neutralKey(alt.assigned_formula) !== own
  )
})
const SAME_ION_TOOLTIP =
  'The same ion read as another neutral through another adduct. Its mass and isotope ' +
  "pattern are this row's own, so the spectrum cannot choose between the two readings; " +
  'a second channel of the run can.'

// --- How the ion was made, and what a list calls it ----------------------------
// The mechanism is half of an assignment: the neutral and the ion formula imply
// it, but a reader should not have to take one from the other to see it.
const ionization = computed(() => channelOf(focusedAssignment.value))
const ionizationTooltip = computed(() => {
  const row = focusedAssignment.value
  if (!ionization.value) return ''
  const reaction =
    row?.assigned_formula && row?.ion_formula
      ? `\n${row.assigned_formula} ${ionization.value} gives ${row.ion_formula}`
      : ''
  return `The ionization mechanism: how the neutral became the ion seen at this peak.${reaction}`
})
// What a reference list calls the committed formula: the identities the run
// matched (in the row's provenance), else the compounds a list holds for it.
const listedAs = computed(() =>
  listingOf(focusedDetail.value, provenance.value?.reference_identities)
)

// Three kinds of close alternative are not simply runners-up, and each says
// which it is: a reading of the same ion, the reading a neighbour's isotope line
// took this peak from, and a loaded list's compound that the formula search
// took the peak from. The last two are also what "use this" puts back.
const ALTERNATIVE_KINDS = [
  { key: 'same_ion', label: 'same ion', tooltip: SAME_ION_TOOLTIP },
  {
    key: 'displaced_by_claim',
    label: 'earlier reading',
    tooltip:
      "What the run first read this peak as, before a neighbour's isotope line claimed " +
      'it. Using it puts that reading back.'
  },
  {
    key: 'displaced_by_rival',
    label: 'list compound',
    tooltip:
      "The loaded list's compound for this peak, which a formula from the search took: " +
      "its evidence was more than twice the list's and it explains the compound's " +
      'isotope lines. Using it puts the list compound back.'
  }
]
const alternativeKind = (alt) => ALTERNATIVE_KINDS.find(({ key }) => alt?.[key] === true) ?? null

// A candidate can only be committed when it names both halves of an
// assignment: the formula and the adduct it was found under. The server
// refuses the rest with a 422, for the reason a set_assignment call has always
// had to name a mechanism - a verification's identity is peak + formula +
// mechanism, so a row assigned without an adduct could never carry a verdict.
// `ionization_mechanism_id` is what the engine records on a runner-up now;
// `target_ion_id` is how one written before that key still resolves to one;
// and `scored` is the adduct the on-demand measurement found for a shortlist
// entry that reached the row with none.
const canPromote = (alt) =>
  Boolean(alt?.assigned_formula) &&
  Boolean(
    alt?.ionization_mechanism_id || alt?.target_ion_id || alt?.scored?.ionization_mechanism_id
  )

// Entries that fail that test are the untargeted finder's shortlist, either
// still being measured or measured and placed on this peak by no adduct at
// all. They get a disabled control with a reason rather than no control,
// because the formula is not unassignable in principle - it just has nothing
// to be assigned under yet.
const promoteBlocked = (alt) => Boolean(alt?.assigned_formula) && !canPromote(alt)

// Said on the row as well as on the control, because a disabled button
// dispatches no mouse events and its own tooltip would seldom be read.
//
// Re-search is the way past every one of these states: it searches the peak's
// composition against the sample's adducts from scratch rather than measuring
// the formulas this shortlist happens to name, so it can find one this list
// never held.
const RESEARCH_HINT = 'Re-search the peak to look wider than this shortlist.'
const NO_ADDUCT_HINT =
  'Not assignable to this peak. A formula needs an adduct to go with it, and this ' +
  `one has none. ${RESEARCH_HINT}`
const SCORING_HINT = 'Measuring this formula against the peak. One moment.'

// One blocked entry is not a candidate the finder listed: the winner an
// override displaced, which the server archives and pushes back to the head of
// this list so the same control undoes the change. That winner can name no
// adduct of its own - the untargeted stage writes one when the finder echoes a
// notation the mechanism map does not hold, and an imported run reaches it
// trivially, since an imported row may only ever have carried a formula - and
// then the undo is refused by the same 422.
//
// Re-search is worth naming, but not as if it were the undo: it writes a NEW
// assignment, and the isotopologues this override unassigned are put back by
// compound AND adduct, so they stay unassigned.
const NO_ADDUCT_UNDO_HINT =
  'Cannot be undone here. The assignment this replaced named no adduct, and one is ' +
  'required to put it back. Re-searching the peak assigns the formula again under a ' +
  'real adduct, but that is a new assignment: the isotopologues this override cleared ' +
  'stay cleared.'

// Whether an entry is that archived winner - the one "use this" would undo.
// Position and formula together: the head is where the server puts it, and the
// formula check keeps an ordinary shortlist entry from wearing the undo wording
// on a row whose override recorded no previous winner at all (overriding an
// `unassigned` placeholder displaces nothing).
const isUndoEntry = (alt, index) =>
  index === 0 &&
  Boolean(manualOverride.value?.previous_formula) &&
  alt?.assigned_formula === manualOverride.value.previous_formula

// Why this entry has no usable "use this", in whichever of the four states it
// is actually in. The server's own reason is preferred where there is one: it
// can tell "no adduct this sample uses reaches the peak" from "the sample has
// no adducts recorded" and from "this formula will not make an ion at all",
// which one fixed sentence cannot.
const noAdductHint = (alt, index) => {
  if (isUndoEntry(alt, index)) return NO_ADDUCT_UNDO_HINT
  if (alt?.scored?.blocked_reason) return `${alt.scored.blocked_reason} ${RESEARCH_HINT}`
  if (scoring.value) return SCORING_HINT
  return NO_ADDUCT_HINT
}

// What committing this entry actually is.
//
// An entry the run itself scored is promoted: its numbers and its adduct are
// on the stored row, the server reads them from there, and nothing about the
// request is the client's word.
//
// An entry the on-demand measurement scored is a `set_assignment` instead -
// the same action the re-search hand button uses. Its numbers are not on the
// stored row and are deliberately never written there: a run is the record of
// what the engine did, and this measurement is not something it did. Sending
// them as a declaration is the honest shape, and it is the shape the server
// already has a validated action for - one that re-tiers under the run's own
// bands and records in provenance that the numbers came from a composition
// search rather than from the run's arbitration.
const promoteBody = (alt, index) =>
  alt?.ionization_mechanism_id || alt?.target_ion_id
    ? {
        action: 'promote_alternative',
        alternative_index: index,
        // Checked server-side against the list as it stands now, so a click on
        // a card another curator has already changed underneath is refused
        // rather than committing whichever candidate now holds that position.
        expected_formula: alt.assigned_formula
      }
    : {
        action: 'set_assignment',
        assigned_formula: alt.assigned_formula,
        ionization_mechanism_id: alt.scored.ionization_mechanism_id,
        ion_formula: alt.scored.ion_formula ?? null,
        // Always M0: the shortlist proposes a composition for this peak's own
        // mass, and the measurement only accepts an adduct whose monoisotopic
        // peak lands here.
        isotope_label: alt.scored.isotope_label ?? 'M0',
        fit_score: alt.scored.fit_score ?? null,
        mz_error_ppm: alt.scored.mz_error_ppm ?? null,
        abundance_error: alt.scored.abundance_error ?? null
      }

async function promoteAlternative(alt, index) {
  if (!canPromote(alt) || curating.value !== null) return
  if (derivedRun.value && alt.candidate == null) return
  curating.value = index
  try {
    if (derivedRun.value) {
      // A derived row has no row of its own to edit: "use this" here pins the
      // identity on the batch peak and measures it in every sample of the
      // batch. The guard is the consensus the card shows, not this member's
      // own formula, which is what the server compares against.
      await curation.curate({
        batch_peak_id: focusedAssignment.value.batch_peak_id,
        candidate: alt.candidate,
        expected_formula: provenance.value?.batch_peak?.consensus_formula ?? null
      })
    } else {
      await app.data.peakAssignment.peak.curate(
        focusedAssignment.value.peak_assignment_id,
        promoteBody(alt, index)
      )
    }
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

// A derived row's provenance is the anchor's, so `manual` there is a
// batch-level pin: the species chosen by hand for the whole batch. Its own
// note and its own undo - release, which puts the re-measured samples back
// and lets the batch decide again.
const batchOverride = computed(() =>
  derivedRun.value && provenance.value?.manual?.action === 'promote_identity'
    ? provenance.value.manual
    : null
)
const releasing = ref(false)
async function releaseOverride() {
  if (releasing.value || !focusedAssignment.value?.batch_peak_id) return
  releasing.value = true
  try {
    await curation.release({ batch_peak_id: focusedAssignment.value.batch_peak_id })
  } catch (error) {
    if (error?.response?.status === 403) curateDenied.value = true
  } finally {
    releasing.value = false
  }
}

// Whether the assignment this override replaced could be committed again at
// all. The archive keeps that winner in the alternatives shape, so the same
// rule decides it as decides any other candidate: no adduct, no assignment. A
// winner really can carry none (see NO_ADDUCT_UNDO_HINT), and then the note's
// "use this on it to undo" points at a control this very card disables. With
// nothing archived - an override written before the archive existed, or the
// detail not landed yet - the common case is the honest guess.
const previousRestorable = computed(() => {
  const previous = manualOverride.value?.previous
  return !previous || canPromote(previous)
})

// Two different things wear source 'manual'. A person assigning a peak is one;
// the other is an isotopologue the server unassigned because its M0 was reassigned
// under it, which is marked 'manual' so the ledger's source filter shows the
// whole footprint of an override. That row was stripped, not chosen, so the
// override note would read as a claim nobody made.
//
// The recorded action decides it once the detail lands. Until then the row's
// own formula does: curating a peak always puts a formula on it, so a manual
// row with none was demoted.
// The action the server records on a row it demoted. Rows demoted by earlier
// builds carry 'demote_satellite' - a name retired because "satellite" means a
// signal artifact here - and still have to read as demoted.
const DEMOTE_ACTIONS = new Set(['demote_isotopologue', 'demote_satellite'])
const manualDemoted = computed(() => {
  const action = manualOverride.value?.action
  if (action) return DEMOTE_ACTIONS.has(action)
  return !focusedAssignment.value?.assigned_formula
})

// The compound this peak was an isotopologue of, which is the compound to put
// back on the M0's own peak to restore it. An isotopologue carries its M0's formula
// verbatim, so the two keys agree on anything the engine wrote; the fallback is
// for an imported run that recorded only one of them.
const demotedOwnerFormula = computed(
  () =>
    manualOverride.value?.previous_owner_formula ?? manualOverride.value?.previous_formula ?? null
)

// How many isotopologues undoing THIS override would put back. They were the same
// compound as their M0 seen through a heavy atom, so committing the replaced
// compound again restores them along with it - the part of "use this to undo" a
// person would otherwise be surprised by.
//
// Counted against the compound the undo would commit, not over the whole
// archive, because a row curated twice carries the first override's demotions
// forward: those isotopologues come back with the compound they were taken under,
// which is no longer the one the first alternative holds. Matched on the same
// key the server restores by (formula + mechanism), so an entry this cannot
// account for is left out of the promise rather than added to it.
const demotedCount = computed(() => {
  const manual = manualOverride.value
  const formula = manual?.previous_formula
  if (!formula) return 0
  const mechanism = manual?.previous?.ionization_mechanism_id ?? null
  return (manual.demoted ?? []).filter(
    (entry) =>
      entry?.owner_formula === formula &&
      (entry?.owner_ionization_mechanism_id ?? null) === mechanism
  ).length
})
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
        <!-- The mechanism beside the neutral, as a chemist writes the pair: it
             is half of the assignment, and the ion formula under it only
             implies it. -->
        <div class="insp-title">
          <span class="insp-formula">{{ focusedAssignment.assigned_formula || 'Unassigned' }}</span>
          <span
            v-if="ionization"
            class="insp-ionization"
            data-testid="ionization"
            v-tooltip.top="ionizationTooltip"
            >{{ ionization }}</span
          >
        </div>
        <!-- Both chips in one box, because the head is `space-between`: a third
             child of it would put this server's tier in the middle of the row
             and the engine's at the far edge, which reads as two unrelated
             marks rather than the pair they are. The pairing is the whole
             point - the tooltips say "the chip beside it". -->
        <div class="insp-tiers">
          <BaseTierTag
            :tier="focusedAssignment.tier"
            :evidence="focusedAssignment.evidence"
            :role="focusedAssignment.role"
            :source="focusedAssignment.source"
            v-help.right="{
              title: 'Confidence Tiers',
              helpKey: 'assignment-tiers',
              doc: app.ui.help.docUrl('how-it-works/peak-assignment/#confidence-tiers')
            }"
          />
          <!-- The producing engine's own verdict, spelled out rather than left to
               the marker on the chip above: this is the detail view, and the
               disagreement is the reason to open an imported row at all. It is
               given no evidence - the evidence is this server's product and did
               not produce this tier. -->
          <BaseTierTag
            v-if="focusedAssignment.engine_tier"
            :tier="focusedAssignment.engine_tier"
            :tooltip="engineTierTooltip"
          />
        </div>
      </div>
      <!-- With no formula the headline is the word "Unassigned" and the
           evidence grid below is empty, so the peak itself has to name the
           card. -->
      <div class="insp-sub" v-if="!focusedAssignment.assigned_formula">{{ peakSummary }}</div>
      <!-- The one place the card names the row's isotope, beside the ion it is
           a line of; the isotopologue table below marks the same row. Read off
           the measured row, which labels a derived row's isotope where the row
           itself names none. -->
      <div
        class="insp-sub"
        v-if="
          focusedAssignment.ion_formula || evidenceRow.isotope_label || focusedAssignment.source
        "
      >
        <span v-if="focusedAssignment.ion_formula">{{ focusedAssignment.ion_formula }}</span>
        <span v-if="evidenceRow.isotope_label"> &middot; {{ evidenceRow.isotope_label }}</span>
        <span v-if="focusedAssignment.source" class="src">
          &middot; {{ focusedAssignment.source }}</span
        >
      </div>
      <!-- A list's name for the formula is the first thing a reader checks the
           assignment against, so it is named outright, above the evidence. -->
      <div v-if="listedAs" class="identity" data-testid="identity">
        <div
          v-if="listedAs"
          class="ev listed"
          data-testid="listed-as"
          v-tooltip.top="listingTooltip(listedAs)"
        >
          <span class="k"
            >reference list<span v-if="!listedAs.matched" class="potential">
              &middot; potential</span
            ></span
          >
          <span class="v"><span class="pi ph ph-flask" /> {{ listingName(listedAs) }}</span>
          <span v-if="listingSource(listedAs)" class="list-source">{{
            listingSource(listedAs)
          }}</span>
        </div>
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
        <!-- A derived row's numbers arrive a moment after the row: the family
             is measured against this sample's peaks on request. -->
        <div v-if="measuring" class="ev measuring">
          <span class="k">measuring</span>
          <span class="v">the isotope pattern against this sample...</span>
        </div>
        <div v-else-if="measured?.blocked_reason" class="ev measuring">
          <span class="k">not measured</span>
          <span class="v">{{ measured.blocked_reason }}</span>
        </div>
        <!-- Measured, but the family's main peak paired with none of the
             predicted isotopologues: the pattern's numbers stand, this row
             says which prediction came nearest and where it went. -->
        <div v-else-if="measured?.main_peak_note" class="ev measuring">
          <span class="k">main peak</span>
          <span class="v">{{ measured.main_peak_note }}</span>
        </div>
        <div class="ev" v-if="evidenceRow.mz_error_ppm != null">
          <span class="k">m/z error</span>
          <span class="v">{{ num.mzError.format(evidenceRow.mz_error_ppm) }} ppm</span>
        </div>
        <div class="ev" v-if="massZ != null" data-testid="mass-z">
          <span class="k" v-tooltip.top="massZTooltip">mass z</span>
          <span class="v" :class="{ far: massZFar }">{{ zFormat.format(massZ) }}</span>
        </div>
        <div class="ev" v-if="evidenceRow.abundance_error != null">
          <span class="k">abund. error</span>
          <span class="v">{{
            num.relativeAbundanceError.format(evidenceRow.abundance_error)
          }}</span>
        </div>
        <div class="ev" v-if="plausibility != null">
          <span class="k" v-tooltip.top="'Chemical plausibility (Seven Golden Rules)'"
            >plausibility</span
          >
          <span class="v">{{ formatFit(plausibility) }}</span>
        </div>
        <!-- The product of the two above, and the number this row's tier was
             read off. Shown here beside its factors rather than only on the
             chip: when a tier looks surprising, "fit 95%, plausibility 40%" is
             the answer, and the inspector is where a reader goes to find it. -->
        <div class="ev" v-if="evidenceRow.evidence != null">
          <span
            class="k"
            v-tooltip.top="'Evidence (fit x plausibility) - what the tier is banded on'"
            >evidence</span
          >
          <span class="v">{{ formatFit(evidenceRow.evidence) }}</span>
        </div>
        <!-- Arbitration confidence and P(correct) are a run's numbers. A row
             served from the batch ledger gets both rows all the same, so the
             card reads the same for every sample: the P(correct) recorded when
             the sample was folded in, or a dash saying why there is none, and a
             dash for the confidence, which the ledger has no contest to compute. -->
        <div class="ev" v-if="provenance?.confidence != null || ledgerServed">
          <span
            class="k"
            v-tooltip.top="'Arbitration confidence: winner share of fit x plausibility'"
            >confidence</span
          >
          <span class="v" v-if="provenance?.confidence != null"
            >{{ formatFit(provenance.confidence)
            }}<span
              v-if="provenance.is_tie"
              class="tie-flag"
              v-tooltip.top="'Runner-up too close to call'"
              >&nbsp;tie</span
            ></span
          >
          <span class="v uncal" v-else v-tooltip.top="LEDGER_CONFIDENCE_TOOLTIP">&mdash;</span>
        </div>
        <div class="ev" v-if="(provenance && provenance.calibrated !== undefined) || ledgerServed">
          <span class="k" v-tooltip.top="P_CORRECT_TOOLTIP">P(correct)</span>
          <span
            class="v"
            v-if="pCorrect != null"
            v-tooltip.top="ledgerServed ? LEDGER_P_CORRECT_TOOLTIP : ''"
          >
            {{ formatFit(pCorrect)
            }}<span
              v-if="provenance?.calibration?.provisional"
              class="prov-flag"
              v-tooltip.top="'Provisional calibration curve - directionally right, not hardened'"
              >&nbsp;prov.</span
            ></span
          >
          <span
            v-else-if="ledgerServed"
            class="v uncal"
            v-tooltip.top="uncalibratedReason(focusedAssignment, { fromLedger: true })"
            >&mdash;</span
          >
          <span class="v uncal" v-else v-tooltip.top="'No calibration curve for this instrument'"
            >uncalibrated</span
          >
        </div>
      </div>
      <div
        v-if="corroboration && corroboration.n > 1"
        class="corroboration"
        :class="{ inherited: corroboration.inherited }"
        v-tooltip.top="corroborationTooltip"
      >
        <span class="pi ph ph-link-simple" />
        {{ corroborationLabel }}
      </div>
      <!-- Why the row holds its tier, in the run's own words. The rule is named
           first so the list can be scanned; the sentence under it is the
           server's. A reason that holds the tier down wears the down arrow, and
           the tier chip above is what says where the row ended up. -->
      <div
        v-if="tierReasons.length"
        class="tier-reasons"
        v-help.right="{
          title: 'Why this tier',
          helpKey: 'assignment-tiers',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#why-a-row-holds-its-tier')
        }"
      >
        <div class="alts-label">Why this tier</div>
        <ul class="reasons">
          <li
            v-for="(reason, i) in tierReasons"
            :key="`own-${i}`"
            :class="['reason', { caps: holdsTierDown(reason) }]"
            v-tooltip.left="reasonTooltip(reason, focusedAssignment.tier)"
          >
            <span :class="['pi', 'ph', reasonIcon(reason), 'reason-icon']" />
            <span class="reason-body">
              <span class="reason-rule">{{ reason.label }}</span>
              <span class="reason-detail">{{ reason.detail }}</span>
            </span>
          </li>
          <li
            v-for="(reason, i) in ownerReasons"
            :key="`m0-${i}`"
            :class="['reason', 'inherited', { caps: holdsTierDown(reason) }]"
            v-tooltip.left="reasonTooltip(reason, reasonsOwner.tier, { viaM0: true })"
          >
            <span :class="['pi', 'ph', reasonIcon(reason), 'reason-icon']" />
            <span class="reason-body">
              <span class="reason-rule">{{ reason.label }}<span class="via"> via M0</span></span>
              <span class="reason-detail">{{ reason.detail }}</span>
            </span>
          </li>
        </ul>
      </div>
      <!-- The splits of this ion the run did not commit, under the reasons that
           read them. Shown whether or not a rule capped the row: a second
           channel settles the count, and the reader should see what it settled. -->
      <div
        v-if="sameIonReadings.length"
        class="same-ion"
        data-testid="same-ion"
        v-help.right="{
          title: 'Why this tier',
          helpKey: 'assignment-tiers',
          doc: app.ui.help.docUrl('how-it-works/peak-assignment/#why-a-row-holds-its-tier')
        }"
      >
        <div class="alts-label">Same ion, read another way</div>
        <ul class="readings">
          <li
            v-for="(reading, i) in sameIonReadings"
            :key="`same-${i}`"
            class="reading"
            v-tooltip.left="SAME_ION_TOOLTIP"
          >
            <span class="pi ph ph-arrows-left-right reading-icon" />
            <span class="reading-formula">{{ reading.assigned_formula }}</span>
            <span v-if="channelOf(reading)" class="reading-channel">
              through {{ channelOf(reading) }}
            </span>
            <span
              v-if="listingOf(reading)"
              class="reading-listed"
              v-tooltip.left="listingTooltip(listingOf(reading))"
            >
              <span class="pi ph ph-flask" /> {{ listingName(listingOf(reading)) }}
            </span>
          </li>
        </ul>
      </div>
      <!-- For a lone M0 as much as for a full pattern: this table is where the
           focused peak's m/z is read, and it should be read in the same place
           whether or not the pattern has more peaks. -->
      <div
        v-if="family.length"
        class="isotopologues"
        v-help.right="{
          message: `
              <h1>Isotopologues</h1>
              <p>
              The isotope pattern behind this assignment: the main peak (M0)
              and its isotopologues (M+1, M+2 ...), each with its m/z error and its
              estimated relative abundance (<b>abu.</b>, as a fraction of the most
              abundant isotopologue). Labels count from the monoisotopic peak, so
              a bromine- or chlorine-rich ion reads M0, M+2, M+4 with M0 the
              lightest peak rather than the tallest.
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
          ><span
            v-tooltip.top="
              'Estimated relative abundance (fraction of the most abundant isotopologue)'
            "
            >abu.</span
          >
        </div>
        <div class="iso-rows">
          <div
            v-for="iso in familyRows"
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

      <!-- A row the server stripped: it was never anyone's choice, so it says
           what happened to it and how to get it back, not what was picked. The
           eraser rather than the hand for the same reason the tier chip uses
           one (BaseTierTag.vue): the hand claims a person chose this row, which
           is the one thing that did not happen here. -->
      <div v-if="focusedAssignment.source === 'manual' && manualDemoted" class="manual-note">
        <span class="pi ph ph-eraser demoted-icon" />
        <span>
          Unassigned by hand<template v-if="demotedOwnerFormula"
            >: this peak was an isotopologue of {{ demotedOwnerFormula }} - the same compound seen
            through a heavy atom - and was cleared when that assignment was replaced by hand on the
            compound's own peak. Assigning {{ demotedOwnerFormula }} there again restores this
            row</template
          ><template v-else>
            when the compound this peak was an isotopologue of was replaced on the compound's own
            peak</template
          >. The next assignment run for this sample recomputes the ledger and supersedes the
          change.
        </span>
      </div>
      <div v-else-if="focusedAssignment.source === 'manual'" class="manual-note">
        <span class="pi ph ph-hand-pointing manual-icon" />
        <span>
          Assigned by hand<template v-if="manualOverride?.previous_formula">
            in place of {{ manualOverride.previous_formula
            }}<template v-if="previousRestorable"
              >, which is now the first close alternative - "use this" on it to undo<template
                v-if="demotedCount"
                >, which also puts back the {{ demotedCount }} isotopologue{{
                  demotedCount === 1 ? '' : 's'
                }}
                unassigned with it, except any of them assigned by hand since</template
              ></template
            ><template v-else
              >, which named no adduct itself and so cannot be put back by hand<template
                v-if="demotedCount"
                >, and the {{ demotedCount }} isotopologue{{
                  demotedCount === 1 ? '' : 's'
                }}
                unassigned with it {{ demotedCount === 1 ? 'stays' : 'stay' }} unassigned</template
              >
              - re-search this peak to assign it again under a real adduct</template
            ></template
          >. The next assignment run for this sample recomputes the ledger and supersedes the
          change; record a verification to keep the judgement.
        </span>
      </div>
      <div v-if="batchOverride" class="manual-note">
        <span class="pi ph ph-hand-pointing manual-icon" />
        <span>
          Curated by hand for the whole batch<template
            v-if="batchOverride.previous?.consensus_formula"
          >
            in place of {{ batchOverride.previous.consensus_formula }}</template
          >: every sample in the batch reads this identity where it could be measured.
          <Button
            label="release"
            size="small"
            text
            severity="secondary"
            class="release-link"
            :disabled="releasing || curateDenied"
            :loading="releasing"
            v-tooltip.top="
              'Let the batch decide again: the samples measured for this identity go back to what they read before'
            "
            @click="releaseOverride"
          />
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
        <!-- Borrowed from the batch level: the dashed pill is this pane's grammar
             for evidence read off another row, as on the inherited corroboration. -->
        <div
          v-if="anchorVerdict && !verification"
          class="anchor-verdict"
          v-tooltip.top="anchorVerdictTooltip"
        >
          <span :class="['pi', VERDICT_META[anchorVerdict.verdict].icon]" />
          <span
            >{{ anchorVerdictLabel }} at batch level &mdash; a verdict here records a per-sample
            exception</span
          >
        </div>
        <div v-if="verification && !editing" class="verify-current">
          <BaseVerdictBadge :record="verification" :conflict="anchorConflict" />
          <Button
            v-if="!denied"
            size="small"
            text
            severity="secondary"
            icon="pi ph ph-pencil-simple"
            v-tooltip.top="'Change the verdict'"
            @click="startEdit"
          />
        </div>
        <div v-else-if="showVerifyForm" class="verify-buttons">
          <Button
            label="Confirm"
            icon="pi ph ph-check-circle"
            size="small"
            severity="success"
            class="verdict-button"
            aria-haspopup="dialog"
            :disabled="submitting"
            :loading="submitting && pendingVerdict === 'confirmed'"
            @click="openVerdict('confirmed', $event)"
          />
          <Button
            label="Reject"
            icon="pi ph ph-x-circle"
            size="small"
            severity="danger"
            class="verdict-button"
            aria-haspopup="dialog"
            :disabled="submitting"
            :loading="submitting && pendingVerdict === 'rejected'"
            @click="openVerdict('rejected', $event)"
          />
          <Button
            label="Unsure"
            icon="pi ph ph-question"
            size="small"
            severity="secondary"
            class="verdict-button"
            aria-haspopup="dialog"
            :disabled="submitting"
            :loading="submitting && pendingVerdict === 'unsure'"
            @click="openVerdict('unsure', $event)"
          />
          <Button
            v-if="editing"
            label="Cancel"
            size="small"
            text
            severity="secondary"
            v-tooltip.top="'Keep the verdict as it is'"
            @click="editing = false"
          />
        </div>
        <div v-else-if="denied" class="verify-denied">
          <span class="pi ph ph-lock-simple" /> Editor access is required to verify.
        </div>
        <!-- No help card in here: the card above covers it, and one inside a
             popover re-registers on every open. Radio buttons rather than a
             Select, which would swallow the Escape that closes the dialog. -->
        <Popover ref="verdictDialog" :aria-label="`${dialogMeta.verb} the assignment`">
          <div class="verdict-dialog" data-testid="verdict-dialog">
            <div class="dialog-claim">
              {{ dialogMeta.verb }}
              <span class="dialog-formula">{{ verifyTarget?.assigned_formula }}</span>
            </div>
            <div
              v-if="confirming"
              class="dialog-levels"
              role="radiogroup"
              :aria-labelledby="`${dialogId}-levels`"
            >
              <span :id="`${dialogId}-levels`" class="alts-label">Evidence level</span>
              <div v-for="level in EVIDENCE_LEVELS" :key="level.value" class="dialog-level">
                <RadioButton
                  v-model="evidenceLevel"
                  :value="level.value"
                  :name="`${dialogId}-level`"
                  :inputId="`${dialogId}-${level.value}`"
                  :pt="{ input: { autofocus: level.value === autofocusLevel } }"
                />
                <label :for="`${dialogId}-${level.value}`">{{ level.label }}</label>
              </div>
            </div>
            <InputText
              v-model="note"
              placeholder="Note (optional)"
              aria-label="Note"
              size="small"
              fluid
              :autofocus="!confirming"
              @keydown.enter="submitDialog"
            />
            <div class="dialog-actions">
              <Button
                label="Cancel"
                size="small"
                text
                severity="secondary"
                @click="verdictDialog?.hide()"
              />
              <Button
                :label="dialogMeta.label"
                :icon="dialogMeta.icon"
                size="small"
                :severity="dialogMeta.severity"
                data-testid="verdict-submit"
                :disabled="submitting || (confirming && !evidenceLevel)"
                :loading="submitting"
                @click="submitDialog"
              />
            </div>
          </div>
        </Popover>
      </div>
      <div
        v-if="alternatives.length"
        class="alts"
        v-help.right="{
          title: 'Close Alternatives',
          helpKey: 'assignment-curation',
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
            v-tooltip.left="altTooltip(alt, i)"
          >
            <span class="f"
              >{{ alt.assigned_formula || alt.ion_formula || '?'
              }}<span v-if="alternativeKind(alt)" class="alt-kind">{{
                alternativeKind(alt).label
              }}</span
              ><span v-if="listingOf(alt)" class="alt-listed"
                ><span class="pi ph ph-flask" /> {{ listingName(listingOf(alt)) }}</span
              ></span
            >
            <span class="s">
              <span v-if="altFit(alt) != null"
                >fit {{ formatFit(altFit(alt))
                }}<span v-if="altMzError(alt) != null">
                  &middot; {{ num.mzError.format(altMzError(alt)) }} ppm</span
                ></span
              >
              <span v-else-if="scoring && !alt.scored" class="scoring">measuring&hellip;</span>
              <span v-else-if="alt.plausibility != null"
                >plaus {{ formatFit(alt.plausibility) }}</span
              >
              <span v-else class="no-stats"><span class="pi ph ph-info" /></span>
            </span>
            <Button
              v-if="(canPromote(alt) || promoteBlocked(alt)) && !curateDenied"
              :class="['alt-use', { busy: curating === i, blocked: promoteBlocked(alt) }]"
              label="use this"
              size="small"
              text
              severity="secondary"
              icon="pi ph ph-hand-pointing"
              :disabled="curating !== null || promoteBlocked(alt)"
              :loading="curating === i"
              v-tooltip.top="promoteBlocked(alt) ? noAdductHint(alt, i) : ''"
              @click="promoteAlternative(alt, i)"
            />
          </div>
        </div>
        <div v-if="derivedRun && !curateDenied" class="verify-denied">
          <span class="pi ph ph-info" /> Derived from the batch ledger: "use this" pins the identity
          on the batch peak for the whole batch and measures it in every sample.
        </div>
        <div v-if="curateDenied" class="verify-denied">
          <span class="pi ph ph-lock-simple" /> Editor access is required to change an assignment.
        </div>
      </div>
      <div class="insp-actions">
        <Button
          :label="showSearch ? 'Hide search' : 'Find more'"
          size="small"
          text
          :severity="showSearch ? 'primary' : 'secondary'"
          icon="pi ph ph-magnifying-glass"
          v-tooltip.top="'Search compositions for this peak in the pane under the spectrum'"
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
/* The formula and its ionization on one baseline; a long pair wraps before it
   pushes the tier chips off the row. */
.insp-title {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 0 0.45rem;
  min-width: 0;
}
.insp-formula {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 1.35rem;
  font-weight: 700;
  overflow-wrap: anywhere;
}
/* Second to the neutral it ionized: the same type, lighter and smaller. */
.insp-ionization {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 1.05rem;
  font-weight: 600;
  opacity: 0.6;
  white-space: nowrap;
  cursor: default;
}
/* This server's tier and the producing engine's, kept adjacent at the right
   edge instead of being spread apart by the head's `space-between`. */
.insp-tiers {
  display: flex;
  align-items: center;
  gap: 0.4rem;
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
/* The measurement is in flight. Dimmed and italic rather than a spinner: it is
   one short line in a list of numbers, and a spinner per row would read as the
   list itself loading. */
.alt .scoring {
  opacity: 0.6;
  font-style: italic;
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
/* The control that is there only to say why it cannot be used. It has to show
   while the row is hovered - that is when its reason gets read - but must not
   look clickable, and the hover rule above outranks the theme's own dimming of
   a disabled button. */
.alt:hover .alt-use.blocked {
  opacity: 0.45;
}
.manual-note {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  font-size: 0.78rem;
  line-height: 1.35;
  opacity: 0.75;
}
.manual-note .release-link {
  padding: 0 0.3rem;
  font-size: inherit;
  vertical-align: baseline;
}
.manual-note > .pi {
  margin-top: 0.1rem;
}
/* "A person chose this" reads at full strength, as the same mark does on the
   tier chip. */
.manual-note > .manual-icon {
  color: var(--p-primary-color, currentColor);
}
/* The eraser does not. A demoted row is the consequence of a decision taken on
   another peak, not a decision about this one, so it must not be coloured like
   a choice - BaseTierTag.vue makes the same call for the same rows, and the two
   surfaces describing one row have to agree about how loudly they say it. */
.manual-note > .demoted-icon {
  opacity: 0.75;
}
.insp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

/* Verification (labelling) capture. Confirm / Reject / Unsure share equal width
   -> equal prominence (reject is a first-class negative label, not an
   afterthought); Cancel, when changing a verdict, takes only its own. */
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
.anchor-verdict {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  align-self: flex-start;
  font-size: 0.78rem;
  padding: 0.12rem 0.55rem;
  border-radius: 100px;
  color: var(--state-info);
  background: color-mix(in srgb, var(--state-info) 12%, transparent);
  border: 1px dashed color-mix(in srgb, var(--state-info) 45%, transparent);
  cursor: default;
}
.anchor-verdict .pi {
  font-size: 0.8rem;
}
.verify-buttons {
  display: flex;
  gap: 0.4rem;
}
.verify-buttons > .verdict-button {
  flex: 1;
}
/* The verdict dialog: the claim, a confirmation's evidence levels strongest
   first, a note. */
.verdict-dialog {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  width: 17rem;
  max-width: 85vw;
}
.dialog-claim {
  font-size: 0.85rem;
}
.dialog-formula {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.dialog-levels {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.dialog-level {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
}
.dialog-level label {
  cursor: pointer;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.4rem;
}
.verify-denied {
  display: inline-flex;
  align-items: center;
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
  color: var(--state-warning);
  font-size: 0.68rem;
  margin-right: 0.2rem;
}
.tie-flag {
  color: var(--state-warning);
  font-weight: 600;
  font-size: 0.72rem;
}
.prov-flag {
  color: var(--state-warning);
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
  color: var(--state-info);
  background: color-mix(in srgb, var(--state-info) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--state-info) 32%, transparent);
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
/* Why this tier: one line per reason, the rule over the server's sentence. */
.tier-reasons {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.reasons {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.reason {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
  font-size: 0.8rem;
  line-height: 1.35;
  cursor: default;
}
.reason-icon {
  margin-top: 0.15rem;
  font-size: 0.8rem;
  opacity: 0.55;
}
/* The one mark that says a reason holds the tier down, in the colour the
   candidate chip wears. */
.reason.caps .reason-icon {
  color: var(--state-warning);
  opacity: 1;
}
.reason-body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.reason-rule {
  font-weight: 600;
}
.reason-detail {
  opacity: 0.75;
  overflow-wrap: anywhere;
}
/* Read off the M0 rather than recorded on this peak: dashed, as the pane marks
   every piece of evidence it borrows from another row. */
.reason.inherited {
  padding-left: 0.5rem;
  border-left: 1px dashed var(--p-content-border-color, #e3e6ec);
}
.reason .via {
  font-weight: 400;
  opacity: 0.6;
}
.ev .v.uncal {
  opacity: 0.55;
  font-style: italic;
}
/* Past the distance the mass gate caps at, in the colour the candidate chip
   wears: the reasons below say whether the cap applied. */
.ev .v.far {
  color: var(--state-warning);
}
/* The same ion's other readings: a list in the reasons' own voice, the
   formula in the alternatives' type. */
.same-ion {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.readings {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.reading {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  font-size: 0.8rem;
  cursor: default;
}
.reading-icon {
  font-size: 0.8rem;
  opacity: 0.55;
}
.reading-formula {
  font-family: var(--font-mono, ui-monospace, monospace);
}
.reading-channel {
  opacity: 0.65;
}
/* A list's name for a formula, wherever the card shows one: the flask the
   search panel marks known compounds with, in the reading's own size. */
.reading-listed,
.alt-listed {
  opacity: 0.75;
}
.alt-listed {
  margin-left: 0.4rem;
  font-family: var(--p-font-family, inherit);
  font-size: 0.72rem;
}
/* What kind of alternative a row is, beside its formula rather than in a
   column of its own: the list's columns are the numbers. */
.alt-kind {
  margin-left: 0.4rem;
  padding: 0 0.3rem;
  border: 1px dashed var(--p-content-border-color, #e3e6ec);
  border-radius: 0.25rem;
  font-size: 0.7rem;
  opacity: 0.7;
}
/* A list's name for the formula, in the evidence grid's key/value voice, above
   it and as wide as the card: it says what the row is, the grid how well. */
.identity {
  display: flex;
  flex-direction: column;
}
.identity .listed .v {
  overflow-wrap: anywhere;
}
.identity .potential {
  text-transform: none;
  letter-spacing: normal;
}
.identity .list-source {
  font-size: 0.72rem;
  opacity: 0.6;
  overflow-wrap: anywhere;
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
.ev.measuring .v {
  font-style: italic;
  opacity: 0.7;
}
</style>
