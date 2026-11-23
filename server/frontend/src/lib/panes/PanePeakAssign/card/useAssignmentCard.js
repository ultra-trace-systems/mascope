import { computed, nextTick, ref, useId, watch } from 'vue'

import { useApp } from '@/stores'
import { num } from '@/lib/formatters'
import { formatIsotopeFormula, formatIsotopeLabel, neutralKey } from '@/lib/chem'
import { isIsotopeLine } from '@/lib/isotopeLines'
import { notFoundLines } from '@/lib/predictedEnvelope'
import { standardMechanism } from '@/lib/mechanism'
import { listingOf, listingTooltip, targetOf } from '@/lib/referenceListings'
import { tierReasonsOf } from '@/lib/tierReasons'
import { MARKS } from '@/lib/evidence/marks'
import { coVariationLabel } from '@/lib/batchCoVariation'
import { checksSummary } from '@/lib/batchChecks'
import { persistenceHold, persistenceHoldNote } from '@/lib/batchPersistence'
import { ROLE_BUCKETS, bucketRank, tierName } from '@/lib/tiers'
import { isLevel, isOpenRival, levelMeta, unlistedRivalsLine } from '@/lib/levels'
import { sourceLabel } from '@/lib/assignmentSources'
import { EVIDENCE_LEVELS, VERDICT_META, claimCandidates } from '@/lib/verification'
import { useBatchPeakJump } from '@/lib/panes/PaneBrowserMatch/stores/batchPeakJump.js'
import { useBatchPeakCuration } from '../stores/batchPeakCuration.js'
import { useChangeGate } from '../changeGate.js'
import {
  bandMissed,
  barPosition,
  bandsOf,
  batchTile,
  formatShare,
  isotopeTile,
  orderedReasons,
  reasonGroup,
  shareDigits,
  whyTitle
} from './cardReadings.js'
import {
  currentReading,
  measuredLines,
  measuredTierLabel,
  measuredTooltipLines
} from '../candidateMeasure.js'

/**
 * The inspector card's model: everything the card's sections read about the
 * focused peak's assignment, and the actions they take on it.
 *
 * Called once, by `PanePeakAssign.vue`, which provides it to the sections
 * under `card/` (`useCard`). The sections read only what they show.
 *
 * @param {{showSearch: import('vue').Ref<boolean>}} options - the Sample view's
 *   toggle for the composition search, which the verdict bar opens
 */
export function useAssignmentCard({ showSearch }) {
  const app = useApp()
  const curation = useBatchPeakCuration()
  const jump = useBatchPeakJump()

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
      `Engine tier: ${tierName(tier)}, as the engine that made this run rated it\n` +
      (agrees
        ? "It agrees with Mascope's tier."
        : "It differs from Mascope's tier, which reads the evidence alone; the engine also " +
          'weighs arbitration, corroboration and degeneracy.')
    )
  })

  // The ledger rows are a slim projection; the full record (alternatives +
  // provenance) is fetched per assignment when its peak is focused. Until it
  // arrives the inspector renders the slim fields and the detail sections fill
  // in, so a slow fetch degrades to less detail rather than an empty inspector.
  const focusedDetail = computed(() =>
    app.data.peakAssignment.peak.detailOf(focusedAssignment.value?.peak_assignment_id)
  )
  // The row whose detail is on its way, so the card can draw its sections as
  // skeletons until it lands rather than let them pop in one by one.
  const detailLoadingId = ref(null)
  watch(
    focusedAssignment,
    (assignment) => {
      if (!assignment) return
      const id = assignment.peak_assignment_id
      if (!app.data.peakAssignment.peak.detailOf(id)) detailLoadingId.value = id
      // Failures already toast via the http layer; the inspector just stays slim.
      app.data.peakAssignment.peak
        .loadDetail(assignment)
        .catch(() => {})
        .finally(() => {
          if (detailLoadingId.value === id) detailLoadingId.value = null
        })
    },
    { immediate: true }
  )
  const loading = computed(
    () =>
      detailLoadingId.value != null &&
      detailLoadingId.value === focusedAssignment.value?.peak_assignment_id
  )

  // --- The card's states (docs/dev/assignment_ledger_and_card.md, 4.2) ---------
  // The run the card reads is the one the run store focuses, which the run
  // selector can set to an earlier run or, asked for, a decoy. Neither is the
  // sample's result: the card says which run it is, and offers nothing that
  // would record a verdict or a change on it.
  // The same gate checks whether the account may write in the workspace,
  // before anything is offered: a viewer sees the recorded verdict and no
  // controls, and the 403 a refused write earns stays the fallback (`denied`,
  // `curateDenied`). `canChange` is what the verdict bar and the changes by
  // hand both need: an editor, on the sample's own result. The composition
  // search's hand button reads the same gate.
  const { viewedRun, notice, readOnly, readOnlyReason, canEdit, canChange } = useChangeGate()

  // --- Opened from the batch scope ----------------------------------------------
  // The batch ledger opens a batch peak in its brightest sample
  // (`useBatchPeakJump`). While the card shows one of that peak's samples, on
  // the peak the batch named there, it says which sample, and steps through
  // the others.
  const batchOpened = computed(() => {
    const opened = jump.opened
    if (!opened?.members?.length || opened.sample_batch_id !== app.data.batch?.focusedId) {
      return null
    }
    const sampleId = app.data.sample.focusedId
    const peakId = app.data.peak.focused?.peak_id
    const index = opened.members.findIndex(
      (member) =>
        member.sample_item_id === sampleId &&
        (member.sample_peak_id == null || String(member.sample_peak_id) === String(peakId))
    )
    if (index === -1) return null
    const nameOf = (id) =>
      (app.data.sample.list ?? []).find((s) => s.sample_item_id === id)?.sample_item_name ?? id
    const count = opened.members.length
    return {
      index,
      count,
      name: nameOf(sampleId),
      next: count > 1 ? nameOf(opened.members[(index + 1) % count].sample_item_id) : null
    }
  })
  function nextSample() {
    const opened = batchOpened.value
    if (!opened || opened.count < 2) return
    jump.stepTo((opened.index + 1) % opened.count)
  }

  // A row that names an ion and no compound - a peak the source made - is headed
  // by its ion, known exactly, rather than by the word "Unassigned": the role chip
  // beside it says whose the ion is.
  const headlineIsIon = computed(
    () =>
      !focusedAssignment.value?.assigned_formula && Boolean(focusedAssignment.value?.ion_formula)
  )

  // What the card is about when there is no formula to name it by: an unassigned
  // ledger row, or a focused peak with no assignment row at all. Both otherwise
  // read "Unassigned" over an empty evidence grid - identical for every
  // unassigned peak in the sample, so the card never says which peak it is.
  // The ledger row calls the intensity `sample_peak_intensity`; the bare peak
  // record calls it `height`.
  const peakSummary = computed(() => {
    const mz = focusedAssignment.value?.sample_peak_mz ?? app.data.peak.focused?.mz
    const intensity =
      focusedAssignment.value?.sample_peak_intensity ?? app.data.peak.focused?.height
    return [
      mz != null ? `m/z ${num.mz.format(mz)}` : null,
      intensity != null ? `intensity ${num.peakIntensity.format(intensity)}` : null
    ]
      .filter(Boolean)
      .join(' · ')
  })

  // What the card says of a focused peak the run on view has no row for. A
  // run writes a row for every peak it read, an unexplained one included, so
  // a peak without one is a peak no run on view has read: the sample has no
  // run yet, or its peaks were found again after the run.
  const noRowLine = computed(() =>
    viewedRun.value
      ? 'The run on view has no row for this peak.'
      : 'No run has assigned this sample yet.'
  )

  // --- Verification (labelling) capture -------------------------------------
  // The current verdict for the focused assignment (by stable identity), plus a
  // small confirm/reject/unsure form. See docs/dev/verification_calibration_loop.md
  // section 4.5.
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
  // The structure a confirmation names, from the compounds the lists name for the
  // formula (BaseClaimStructure); one on structure evidence must name one.
  const claimOptions = computed(() =>
    claimCandidates({ target: targetAs.value, identities: listedAs.value?.identities })
  )
  const claim = ref(null)
  const canConfirm = computed(() => Boolean(evidenceLevel.value) && claim.value?.ready !== false)

  // Show the verdict buttons when there is no verdict yet, or the user chose to
  // change it.
  //
  // Never for a viewer, who is offered no control at all. On a run that is not
  // the sample's result the buttons show, disabled, beside the reason.
  const showVerifyForm = computed(
    () =>
      verifiable.value && canEdit.value && !denied.value && (!verification.value || editing.value)
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
  const dialogMeta = computed(
    () => VERDICT_DIALOGS[dialogVerdict.value] ?? VERDICT_DIALOGS.confirmed
  )
  const confirming = computed(() => dialogVerdict.value === 'confirmed')
  // Where focus lands as the dialog opens: on a confirmation, the level already
  // picked when the verdict being changed carries one, else the strongest; on
  // the others, the note. The dialog focuses nothing on its own (Popover only
  // looks for an `[autofocus]` child).
  const autofocusLevel = computed(() => evidenceLevel.value ?? EVIDENCE_LEVELS[0].value)

  function openVerdict(verdict, event) {
    const dialog = verdictDialog.value
    if (!dialog || !canChange.value) return
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
    // Confirm requires an evidence level, and a structure on structure evidence
    // (both also enforced server-side).
    if (confirm && !canConfirm.value) return false
    if (!canChange.value) return false
    submitting.value = true
    pendingVerdict.value = verdict
    try {
      await app.data.peakAssignment.verification.verify({
        peak_assignment_id: verifyTarget.value.peak_assignment_id,
        verdict,
        evidence_level: confirm ? evidenceLevel.value : null,
        note: note.value?.trim() || null,
        ...(confirm && claim.value?.claimed_inchikey
          ? {
              claimed_inchikey: claim.value.claimed_inchikey,
              claimed_name: claim.value.claimed_name
            }
          : {})
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
  // Rules), arbitration confidence and a tie flag. The calibrated P(correct) is
  // in it too, and is not shown: its curve is provisional (step 3.4d of the
  // assignment quality plan), so the API serves it and the card does not.
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
  //
  // `measured` is the other on-demand measurement: a derived row's alternative is
  // one of the batch peak's identities, measured on this sample's member before
  // "use this" pins it batch-wide (see `candidateMeasureOf`).
  const alternatives = computed(() =>
    storedAlternatives.value.map((alt, index) => ({
      ...alt,
      scored:
        alt?.assigned_formula && !isUndoEntry(alt, index)
          ? (scoreByFormula.value.get(alt.assigned_formula) ?? null)
          : null,
      measured: candidateMeasureOf(alt)
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
  // A source ion's monoisotopic row is a reagent row that is no other row's line
  // (isIsotopeLine), and heads its lines as an analyte's M0 heads its own.
  const m0 = computed(
    () =>
      family.value.find(
        (f) =>
          f.role === 'M0' || f.isotope_label === 'M0' || (f.role === 'reagent' && !isIsotopeLine(f))
      ) ?? null
  )
  const isFamilyHead = (iso) =>
    m0.value != null && iso.peak_assignment_id === m0.value.peak_assignment_id

  // The lines of the M0's predicted envelope that its run asked the spectrum for
  // and did not find there (step 3.4e): listed under the found lines, so an
  // absence reads as one rather than as a row the table happens not to have.
  // They are on the M0's own record: the focused row's detail, or for a focused
  // isotopologue the M0's, which the card fetches for the reasons that row
  // follows (`reasonsOwner`) - every isotopologue of a run that asked any line
  // carries one. A derived row's family was never asked.
  const m0Provenance = computed(() => {
    const head = m0.value
    if (!head || derivedRun.value) return null
    const detail = app.data.peakAssignment.peak.detailOf(head.peak_assignment_id)
    return detail?.provenance ?? head.provenance
  })
  const missingLines = computed(() => notFoundLines(m0Provenance.value))

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
  // is never the smaller number and is preferred.
  //
  // Neither is written onto an isotopologue: it is the same ion measured at
  // another isotope, not a second sighting of the compound. The evidence is about
  // the formula the family shares, so a focused isotopologue shows its M0's count,
  // flagged inherited. Only the count carries across - the channels are named in
  // the M0's provenance, and detail is fetched for the focused assignment alone.
  // The channels are named as the run recorded them, which for a run from before
  // the standard adduct notation is the legacy spelling; both show as the standard.
  const corroboration = computed(() => {
    const channels = provenance.value?.cross_channel?.channels
    if (channels?.length) {
      return {
        n: channels.length,
        names: channels.map(standardMechanism),
        inherited: false
      }
    }
    const own = provenance.value?.corroboration
    if (own?.n_adducts != null) {
      return {
        n: own.n_adducts,
        names: (own.adducts ?? []).map(standardMechanism),
        inherited: false
      }
    }
    // The slim ledger row carries both counts flattened, so the badge is there
    // before the detail fetch lands - just without the channel names.
    const flatChannels = focusedAssignment.value?.corroboration_channels
    if (flatChannels != null) return { n: flatChannels, names: [], inherited: false }
    const flat = focusedAssignment.value?.corroboration_adducts
    if (flat != null) return { n: flat, names: [], inherited: false }
    // Same two-step as the ledger's, so the two panes agree about a family whose
    // rows carry provenance inline (a backend predating the slim projection).
    const m0Channels =
      m0.value?.corroboration_channels ?? m0.value?.provenance?.cross_channel?.channels?.length
    if (m0Channels != null) return { n: m0Channels, names: [], inherited: true }
    const fromM0 = m0.value?.corroboration_adducts ?? m0.value?.provenance?.corroboration?.n_adducts
    return fromM0 != null ? { n: fromM0, names: [], inherited: true } : null
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

  // The tooltip said whether the count was folded into P(correct) while the card
  // showed that probability; it no longer names a number the card does not show.
  const corroborationTooltip = computed(() => {
    const c = corroboration.value
    if (!c) return ''
    const click = otherReadings.value.length ? '\nClick for the peaks the other channels read.' : ''
    if (c.inherited) {
      return (
        `This isotopologue's M0 was seen through ${c.n} ionization channels: ` +
        'independent evidence for the formula.' +
        click
      )
    }
    const channels = (c.names ?? []).join(', ')
    return (
      `Seen through ${c.n} ionization channels${channels ? ` (${channels})` : ''}: ` +
      'independent evidence for the formula.' +
      click
    )
  })

  // --- The batch's co-variation reading ------------------------------------------
  // What a "Read co-variation" run of the batch says of the batch peak this row's
  // peak is in: how its trace follows the same compound through the mode's other
  // channels. The detail read serves it only where the row reads the peak as the
  // batch did, so it is about this reading; display-only, as the line says.
  const batchCoVariation = computed(() => focusedDetail.value?.batch_co_variation ?? null)
  const batchCoVariationR = computed(() => {
    const record = batchCoVariation.value
    return record && record.r != null ? coVariationLabel(record) : ''
  })

  // --- The batch checks ----------------------------------------------------------
  // What a "Read batch checks" run states of the batch peak this row's peak is
  // in: its halogen lock, its labelled ion's 14N twin, its uronium pair. The
  // detail read serves them only where the row reads the peak as the checks did;
  // display-only, as the line says.
  const batchChecks = computed(() => focusedDetail.value?.batch_checks ?? null)

  // --- The batch search's persistence admission -------------------------------
  // Where "Search untargeted" took the batch peak this row's peak is in because
  // the peak recurs across the batch rather than for its height: the share of
  // the batch's spectra the admission rested on, and - on a row that reads what
  // the search elected there - whether the batch holds that reading at
  // candidate, read off the batch peak's tier: the row's own may be its run's.
  const batchPersistence = computed(() => focusedDetail.value?.batch_persistence ?? null)
  const batchPersistenceHold = computed(() =>
    persistenceHold(batchPersistence.value?.tier_reasons, batchPersistence.value?.consensus_tier)
  )
  const batchPersistenceNote = computed(() =>
    persistenceHoldNote(
      batchPersistence.value?.tier_reasons,
      batchPersistence.value?.consensus_tier
    )
  )

  // --- The batch's flat-series reading ----------------------------------------
  // What "Read co-variation" found of the trace of the batch peak this row's
  // peak is in, against the batch's reagent ions or total ion current, on a row
  // that reads the reading it was read on: varying, or flat - an origin flag,
  // likely background, which moves no tier.
  const batchFlatSeries = computed(() => focusedDetail.value?.batch_flat_series ?? null)

  // --- The batch's isotopologue stamping ---------------------------------------
  // What "Stamp isotopologues" says of this row's peak in this sample: the batch
  // read it as another batch peak's isotope line there, whatever the row reads
  // it as. The line links the parent's peak in this sample, as the channel badge
  // links the other channels' peaks. And on a monoisotopic row that reads the
  // parent's formula, the parent's own record: each isotope line predicted,
  // found and stamped. Display-only, as the lines say.
  const batchStamp = computed(() => focusedDetail.value?.batch_stamped_isotopologue_of ?? null)
  const batchStampParent = computed(() => {
    const id = batchStamp.value?.parent_sample_peak_id
    return id != null ? peakOf({ sample_peak_id: id }) : null
  })
  function focusStampParent() {
    if (batchStampParent.value) app.data.peak.focus(batchStampParent.value)
  }
  // A record is kept for every parent with a predicted line, found or not; like
  // the ledger's mark, the line shows only where the batch found one of them.
  const batchIsotopologues = computed(() => {
    const record = focusedDetail.value?.batch_diagnostic_isotopologues
    return record?.n_found ? record : null
  })

  // --- The other channels' peaks ------------------------------------------------
  // The badge names channels, which leaves the reader to find each one's peak in
  // the ledger: the cross-channel record is about the neutral and names no peak.
  // So the peaks are read off the ledger's rows, by neutral formula, the way the
  // ledger's "group by formula" reads them (PaneBrowserAssignment.vue): every
  // analyte row of the same neutral but the family's own M0, strongest first.
  // Isotope lines are their M0's peak again, and a source ion or a peak nothing
  // explained names no neutral of the sample.
  const otherReadings = computed(() => {
    const own = app.data.peakAssignment.peak.m0Of(focusedAssignment.value)
    const key = neutralKey(own?.assigned_formula)
    if (!key) return []
    return (app.data.peakAssignment.peak.list ?? [])
      .filter(
        (row) =>
          row.peak_assignment_id !== own.peak_assignment_id &&
          !isIsotopeLine(row) &&
          !ROLE_BUCKETS.includes(row.role) &&
          neutralKey(row.assigned_formula) === key
      )
      .sort(
        (a, b) =>
          bucketRank(a.tier, a.role) - bucketRank(b.tier, b.role) ||
          (b.fit_score ?? -1) - (a.fit_score ?? -1)
      )
      .map((row) => ({ row, channel: channelOf(row), peak: peakOf(row) }))
  })

  // A reading whose peak the sample no longer has - its peaks were found again
  // after the run - is listed but not a link: focusing it would do nothing.
  const STALE_PEAK_TOOLTIP =
    "This run's peak is no longer in the sample's peak list: the peaks were found again after " +
    'the run. Run the assignment again to read the current peaks.'
  const readingTooltip = (reading) =>
    reading.peak
      ? `Focus this peak: ${reading.row.assigned_formula}` +
        `${reading.channel ? ` through ${reading.channel}` : ''}`
      : STALE_PEAK_TOOLTIP

  const channelsMenu = ref(null)
  const toggleChannels = (event) => channelsMenu.value?.toggle(event)
  function focusReading(reading) {
    channelsMenu.value?.hide()
    app.data.peak.focus(reading.peak)
  }

  // Compact substitution label (e.g. "[13C]", "[81Br]2") from the full
  // isotopologue formula; falls back to the M0/M+1 offset label. A source ion's
  // lines name their heavy isotopes in the label alone ("81Br2"), which reads in
  // the same brackets, and its monoisotopic row, which carries no label, is M0.
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
      : formatIsotopeLabel(iso.isotope_label) || (isFamilyHead(iso) ? 'M0' : '-')

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
  const peakOf = (entry) =>
    app.data.peak.list.find((p) => String(p.peak_id) === String(entry.sample_peak_id)) ?? null
  const focusIsotopePeak = (iso) => {
    const peak = peakOf(iso)
    if (peak) app.data.peak.focus(peak)
  }

  // Per-isotopologue match quality; M0 is the reference and never "poor".
  const isPoorMatch = (iso) => {
    if (iso.role === 'M0' || iso.isotope_label === 'M0' || isFamilyHead(iso)) return false
    const ab = iso.abundance_error != null ? 1 - Math.min(1, Math.abs(iso.abundance_error)) : 1
    const mz = iso.mz_error_ppm != null ? Math.max(0, 1 - 0.01 * Math.abs(iso.mz_error_ppm)) : 1
    return ab * mz < 0.5
  }

  // The numbers an entry was scored on, whichever of the two ways it got them.
  // A runner-up the run competed carries its own; a finder shortlist entry gets
  // them from the on-demand measurement, which also names the adduct it found.
  // Falls back to the entry's own fields so a run stored before this existed,
  // and an imported one, read exactly as they did.
  const altFit = (alt) =>
    alt?.fit_score ?? alt?.scored?.fit_score ?? alt?.measured?.fit_score ?? null
  const altMzError = (alt) =>
    alt?.mz_error_ppm ?? alt?.scored?.mz_error_ppm ?? alt?.measured?.mz_error_ppm ?? null
  const altPlausibility = (alt) => alt?.plausibility ?? alt?.measured?.plausibility ?? null
  const altAdduct = (alt) => alt?.scored?.ionization_mechanism ?? null

  // Stats for a close alternative (runner-up), surfaced on hover. Entries the
  // run scored carry fit + m/z error; the finder's shortlist entries read
  // "measuring" until their measurement lands, then either show one or say why
  // there is none.
  const altTooltip = (alt, index) => {
    const fit = altFit(alt)
    const measuring =
      fit == null && !alt?.scored && !alt?.measured && (scoring.value || measuringAlt(alt))
    const kind = alternativeKind(alt)
    const lines = [
      ...(kind ? [kind.tooltip] : []),
      `fit: ${
        fit != null
          ? formatFit(fit)
          : measuring
            ? '— measuring'
            : alt?.measured
              ? '— none on this peak'
              : '— not measured'
      }`
    ]
    const mzError = altMzError(alt)
    if (mzError != null) {
      lines.push(`m/z error: ${num.mzError.format(mzError)} ppm`)
    }
    const plausibility = altPlausibility(alt)
    lines.push(`plausibility: ${plausibility != null ? formatFit(plausibility) : '—'}`)
    lines.push(...measuredTooltipLines(alt.measured))
    // Named only when the measurement supplied it: an entry that arrived with an
    // adduct already shows it as its ion formula in the row above.
    if (altAdduct(alt)) lines.push(`adduct: ${altAdduct(alt)}`)
    if (alt.source) lines.push(`source: ${sourceLabel(alt.source)}`)
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

  // A derived row with a formula. The row a run fills - arbitration confidence -
  // is kept on the card for it, with a placeholder that says why the ledger has
  // none, so the card reads the same for every sample.
  const ledgerServed = computed(
    () => derivedRun.value && Boolean(focusedAssignment.value?.assigned_formula)
  )
  // Why a row served from the batch ledger shows no arbitration confidence: that
  // number is a run's, from weighing a peak's candidates against each other, and
  // a ledger member carries one identity with nothing to weigh it against.
  const LEDGER_CONFIDENCE_TOOLTIP =
    'Arbitration confidence comes from an assignment run of this sample, which weighs the ' +
    "peak's candidates against each other; a row served from the batch ledger carries one " +
    'identity and no such contest'

  // --- Measuring a batch identity before it is pinned (step 3.4c) ------------
  // A derived row's alternatives are the batch peak's other identities, each
  // measured in the sample that brought it and not in this one: "use this" pins
  // one batch-wide, which used to be the only way to see how it reads here. The
  // server measures each on this sample's member as the pin would - its fit, its
  // lines, plausibility and the tier the pin writes - beside what the member
  // reads now; nothing is written. One request at a time: each loads this
  // sample's peaks and builds an envelope.
  const peakStore = app.data.peakAssignment.peak
  const sampleBatchId = computed(() => app.data.batch?.focusedId ?? null)
  const derivedCandidates = computed(() =>
    derivedRun.value && focusedAssignment.value?.batch_peak_id
      ? storedAlternatives.value.map((alt) => alt?.candidate).filter((c) => c != null)
      : []
  )
  const candidateMeasureOf = (alt) =>
    derivedRun.value && alt?.candidate != null
      ? peakStore.candidateMeasureOf(
          peakStore.candidateMeasureKey(focusedAssignment.value, alt.candidate)
        )
      : null
  const measuringAlt = (alt) =>
    derivedRun.value &&
    alt?.candidate != null &&
    peakStore.candidateMeasurePending(
      peakStore.candidateMeasureKey(focusedAssignment.value, alt.candidate)
    )
  watch(
    [derivedCandidates, sampleBatchId],
    async () => {
      const row = focusedAssignment.value
      const batchId = sampleBatchId.value
      if (!batchId || !row) return
      for (const candidate of derivedCandidates.value) {
        // A failure already toasts; the entry keeps reading "not measured".
        await peakStore.loadCandidateMeasure(batchId, row, candidate).catch(() => {})
      }
    },
    { immediate: true }
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
  // abundance column is a fraction of (see theoreticalRel). A line listed as not
  // found counts: a dibromide whose M+2 is missing is still read against it.
  const familyRelReference = computed(() => {
    const values = [
      ...familyRows.value.map(relativeToM0),
      ...missingLines.value.map((line) => line.predicted_share)
    ].filter((v) => v != null && v > 0)
    return values.length ? Math.max(...values) : null
  })
  // A not-found line's share, on the abundance column's scale.
  const missingRel = (line) => {
    const top = familyRelReference.value
    return top && top > 0 ? line.predicted_share / top : line.predicted_share
  }
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

  // --- Identification level -------------------------------------------------------
  // What a reader may state of the row, in the scale peaky and this server share
  // (lib/levels.js): the level, the line naming what earned it, what would raise
  // it, and the rivals it was read against, each with the test that excluded it
  // or none. Shown beside the tier, never instead of it: the level moves no tier.
  //
  // The level and its two lines are on the slim ledger row, so they show before
  // the detail lands; the rivals are only in the detail's provenance. A row with
  // no level - one that commits no formula, an imported row, a run from before
  // the level - shows nothing. Nor does a derived row, whose provenance is its
  // anchor's consensus record: no run read a level for this sample's peak.
  //
  // An isotopologue carries its M0's level and no rivals of its own (its record
  // names the M0 it inherits from); the card says whose level it is.
  const identificationLevel = computed(() => {
    const row = focusedAssignment.value
    if (!row || derivedRun.value) return null
    const record = provenance.value?.identification_level ?? null
    const level = row.level ?? focusedDetail.value?.level ?? record?.level ?? null
    if (!isLevel(level)) return null
    const rivals = Array.isArray(record?.rivals) ? record.rivals : []
    return {
      level,
      name: levelMeta(level).name,
      evidence:
        row.level_evidence ?? focusedDetail.value?.level_evidence ?? record?.evidence ?? null,
      lift: row.level_lift ?? focusedDetail.value?.level_lift ?? record?.lift ?? null,
      inherited: row.role === 'iso_child' || record?.inherited_from != null,
      rivals,
      unlisted: unlistedRivalsLine(record?.rivals_unlisted, rivals.length)
    }
  })
  // What a rival's line says on hover: the ion it would be, where the record names
  // one, and whether a test excluded it.
  const rivalTooltip = (rival) =>
    [
      rival.ion_formula ? `Ion: ${rival.ion_formula}` : null,
      isOpenRival(rival)
        ? 'Open: no test of the run excluded this rival'
        : 'Excluded by a test of the run'
    ]
      .filter(Boolean)
      .join('\n')

  // A peak the instrument did not separate from a neighbouring one names that
  // neighbour beside the reason, as a link that focuses it. A neighbour the
  // sample no longer has - its peaks were found again after the run - is named
  // and says so, but is no link: focusing it would do nothing.
  const neighbourPeak = (reason) =>
    reason.neighbour ? peakOf({ sample_peak_id: reason.neighbour.peakId }) : null
  const focusNeighbour = (reason) => app.data.peak.focus(neighbourPeak(reason))

  // --- Where the mass error sits -------------------------------------------------
  // The ppm error is a distance with no scale. The run measures its own mass
  // calibration over the rows it committed and corroborated, together with the
  // source's own lines its reagent pre-pass claimed, and records every
  // committed row's distance from it in that calibration's widths (`mass_z`):
  // the number its mass gate judged, and what "off calibration" among the
  // reasons is about. Flattened onto the ledger row, so it shows before the
  // detail lands; a derived row and an imported one have none.
  const massZ = computed(() => {
    const z = focusedAssignment.value?.mass_z ?? provenance.value?.mass_z
    return typeof z === 'number' && Number.isFinite(z) ? z : null
  })
  // What the distance is measured in, recorded once on the run: its measurement
  // model, the centre and width the row's fit was scored at too, with what its
  // mass gate capped at it. A run from before the one record kept the gate's
  // calibration under its own key.
  const isRecord = (value) => value != null && typeof value === 'object' && !Array.isArray(value)
  const massCalibration = computed(() => {
    const config = app.data.peakAssignment.peak.run?.config
    const model = config?.measurement_model
    if (isRecord(model)) {
      const gate = isRecord(model.gate) ? model.gate : {}
      return { ...model, gate_sigma_ppm: model.width_ppm, cap_z: gate.cap_z, floor_z: gate.floor_z }
    }
    return isRecord(config?.mass_calibration) ? config.mass_calibration : null
  })
  // Past the distance the gate caps a row at: marked, since that is what a reader
  // scanning the card needs to see. Whether the cap applied is the reasons' to
  // say - an isotopologue that tracks the row lifts it.
  const massZFar = computed(() => {
    const cap = massCalibration.value?.cap_z
    return massZ.value != null && typeof cap === 'number' && Math.abs(massZ.value) > cap
  })
  // What each key of the evidence grid says on hover. mass z builds its own,
  // from what the run measured.
  const EVIDENCE_TOOLTIPS = Object.freeze({
    fit: "How well the peak and its isotope lines match the formula's predicted pattern",
    mzError: 'Measured minus predicted m/z of the peak, in ppm',
    abundanceError: 'How far the measured isotope abundances are from the predicted ones',
    plausibility: 'How chemically plausible the formula is (Seven Golden Rules)',
    evidence: 'Fit × plausibility: the number the tier is read off',
    confidence: "This formula's share of the evidence among all candidates for the peak"
  })

  // An isotopologue row's hover text: its full isotope formula, how it matched,
  // and what a click does.
  const isoRowTooltip = (iso) =>
    [
      iso.isotope_formula || iso.isotope_label,
      isPoorMatch(iso) ? 'Poor match to the prediction (abundance or m/z off)' : null,
      'Click to focus this peak'
    ]
      .filter(Boolean)
      .join('\n')

  const zFormat = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
    signDisplay: 'exceptZero'
  })
  const massZTooltip = computed(() => {
    if (massZ.value == null) return ''
    const lines = ["The m/z error in widths of the run's own mass calibration at this m/z."]
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
        `Beyond ${cap} widths an uncorroborated row stays at candidate` +
          (typeof floor === 'number' ? `; beyond ${floor}, below assignability.` : '.')
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
    'The same ion read as another neutral with another adduct. The two readings share their ' +
    'mass and isotope pattern, so only a second ionization channel, or the sample showing one ' +
    'of the molecules far more strongly, can tell them apart.'

  // --- How the ion was made, and what a list calls it ----------------------------
  // The mechanism is half of an assignment: the neutral and the ion formula imply
  // it, but a reader should not have to take one from the other to see it.
  const ionization = computed(() => channelOf(focusedAssignment.value))
  const ionizationTooltip = computed(() => {
    const row = focusedAssignment.value
    if (!ionization.value) return ''
    return row?.assigned_formula && row?.ion_formula
      ? `Ionization mechanism: ${row.assigned_formula} ${ionization.value} → ${row.ion_formula}`
      : 'Ionization mechanism'
  })
  // What a reference list calls the committed formula: the identities the run
  // matched (in the row's provenance), else the compounds a list holds for it.
  const listedAs = computed(() =>
    listingOf(focusedDetail.value, provenance.value?.reference_identities)
  )
  // What the target compound the row was matched to declares: a candidate beside
  // the list's, one with it where a list's identity has the same InChIKey.
  const targetAs = computed(() => targetOf(focusedDetail.value?.reference_listing, listedAs.value))

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
        "A loaded list's compound for this peak, taken by a formula from the search with more " +
        'than twice its evidence that also explains its isotope lines. Using it puts the list ' +
        'compound back.'
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
  // Find other formulas - the composition search - is the way past every one of these
  // states: it searches the peak's composition against the sample's adducts from
  // scratch rather than measuring the formulas this shortlist happens to name, so
  // it can find one this list never held.
  const RESEARCH_HINT = 'Find other formulas for this peak searches wider than this shortlist.'
  const NO_ADDUCT_HINT = `Not assignable to this peak: the formula names no adduct. ${RESEARCH_HINT}`
  const SCORING_HINT = 'Measuring this formula against the peak...'

  // One blocked entry is not a candidate the finder listed: the winner an
  // override displaced, which the server archives and pushes back to the head of
  // this list so the same control undoes the change. That winner can name no
  // adduct of its own - the untargeted stage writes one when the finder echoes a
  // notation the mechanism map does not hold, and an imported run reaches it
  // trivially, since an imported row may only ever have carried a formula - and
  // then the undo is refused by the same 422.
  //
  // The composition search is worth naming, but not as if it were the undo: it
  // writes a NEW assignment, and the isotopologues this override unassigned are
  // put back by compound AND adduct, so they stay unassigned.
  const NO_ADDUCT_UNDO_HINT =
    'Cannot be undone here: the assignment this replaced named no adduct. Find other ' +
    'formulas for this peak can ' +
    'assign the formula again under an adduct, but as a new assignment, and the ' +
    'isotopologues this override cleared stay cleared.'

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
    if (!canPromote(alt) || curating.value !== null || !canChange.value) return
    if (derivedRun.value && alt.candidate == null) return
    curating.value = index
    try {
      if (derivedRun.value) {
        // A derived row has no row of its own to edit: "use this" here pins the
        // identity on the batch peak and measures it in every sample of the
        // batch. The guard is the consensus the card shows, not this member's
        // own formula, which is what the server compares against.
        // The sample it was measured on goes with it, so the pin records the
        // measurement the curator judged it by.
        await curation.curate({
          batch_peak_id: focusedAssignment.value.batch_peak_id,
          candidate: alt.candidate,
          expected_formula: provenance.value?.batch_peak?.consensus_formula ?? null,
          sample_item_id: focusedAssignment.value.sample_item_id ?? app.data.sample.focusedId
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
  // What the curator saw before the pin, kept on it by the server.
  const batchOverrideMeasured = computed(() => batchOverride.value?.measured ?? null)
  const measuredAtPin = computed(() => {
    const record = batchOverrideMeasured.value
    if (!record) return null
    if (!record.takes_member) return record.blocked_reason ?? null
    const parts = [`fit ${formatFit(record.fit_score)}`, measuredTierLabel(record)]
    const lines = measuredLines(record)
    if (lines) parts.push(lines)
    return parts.filter(Boolean).join(', ')
  })
  // What this sample's member reads now, as the first measured alternative
  // reports it: the same member for every alternative.
  const measuredCurrent = computed(() => {
    const first = alternatives.value.find((alt) => alt.measured?.current)
    return first ? currentReading(first.measured) : null
  })
  const releasing = ref(false)
  async function releaseOverride() {
    if (releasing.value || !focusedAssignment.value?.batch_peak_id || !canChange.value) return
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
  // --- The Why box ----------------------------------------------------------------
  // The tier, read as the reasons for it: the evidence against the run's own
  // bands, the product it is, and the reasons, those holding the tier back
  // first (docs/dev/assignment_ledger_and_card.md, section 4.1).
  const tierBands = computed(() => bandsOf(app.data.peakAssignment.peak.run?.tier_bands))
  const whyHeading = computed(() => whyTitle(focusedAssignment.value?.tier))
  // The evidence the tier was read off, and its two factors.
  const evidenceValue = computed(() => {
    const value = evidenceRow.value?.evidence
    return typeof value === 'number' && Number.isFinite(value) ? value : null
  })
  const evidenceBar = computed(() => {
    if (evidenceValue.value == null) return null
    const bands = tierBands.value
    return {
      fill: barPosition(evidenceValue.value),
      candidate: barPosition(bands.candidate),
      assigned: barPosition(bands.assigned),
      bands
    }
  })
  // One precision for the product line's numbers, as fine as it takes for the
  // evidence not to read as the band it misses, as the evidence-band reason's
  // sentence has it (tiering.band_reason).
  const evidenceDigits = computed(() =>
    shareDigits(evidenceValue.value, bandMissed(evidenceValue.value, tierBands.value))
  )
  const evidenceText = computed(() =>
    evidenceValue.value != null ? formatShare(evidenceValue.value, evidenceDigits.value) : ''
  )
  // The product line, "fit 30 % × plausibility 100 % = 30 %": its factors,
  // each saying on hover what it measures, and the evidence they make.
  const productParts = computed(() => {
    const fit = focusedAssignment.value?.fit_score
    const digits = evidenceDigits.value
    return [
      fit != null
        ? { key: 'fit', text: `fit ${formatShare(fit, digits)}`, tooltip: EVIDENCE_TOOLTIPS.fit }
        : null,
      plausibility.value != null
        ? {
            key: 'plausibility',
            text: `plausibility ${formatShare(plausibility.value, digits)}`,
            tooltip: EVIDENCE_TOOLTIPS.plausibility
          }
        : null
    ].filter(Boolean)
  })
  const productLine = computed(() => {
    const parts = productParts.value
    if (!parts.length) return ''
    const product = parts.length === 2 && evidenceText.value ? ` = ${evidenceText.value}` : ''
    return `${parts.map((part) => part.text).join(' × ')}${product}`
  })
  const whyReasons = computed(() => orderedReasons(tierReasons.value))
  const whyOwnerReasons = computed(() => orderedReasons(ownerReasons.value))
  const showWhy = computed(
    () =>
      Boolean(focusedAssignment.value?.assigned_formula) &&
      (evidenceBar.value != null ||
        productParts.value.length > 0 ||
        tierReasons.value.length > 0 ||
        (corroboration.value?.n ?? 0) > 1 ||
        sameIonReadings.value.length > 0)
  )

  // --- The batch's groups ---------------------------------------------------------
  // The rows a supporting reason spans, as the batch serves them on the detail
  // (`batch_groups`, MAS-1066): each member's m/z links its peak in this
  // sample where the sample holds one.
  const batchGroups = computed(() => focusedDetail.value?.batch_groups ?? null)
  const batchSeries = computed(() => focusedDetail.value?.batch_series ?? null)
  const groupOf = (reason) => reasonGroup(reason, batchGroups.value, batchSeries.value)
  const memberPeak = (member) =>
    member?.sample_peak_id != null ? peakOf({ sample_peak_id: member.sample_peak_id }) : null
  const isOwnMember = (member) =>
    member?.sample_peak_id != null &&
    String(member.sample_peak_id) === String(focusedAssignment.value?.sample_peak_id)
  function focusMember(member) {
    const peak = memberPeak(member)
    if (peak) app.data.peak.focus(peak)
  }

  // --- Across the batch -----------------------------------------------------------
  // The four readings MAS-1066 added to the detail, in the registry's record
  // shapes, so the card draws them as the batch ledger does.
  const batchContinuity = computed(() => focusedDetail.value?.batch_continuity ?? null)
  const extraReadings = computed(() => {
    const detail = focusedDetail.value
    if (!detail) return []
    const continuity = batchContinuity.value
    const sources = [
      ['series', MARKS.series.read({ series: detail.batch_series ?? null })],
      [
        'ratio',
        continuity
          ? {
              continuity: continuity.detail ?? {},
              continuity_grade: continuity.grade ?? null,
              continuity_stale: continuity.stale ?? false
            }
          : null
      ],
      [
        'reagent',
        MARKS.reagent.read({
          known_species: detail.batch_known_species ?? null,
          water_ladder: detail.batch_water_ladder ?? null
        })
      ],
      ['blend', MARKS.blend.read({ blend: detail.batch_blend ?? null })]
    ]
    return sources
      .filter(([, record]) => record)
      .map(([id, record]) => ({
        id,
        record,
        text: MARKS[id].summary(record),
        tooltip: MARKS[id].tooltip(record)
      }))
  })
  // Every reading of the batch the section lists, counted for the batch tile.
  const batchReadingCount = computed(
    () =>
      [
        batchStamp.value,
        batchIsotopologues.value,
        batchCoVariation.value,
        checksSummary(batchChecks.value) ? batchChecks.value : null,
        batchPersistence.value,
        batchFlatSeries.value
      ].filter(Boolean).length + extraReadings.value.length
  )
  // The badges the section's folded line shows: each reading's mark and record.
  const batchBadges = computed(() =>
    [
      batchStamp.value ? { id: 'isotopes', record: { stampedAs: batchStamp.value } } : null,
      batchIsotopologues.value
        ? { id: 'isotopes', record: { stamping: batchIsotopologues.value } }
        : null,
      batchCoVariation.value ? { id: 'coVariation', record: batchCoVariation.value } : null,
      checksSummary(batchChecks.value) ? { id: 'facts', record: batchChecks.value } : null,
      batchPersistence.value ? { id: 'persistence', record: batchPersistence.value } : null,
      batchFlatSeries.value ? { id: 'flatSeries', record: batchFlatSeries.value } : null,
      ...extraReadings.value.map(({ id, record }) => ({ id, record }))
    ].filter(Boolean)
  )

  // --- The four measurements ------------------------------------------------------
  const isotopeMeasure = computed(() =>
    isotopeTile({
      family: familyRows.value,
      missing: missingLines.value,
      abundanceError: evidenceRow.value?.abundance_error ?? null
    })
  )
  // The samples the batch peak is in: a derived row's anchor names them, and
  // the persistence admission counts them where it took the peak.
  const batchMeasure = computed(() => {
    const anchor = provenance.value?.batch_peak
    const present = anchor?.n_present ?? batchPersistence.value?.n_present ?? null
    const total = batchPersistence.value?.n_samples ?? null
    return batchTile({
      present: Number.isInteger(present) ? present : null,
      total: Number.isInteger(total) ? total : null,
      readings: batchReadingCount.value,
      inBatch: Array.isArray(batchGroups.value) || Boolean(anchor)
    })
  })
  const showMeasurements = computed(() => Boolean(focusedAssignment.value?.assigned_formula))

  // --- Notes: one line and its action ---------------------------------------------
  // The undo of an override is "use this" on the winner it displaced, the head
  // of the alternatives: the note offers that same action.
  const undoEntry = computed(() => {
    const head = alternatives.value[0]
    return head && isUndoEntry(head, 0) && canPromote(head) ? head : null
  })
  function undoOverride() {
    if (undoEntry.value) promoteAlternative(undoEntry.value, 0)
  }
  const isotopologues = (n) => `${n} isotopologue${n === 1 ? '' : 's'}`
  // The override's whole sentence, which the one-line note carries on hover.
  const manualSentence = computed(() => {
    const manual = manualOverride.value
    if (manualDemoted.value) {
      const owner = demotedOwnerFormula.value
      return (
        'Unassigned by hand' +
        (owner
          ? `: this peak was an isotopologue of ${owner} - the same compound seen through a ` +
            'heavy atom - and was cleared when that assignment was replaced by hand on the ' +
            `compound's own peak. Assigning ${owner} there again restores this row`
          : ' when the compound this peak was an isotopologue of was replaced on the ' +
            "compound's own peak") +
        '. The next assignment run for this sample recomputes the ledger and supersedes the change.'
      )
    }
    let text = 'Assigned by hand'
    if (manual?.previous_formula) {
      text += ` in place of ${manual.previous_formula}`
      const n = demotedCount.value
      if (previousRestorable.value) {
        text += ', which is now the first close alternative - Undo puts it back'
        if (n) {
          text +=
            `, which also puts back the ${isotopologues(n)} unassigned with it, except any ` +
            'of them assigned by hand since'
        }
      } else {
        text += ', which named no adduct itself and so cannot be put back by hand'
        if (n)
          text += `, and the ${isotopologues(n)} unassigned with it ${n === 1 ? 'stays' : 'stay'} unassigned`
        text += ' - use Find other formulas for this peak to assign it again under a real adduct'
      }
    }
    return (
      text +
      '. The next assignment run for this sample recomputes the ledger and supersedes the ' +
      'change; record a verification to keep the judgement.'
    )
  })
  const manualLine = computed(() => {
    if (manualDemoted.value) {
      return demotedOwnerFormula.value
        ? `Unassigned by hand: a line of ${demotedOwnerFormula.value}`
        : 'Unassigned by hand'
    }
    const previous = manualOverride.value?.previous_formula
    return previous ? `Assigned by hand in place of ${previous}` : 'Assigned by hand'
  })
  const batchPinSentence = computed(() => {
    const pin = batchOverride.value
    if (!pin) return ''
    const previous = pin.previous?.consensus_formula
    return [
      `Curated by hand for the whole batch${previous ? ` in place of ${previous}` : ''}: every ` +
        'sample in the batch reads this identity where it could be measured.',
      measuredAtPin.value ? `Measured before the pin: ${measuredAtPin.value}.` : null,
      'Release lets the batch decide again: the samples measured for this identity go back to ' +
        'what they read before.'
    ]
      .filter(Boolean)
      .join(' ')
  })
  const batchPinLine = computed(() => {
    const previous = batchOverride.value?.previous?.consensus_formula
    return `Curated for the batch${previous ? ` in place of ${previous}` : ''}`
  })

  // Leaving a verdict being changed as it was.
  function cancelEdit() {
    editing.value = false
  }
  function toggleSearch() {
    showSearch.value = !showSearch.value
  }

  return {
    showSearch,
    app,
    curation,
    focusedAssignment,
    loading,
    viewedRun,
    notice,
    readOnly,
    readOnlyReason,
    canEdit,
    canChange,
    batchOpened,
    nextSample,
    noRowLine,
    engineTierTooltip,
    focusedDetail,
    headlineIsIon,
    peakSummary,
    verification,
    verifyTarget,
    anchorVerdict,
    anchorConflict,
    anchorVerdictLabel,
    anchorVerdictTooltip,
    verifiable,
    editing,
    evidenceLevel,
    note,
    submitting,
    pendingVerdict,
    denied,
    claimOptions,
    claim,
    canConfirm,
    showVerifyForm,
    startEdit,
    verdictDialog,
    dialogVerdict,
    dialogId,
    VERDICT_DIALOGS,
    dialogMeta,
    confirming,
    autofocusLevel,
    openVerdict,
    submitVerdict,
    submitDialog,
    provenance,
    storedAlternatives,
    altScores,
    scoring,
    scoreByFormula,
    alternatives,
    hasUnscored,
    fitPercent,
    formatFit,
    family,
    m0,
    isFamilyHead,
    m0Provenance,
    missingLines,
    corroboration,
    corroborationLabel,
    corroborationTooltip,
    batchCoVariation,
    batchCoVariationR,
    batchChecks,
    batchPersistence,
    batchPersistenceHold,
    batchPersistenceNote,
    batchFlatSeries,
    batchStamp,
    batchStampParent,
    focusStampParent,
    batchIsotopologues,
    otherReadings,
    STALE_PEAK_TOOLTIP,
    readingTooltip,
    channelsMenu,
    toggleChannels,
    focusReading,
    isoLabel,
    relativeToM0,
    theoreticalRel,
    relAbuFmt,
    formatRel,
    peakOf,
    focusIsotopePeak,
    isPoorMatch,
    altFit,
    altMzError,
    altPlausibility,
    altAdduct,
    altTooltip,
    curating,
    curateDenied,
    derivedRun,
    ledgerServed,
    LEDGER_CONFIDENCE_TOOLTIP,
    peakStore,
    sampleBatchId,
    derivedCandidates,
    candidateMeasureOf,
    measuringAlt,
    measuredKey,
    measured,
    measuring,
    measuredByPeak,
    withMeasured,
    evidenceRow,
    familyRows,
    familyRelReference,
    missingRel,
    plausibility,
    tierReasons,
    reasonsOwner,
    ownerReasons,
    identificationLevel,
    rivalTooltip,
    neighbourPeak,
    focusNeighbour,
    massZ,
    massCalibration,
    massZFar,
    EVIDENCE_TOOLTIPS,
    isoRowTooltip,
    zFormat,
    massZTooltip,
    notationById,
    channelOf,
    sameIonReadings,
    SAME_ION_TOOLTIP,
    ionization,
    ionizationTooltip,
    listedAs,
    targetAs,
    ALTERNATIVE_KINDS,
    alternativeKind,
    canPromote,
    promoteBlocked,
    RESEARCH_HINT,
    NO_ADDUCT_HINT,
    SCORING_HINT,
    NO_ADDUCT_UNDO_HINT,
    isUndoEntry,
    noAdductHint,
    promoteBody,
    promoteAlternative,
    manualOverride,
    batchOverride,
    batchOverrideMeasured,
    measuredAtPin,
    measuredCurrent,
    releasing,
    releaseOverride,
    previousRestorable,
    DEMOTE_ACTIONS,
    manualDemoted,
    demotedOwnerFormula,
    demotedCount,
    tierBands,
    whyHeading,
    evidenceValue,
    evidenceBar,
    productParts,
    productLine,
    evidenceText,
    whyReasons,
    whyOwnerReasons,
    showWhy,
    batchGroups,
    batchSeries,
    groupOf,
    memberPeak,
    isOwnMember,
    focusMember,
    batchContinuity,
    extraReadings,
    batchReadingCount,
    batchBadges,
    isotopeMeasure,
    batchMeasure,
    showMeasurements,
    undoEntry,
    undoOverride,
    manualSentence,
    manualLine,
    batchPinSentence,
    batchPinLine,
    cancelEdit,
    toggleSearch
  }
}
