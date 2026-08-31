"""
Copy a curated sample's assignments onto its batch's other samples.

One sample of a batch gets the full treatment - an engine run, inspection, and
curation - while the batch's other samples usually carry closely related
chemistry. This service propagates the curated sample's current ledger to them
without re-deriving it: per destination it **remaps** the source run's rows
onto the destination's own peaks, **re-scores** the seeded list against the
destination's data with the engine's own scoring chain, and **publishes** the
result as a complete first-class run through the import pipeline. Design and
the B1/B2 comparison: ``docs/dev/peak_assignment_copy.md`` (this is B2, the
seeded re-score; B1 - carrying the source's numbers verbatim - survives only
as the internal ``rescore=False`` degenerate mode and is not exposed).

The three stages, and what each deliberately reuses:

- **Remap** (note section 1). Source peaks are matched onto destination peaks
  on a mu-corrected axis - each sample's peaks shifted by its own median
  ``mz_error_ppm``, exactly the offset the batch fold applies before anchor
  snapping - within the same resolution-adaptive tolerance the batch anchors
  use. Where both samples are already folded, the mapping is a
  ``BatchPeakOccurrence`` join instead (source occurrence -> batch peak ->
  destination occurrence); the m/z path covers the common case of a
  destination with no run at all. Source rows with no destination peak are
  dropped, the better-scoring row wins when two land on one peak,
  isotopologue children whose owner was dropped are dropped, and every
  unmapped destination peak gets an ``unassigned`` placeholder - the batch
  fold takes a sample's whole contribution from its latest completed run, so
  publishing anything less than a complete run would silently shrink the
  sample in the batch view.

- **Re-score** (note section 3). Winners stay the curated winners; only their
  evidence is re-measured. The seeded formula x mechanism list is driven
  through the same chain Stage A scores with: ions generated per formula
  (``generate_target_ions_from_composition``), one ``compute_match_isotopes``
  pass over the destination's peak file, ``apply_match_params`` gating, then
  ``score_ions_by_fit`` - the persisted Stage-A fit scale, NOT the legacy v1
  aggregate of ``/fit/aggregate``. Each copied row takes its ion's fit and
  the mass/abundance error of the isotopologue that paired to its destination
  peak, and is re-tiered with ``tier_for_evidence`` under the source run's
  declared bands, so tier-fit coherence holds by construction. ``alternatives``
  travel verbatim: they are curation context, and their embedded fits are
  labeled by the run's copy provenance rather than re-scored.

- **Publish** (note section 5). Each destination gets a new run under the
  reserved ``mascope-copy`` engine, through ``import_assignment_run`` with the
  trusted ``allow_reserved_engine`` parameter the HTTP boundary never
  forwards. The run's ``calibration`` disclosure carries the copy manifest
  (source ids, offsets, tolerance, mapped/dropped counts), ``config`` the copy
  parameters, and every row's provenance gains ``copied_from``. Publishing is
  append-only, admission-controlled, and folds each destination into the batch
  peaks at finalize, exactly as any import does. The fan-out runs under the
  background-task decorator, whose completion emits ``peak_assignment_reload``
  to the batch room - the import channel itself emits nothing, and a copy that
  landed silently would leave every open ledger stale.

Manual overrides (note section 6) propagate mechanically because the copy
reads the source run's *current* rows and carries provenance verbatim minus
only the three server-owned keys the import strips. The row's ``source`` field
travels opaquely too, so the WP11 ``manual`` value needs no change here beyond
its own widening of the shared ``AssignmentSource`` literal. Verifications do
NOT copy: a verdict is human judgement about one sample's evidence.
"""

import asyncio
import statistics
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import select

from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    fit_sample_mass_accuracy,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import DuplicateException
from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.match.params.lib import apply_match_params
from mascope_backend.api.new.peak_assignments.admission import (
    assignment_claim,
    in_flight_run_ids,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    DEFAULT_DRIFT_MARGIN_PPM,
    resolution_adaptive_tol_ppm,
)
from mascope_backend.api.new.peak_assignments.config import (
    COPY_ENGINE,
    COPY_ENGINE_VERSION,
    MAX_IMPORT_ROWS_PER_REQUEST,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_UNASSIGNED,
    evidence_for,
    score_ions_by_fit,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.import_service import (
    abandon_import_run,
    import_assignment_run,
)
from mascope_backend.api.new.peak_assignments.schemas import (
    ImportChunk,
    ImportRunBody,
    TierBands,
)
from mascope_backend.api.new.peak_assignments.service import load_sample_peaks
from mascope_backend.api.new.peak_assignments.tiers import (
    TIER_UNASSIGNED,
    normalize_tier_bands,
)
from mascope_backend.db import (
    BatchPeakOccurrence,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    Sample,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_file.name import get_instrument_type
from mascope_match import compute_match_isotopes


# -------------------------------------------------------------------
# Eligibility partition
# -------------------------------------------------------------------


@dataclass(frozen=True)
class CopyCandidate:
    """One batch sibling of the copy source, with its eligibility verdict."""

    sample_item_id: str
    sample_item_name: str
    #: Why the fan-out will skip this sample, or None when it is eligible.
    reason: str | None


@dataclass(frozen=True)
class CopyPartition:
    """What a copy of this source would do, computed once and then executed.

    Mirrors ``BatchAssignmentPartition``: the split is computed in the request,
    reported in the response, and handed to the background task, so what the
    caller was told is what runs rather than a second evaluation of the same
    predicate at a later moment.
    """

    #: The source's latest completed run - the rows a copy would remap. None
    #: when the sample has no completed run, in which case a launch is refused
    #: but the preview still serves the partition so the dialog can say why.
    source_run_id: str | None
    source_engine: str | None
    #: The source run's declared tier bands (normalized), which the copied
    #: rows are re-tiered under and the published runs declare. None when the
    #: run predates bands, which also refuses a launch.
    tier_bands: dict | None
    #: Every other sample of the batch, in fan-out order.
    destinations: tuple[CopyCandidate, ...]

    @property
    def admitted(self) -> tuple[str, ...]:
        """Destination ids the fan-out will copy to, in order."""
        return tuple(c.sample_item_id for c in self.destinations if c.reason is None)

    def skipped_payload(self) -> list[dict]:
        """The skips as response records, one object per skipped sample."""
        return [
            {"sample_item_id": c.sample_item_id, "reason": c.reason}
            for c in self.destinations
            if c.reason is not None
        ]


#: Skip reasons, shared between the partition and the runtime re-checks so a
#: destination skipped mid-flight reports the same words the preview showed.
#: The blank wording matches ``service.ineligible_reason`` on purpose.
_REASON_BLANK = "blank sample (no peaks)"
_REASON_IN_FLIGHT = "assignment run in flight"


def _polarity_reason(destination_polarity, source_polarity) -> str:
    return (
        f"different polarity ('{destination_polarity}' vs source '{source_polarity}')"
    )


async def partition_copy_destinations(source: Sample) -> CopyPartition:
    """Split the batch's other samples into copy destinations and skips.

    Eligibility per the design note's section 1: same batch (by construction),
    same polarity as the source, not a blank, and no assignment run in flight.
    The unverified-calibration clause of the in-app eligibility rule is
    deliberately absent: a copy publishes through the import channel, which
    replaces that gate with the calibration disclosure on the run - here the
    copy manifest with its per-sample axis offsets.

    Eligible destinations are re-guarded at execution time anyway - import
    admission refuses a sample whose run state changed after this partition -
    so a stale verdict degrades to a reported failure, never a double run.

    :param source: The curated sample whose assignments would be copied.
    :return: The partition, in the order the fan-out visits destinations.
    """
    async with async_session() as session:
        run = (
            await session.execute(
                select(PeakAssignmentRun)
                .where(
                    PeakAssignmentRun.sample_item_id == source.sample_item_id,
                    PeakAssignmentRun.status == "completed",
                )
                .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        siblings = (
            (
                await session.execute(
                    select(Sample)
                    .where(
                        Sample.sample_batch_id == source.sample_batch_id,
                        Sample.sample_item_id != source.sample_item_id,
                    )
                    # Deterministic order, same tiebreak as the batch assign
                    # partition, so progress and re-runs visit samples stably.
                    .order_by(Sample.sample_item_name, Sample.sample_item_id)
                )
            )
            .scalars()
            .all()
        )

    in_flight = await in_flight_run_ids([s.sample_item_id for s in siblings])

    candidates: list[CopyCandidate] = []
    for sibling in siblings:
        if sibling.polarity != source.polarity:
            reason = _polarity_reason(sibling.polarity, source.polarity)
        elif sibling.instrument_function_id is None:
            reason = _REASON_BLANK
        elif sibling.sample_item_id in in_flight:
            reason = _REASON_IN_FLIGHT
        else:
            reason = None
        candidates.append(
            CopyCandidate(
                sample_item_id=sibling.sample_item_id,
                sample_item_name=sibling.sample_item_name,
                reason=reason,
            )
        )

    bands = normalize_tier_bands(run.tier_bands) if run is not None else None
    if bands is not None and not {"assigned", "candidate"} <= bands.keys():
        # A run predating bands (or carrying a malformed pair) cannot tier
        # copied rows; reported as no bands so the launch is refused up front.
        bands = None

    return CopyPartition(
        source_run_id=run.peak_assignment_run_id if run is not None else None,
        source_engine=run.engine if run is not None else None,
        tier_bands=bands,
        destinations=tuple(candidates),
    )


# -------------------------------------------------------------------
# Remap: source peaks onto destination peaks
# -------------------------------------------------------------------


def map_peaks_by_mz(
    source_mz_by_peak: dict[str, float],
    dest_peaks_df: pd.DataFrame,
    mu_source_ppm: float,
    mu_destination_ppm: float,
    tol_fn,
) -> dict[str, str]:
    """Match source peaks onto destination peaks on a mu-corrected axis.

    Both sides are shifted by their own median run offset (``mz * (1 -
    mu/1e6)``, the batch fold's correction) so calibration drift between the
    two samples does not push a peak out of tolerance, then each source peak
    takes the nearest destination peak within the resolution-adaptive
    tolerance at its m/z. Deliberately non-exclusive: two source peaks may map
    to one destination peak - a real merge on the destination's axis - and the
    row-level conflict rule in :func:`remap_source_rows` keeps the
    better-scoring assignment.

    :param source_mz_by_peak: Source ``sample_peak_id`` -> observed m/z.
    :param dest_peaks_df: The destination's peaks (``sample_peak_id``, ``mz``).
    :param mu_source_ppm: The source run's median ``mz_error_ppm``.
    :param mu_destination_ppm: The destination's axis offset estimate.
    :param tol_fn: ``mz -> tolerance_ppm``, the batch anchors' half-window.
    :return: Source peak id -> destination peak id, for the peaks that map.
    """
    if not source_mz_by_peak or dest_peaks_df.empty:
        return {}

    dest_ids = dest_peaks_df["sample_peak_id"].to_numpy()
    dest_mz = dest_peaks_df["mz"].to_numpy(dtype=float) * (
        1.0 - mu_destination_ppm / 1e6
    )
    order = np.argsort(dest_mz, kind="stable")
    sorted_mz = dest_mz[order]
    source_factor = 1.0 - mu_source_ppm / 1e6

    mapping: dict[str, str] = {}
    for peak_id, mz in source_mz_by_peak.items():
        predicted = float(mz) * source_factor
        tol = predicted * float(tol_fn(predicted)) * 1e-6
        lo = int(np.searchsorted(sorted_mz, predicted - tol, side="left"))
        hi = int(np.searchsorted(sorted_mz, predicted + tol, side="right"))
        if lo >= hi:
            continue
        window = sorted_mz[lo:hi]
        best = lo + int(np.argmin(np.abs(window - predicted)))
        mapping[peak_id] = str(dest_ids[order[best]])
    return mapping


def remap_source_rows(
    source_rows: list[dict],
    peak_map: dict[str, str],
) -> tuple[list[tuple[dict, str]], dict[str, str], dict[str, int]]:
    """Apply the note's drop rules to the mapped rows.

    Three rules, in order: a row whose source peak found no destination peak
    is dropped; when two rows land on one destination peak the better-scoring
    one (by source fit, the only score that exists at remap time) is kept; an
    isotopologue child whose owner did not survive is dropped, because an
    owner link must name a row of the same run. A child with no owner at all
    (routine engine output for an ion whose M0 peak went to another compound)
    is kept - it was never attached to anything that could be dropped.

    :param source_rows: The source run's assignment rows (no ``unassigned``
        placeholders - those are rebuilt for the destination's own peaks).
    :param peak_map: Source peak id -> destination peak id.
    :return: ``(kept, owner_destination_peaks, counts)``: the surviving rows
        each paired with its destination peak id; for each surviving child,
        its owner's destination peak id keyed by the child's source assignment
        id; and the drop counters for the manifest.
    """
    candidates = [
        (row, peak_map[row["sample_peak_id"]])
        for row in source_rows
        if row["sample_peak_id"] in peak_map
    ]
    dropped_no_peak = len(source_rows) - len(candidates)

    by_destination: dict[str, list[dict]] = {}
    for row, dest_peak_id in candidates:
        by_destination.setdefault(dest_peak_id, []).append(row)

    winners: list[tuple[dict, str]] = []
    dropped_conflict = 0
    for dest_peak_id, contenders in by_destination.items():
        contenders.sort(
            key=lambda r: (
                -(r["fit_score"] if r["fit_score"] is not None else -1.0),
                r["sample_peak_id"],
            )
        )
        winners.append((contenders[0], dest_peak_id))
        dropped_conflict += len(contenders) - 1

    surviving = {row["peak_assignment_id"] for row, _ in winners}
    destination_by_assignment = {
        row["peak_assignment_id"]: dest_peak_id for row, dest_peak_id in winners
    }

    kept: list[tuple[dict, str]] = []
    owner_destination: dict[str, str] = {}
    dropped_orphaned = 0
    for row, dest_peak_id in winners:
        owner_id = row.get("owner_peak_assignment_id")
        if (
            row["role"] == ROLE_ISO_CHILD
            and owner_id is not None
            and owner_id not in surviving
        ):
            dropped_orphaned += 1
            continue
        kept.append((row, dest_peak_id))
        if row["role"] == ROLE_ISO_CHILD and owner_id in destination_by_assignment:
            owner_destination[row["peak_assignment_id"]] = destination_by_assignment[
                owner_id
            ]

    counts = {
        "source_rows": len(source_rows),
        "mapped": len(kept),
        "dropped_no_destination_peak": dropped_no_peak,
        "dropped_peak_conflicts": dropped_conflict,
        "dropped_orphaned_isotopologues": dropped_orphaned,
    }
    return kept, owner_destination, counts


async def _occurrence_peak_map(
    source_sample_item_id: str, destination_sample_item_id: str
) -> dict[str, str]:
    """The occurrence fast-path: map peaks through shared batch anchors.

    For a destination that has already folded into the batch peaks, the
    cross-sample identity question is answered by the anchors themselves: one
    join from the source's occurrence over ``batch_peak_id`` to the
    destination's. Peak ids are properties of each sample's peak file, not of
    a run, so the mapping holds whichever runs happened to fold. It cannot be
    the primary mechanism - occurrences exist only once a completed run has
    folded, and the common destination has no run at all - so the m/z path
    covers the rest.

    :param source_sample_item_id: The copy source.
    :param destination_sample_item_id: The copy destination.
    :return: Source peak id -> destination peak id, for anchors both share.
    """
    source_occ = BatchPeakOccurrence.__table__.alias("source_occ")
    dest_occ = BatchPeakOccurrence.__table__.alias("dest_occ")
    async with async_session() as session:
        rows = (
            await session.execute(
                select(source_occ.c.sample_peak_id, dest_occ.c.sample_peak_id)
                .select_from(source_occ)
                .join(
                    dest_occ,
                    dest_occ.c.batch_peak_id == source_occ.c.batch_peak_id,
                )
                .where(
                    source_occ.c.sample_item_id == source_sample_item_id,
                    dest_occ.c.sample_item_id == destination_sample_item_id,
                )
            )
        ).all()
    return {str(source_peak): str(dest_peak) for source_peak, dest_peak in rows}


async def _destination_run_mz_errors(sample_item_id: str) -> list[float]:
    """The destination's latest completed run's mass errors, for its mu.

    :param sample_item_id: The destination sample.
    :return: The run's non-null ``mz_error_ppm`` values, or empty when the
        sample has no completed run.
    """
    async with async_session() as session:
        run_id = (
            await session.execute(
                select(PeakAssignmentRun.peak_assignment_run_id)
                .where(
                    PeakAssignmentRun.sample_item_id == sample_item_id,
                    PeakAssignmentRun.status == "completed",
                )
                .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if run_id is None:
            return []
        errors = (
            (
                await session.execute(
                    select(PeakAssignment.mz_error_ppm).where(
                        PeakAssignment.peak_assignment_run_id == run_id,
                        PeakAssignment.mz_error_ppm.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    return [float(e) for e in errors]


async def _destination_tolerance_fn(filename: str):
    """The resolution-adaptive tolerance at the destination's resolution.

    Same construction as the batch fold's: half the peak FWHM at each m/z plus
    the calibration-drift margin, falling back to the margin alone when the
    file carries no resolution function.

    :param filename: The destination sample's file.
    :return: ``mz -> tolerance_ppm``.
    """
    try:
        _, resolution_func = await read_instrument_functions(filename)
    except Exception as exc:  # noqa: BLE001 - resolution is best-effort
        runtime.logger.debug(
            f"No resolution function for '{filename}' ({exc}); copy mapping "
            "tolerance falls back to the drift margin."
        )
        resolution_func = None

    def tol_fn(mz: float) -> float:
        resolution = None
        if resolution_func is not None:
            try:
                resolution = float(resolution_func(mz))
            except Exception:  # noqa: BLE001 - fall back per point
                resolution = None
        return resolution_adaptive_tol_ppm(mz, resolution)

    return tol_fn


# -------------------------------------------------------------------
# Seeded re-score: the engine's own chain over the copied list
# -------------------------------------------------------------------


def _finite_or_none(value) -> float | None:
    """Coerce to a finite float, else None (NaN never reaches a stored row)."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _score_or_none(value) -> float | None:
    """Coerce a fit score to a finite float clamped to [0, 1], else None."""
    score = _finite_or_none(value)
    if score is None:
        return None
    return min(1.0, max(0.0, score))


def _build_seeded_isotopes_df(
    seeds: set[tuple[str, str]],
    mechanisms_by_id: dict[str, SimpleNamespace],
    resolution_type: str,
    abundance_threshold: float,
) -> tuple[pd.DataFrame, dict[tuple[str, str], str]]:
    """Expand the copied formula x mechanism list into a Stage-A isotope frame.

    The same target-ion/IsoSpec path the curated library and the reference
    mirror use, shaped like ``_fetch_known_target_isotopes`` output so
    ``compute_match_isotopes`` and the fit scorer consume it unchanged. Ion
    and isotope ids are synthetic, used only to group the scored frame back to
    seeds - they are never persisted. A formula that fails to generate is
    skipped (its rows re-score to no evidence), never fails the copy.

    :param seeds: Distinct ``(assigned_formula, ionization_mechanism_id)``
        pairs from the source rows.
    :param mechanisms_by_id: The mechanisms those pairs name.
    :param resolution_type: "LOW" (TOF) or "HIGH", the destination's.
    :param abundance_threshold: Minimum relative abundance to participate,
        the destination's match-params floor as in Stage A.
    :return: The seeded isotope frame, and seed pair -> synthetic ion id.
    """
    mechanism_ids_by_formula: dict[str, list[str]] = {}
    for formula, mechanism_id in seeds:
        mechanism_ids_by_formula.setdefault(formula, []).append(mechanism_id)

    rows: list[dict] = []
    ion_by_seed: dict[tuple[str, str], str] = {}
    for formula, mechanism_ids in sorted(mechanism_ids_by_formula.items()):
        mechanisms = [
            mechanisms_by_id[mechanism_id]
            for mechanism_id in mechanism_ids
            if mechanism_id in mechanisms_by_id
        ]
        if not mechanisms:
            continue
        compound = SimpleNamespace(
            target_compound_id="copy-seed",
            target_compound_formula=formula,
        )
        try:
            ions, isotopes = generate_target_ions_from_composition(compound, mechanisms)
        except Exception as error:  # noqa: BLE001 - a bad formula skips, never fails
            runtime.logger.debug(f"Skipping copy seed formula '{formula}': {error}")
            continue
        ion_by_id = {ion.target_ion_id: ion for ion in ions}
        for ion in ions:
            ion_by_seed[(formula, ion.ionization_mechanism_id)] = ion.target_ion_id
        for iso in isotopes:
            if iso.resolution != resolution_type:
                continue
            if iso.relative_abundance < abundance_threshold:
                continue
            ion = ion_by_id.get(iso.target_ion_id)
            if ion is None:
                continue
            mechanism = mechanisms_by_id.get(ion.ionization_mechanism_id)
            rows.append(
                {
                    "target_isotope_id": iso.target_isotope_id,
                    "target_ion_id": iso.target_ion_id,
                    "target_isotope_formula": iso.target_isotope_formula,
                    "mz": iso.mz,
                    "relative_abundance": iso.relative_abundance,
                    "resolution": iso.resolution,
                    "target_ion_formula": ion.target_ion_formula,
                    "ionization_mechanism_id": ion.ionization_mechanism_id,
                    # The seed is the copied row's own formula, not a curated
                    # target; the copied row keeps its original target FKs.
                    "target_compound_id": None,
                    "target_compound_formula": formula,
                    "ionization_mechanism": (
                        mechanism.ionization_mechanism if mechanism else None
                    ),
                    "ionization_mechanism_polarity": (
                        mechanism.ionization_mechanism_polarity if mechanism else None
                    ),
                }
            )
    return pd.DataFrame(rows), ion_by_seed


async def _fetch_mechanisms_by_id(
    mechanism_ids: set[str],
) -> dict[str, SimpleNamespace]:
    """Resolve mechanism rows for the seeded generation, detached.

    :param mechanism_ids: The mechanism ids the source rows name.
    :return: Mechanism id -> detached namespace (id, notation, polarity).
    """
    if not mechanism_ids:
        return {}
    async with async_session() as session:
        mechanisms = (
            (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism_id.in_(
                            tuple(mechanism_ids)
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    return {
        m.ionization_mechanism_id: SimpleNamespace(
            ionization_mechanism_id=m.ionization_mechanism_id,
            ionization_mechanism=m.ionization_mechanism,
            ionization_mechanism_polarity=m.ionization_mechanism_polarity,
        )
        for m in mechanisms
    }


def _rescore_maps(
    scored_df: pd.DataFrame,
) -> tuple[dict[str, float | None], dict[tuple[str, str], dict]]:
    """Index the scored frame for the per-row evidence lookup.

    The fit is ion-level - after ``score_ions_by_fit`` every isotopologue of
    an ion carries the ion's consolidated fit - while the mass and abundance
    errors are per isotopologue, read off the row that paired to the copied
    row's destination peak. A copied row whose destination peak no scored
    isotopologue paired to gets no errors (None): the envelope's evidence for
    that peak was measured elsewhere or not at all, and inventing a number
    here would be exactly the source-sample arithmetic B1 was rejected for.

    :param scored_df: The gated, fit-scored match frame.
    :return: ``(fit by ion id, per (ion id, destination peak id) errors)``.
    """
    if scored_df.empty or "target_ion_id" not in scored_df.columns:
        return {}, {}

    fit_by_ion: dict[str, float | None] = {}
    for ion_id, group in scored_df.groupby("target_ion_id", sort=False):
        fit_by_ion[str(ion_id)] = _score_or_none(group["match_score"].iloc[0])

    paired = scored_df[
        scored_df["sample_peak_id"].notna() & (scored_df["sample_peak_id"] != "")
    ]
    # Two isotopologues of one ion can pair to the same peak in a crowded
    # window; the more abundant one is the honest evidence for that peak.
    paired = paired.sort_values("relative_abundance", ascending=False)
    errors: dict[tuple[str, str], dict] = {}
    for row in paired.itertuples(index=False):
        key = (str(row.target_ion_id), str(row.sample_peak_id))
        if key in errors:
            continue
        # The matcher writes -1.0 as its no-value TOF sentinel and a real time
        # of flight is positive, so only a positive value is carried.
        tof = _finite_or_none(getattr(row, "sample_peak_tof", None))
        errors[key] = {
            "mz_error_ppm": _finite_or_none(getattr(row, "match_mz_error", None)),
            "abundance_error": _finite_or_none(
                getattr(row, "match_abundance_error", None)
            ),
            "sample_peak_tof": tof if tof is not None and tof > 0 else None,
        }
    return fit_by_ion, errors


# -------------------------------------------------------------------
# Row assembly
# -------------------------------------------------------------------


def build_copied_rows(
    kept: list[tuple[dict, str]],
    owner_destination: dict[str, str],
    dest_peaks: dict[str, tuple[float, float]],
    ion_by_seed: dict[tuple[str, str], str],
    fit_by_ion: dict[str, float | None],
    errors_by_pairing: dict[tuple[str, str], dict],
    bands: dict,
    source_sample_item_id: str,
    rescore: bool,
) -> list[dict]:
    """Shape the surviving source rows into import rows for the destination.

    Formulas, roles, isotope labels, target references, ``alternatives`` and
    provenance travel verbatim; the peak identity (id, m/z, intensity) becomes
    the destination's observed values; and under B2 the evidence columns are
    replaced by the seeded re-score with the tier recomputed under the
    declared bands. Provenance gains ``copied_from`` with the source ids and
    the source fit for reference; the import's own strip removes the
    server-owned confidence keys.

    :param kept: Surviving ``(source row, destination peak id)`` pairs.
    :param owner_destination: Child source assignment id -> its owner's
        destination peak id, for the import's owner reference.
    :param dest_peaks: Destination peak id -> ``(mz, intensity)``.
    :param ion_by_seed: Seed pair -> synthetic ion id in the scored frame.
    :param fit_by_ion: Ion id -> destination fit score.
    :param errors_by_pairing: ``(ion id, destination peak id)`` -> errors.
    :param bands: The declared tier bands the rows are re-tiered under.
    :param source_sample_item_id: For each row's ``copied_from``.
    :param rescore: True for B2; False carries the source numbers (B1, the
        internal degenerate mode).
    :return: Import row dicts, ordered by destination m/z.
    """
    rows: list[dict] = []
    for source_row, dest_peak_id in kept:
        dest_mz, dest_intensity = dest_peaks[dest_peak_id]
        ion_id = ion_by_seed.get(
            (source_row["assigned_formula"], source_row["ionization_mechanism_id"])
        )
        if rescore:
            fit_score = fit_by_ion.get(ion_id) if ion_id is not None else None
            errors = (
                errors_by_pairing.get((ion_id, dest_peak_id))
                if ion_id is not None
                else None
            ) or {}
            mz_error_ppm = errors.get("mz_error_ppm")
            abundance_error = errors.get("abundance_error")
            sample_peak_tof = errors.get("sample_peak_tof")
            # The formula is carried over from the source row, so only the fit is
            # re-measured here; plausibility is a property of that formula and is
            # the same on either sample. Evidence is therefore the destination's
            # fit against the copied formula's own plausibility.
            tier = tier_for_evidence(
                evidence_for(fit_score, source_row["assigned_formula"]),
                candidate_threshold=bands["candidate"],
                assigned_threshold=bands["assigned"],
            )
        else:
            fit_score = _score_or_none(source_row["fit_score"])
            mz_error_ppm = _finite_or_none(source_row["mz_error_ppm"])
            abundance_error = _finite_or_none(source_row["abundance_error"])
            sample_peak_tof = None
            tier = source_row["tier"]

        provenance = dict(source_row.get("provenance") or {})
        provenance["copied_from"] = {
            "sample_item_id": source_sample_item_id,
            "sample_peak_id": source_row["sample_peak_id"],
            "peak_assignment_id": source_row["peak_assignment_id"],
            "fit_score": _score_or_none(source_row["fit_score"]),
        }

        rows.append(
            {
                "sample_peak_id": dest_peak_id,
                "sample_peak_mz": dest_mz,
                "sample_peak_intensity": dest_intensity,
                "sample_peak_tof": sample_peak_tof,
                "role": source_row["role"],
                "assigned_formula": source_row["assigned_formula"],
                "ion_formula": source_row["ion_formula"],
                "ionization_mechanism_id": source_row["ionization_mechanism_id"],
                "isotope_label": source_row["isotope_label"],
                "isotope_formula": source_row["isotope_formula"],
                "source": source_row["source"],
                "fit_score": fit_score,
                "mz_error_ppm": mz_error_ppm,
                "abundance_error": abundance_error,
                "tier": tier,
                "target_compound_id": source_row["target_compound_id"],
                "target_ion_id": source_row["target_ion_id"],
                "owner_sample_peak_id": owner_destination.get(
                    source_row["peak_assignment_id"]
                ),
                "alternatives": source_row.get("alternatives"),
                "provenance": provenance,
            }
        )
    rows.sort(key=lambda r: (r["sample_peak_mz"], r["sample_peak_id"]))
    return rows


def build_placeholder_rows(
    dest_peaks_df: pd.DataFrame, claimed_peak_ids: set[str]
) -> list[dict]:
    """Placeholder rows for the destination peaks no copied row landed on.

    One row per destination peak is what makes the published run complete: the
    batch fold takes a sample's whole contribution from its latest completed
    run, so a partial run would silently withdraw the unmapped peaks from the
    batch view. Mirrors the engine's ``build_unassigned_assignments``.

    :param dest_peaks_df: The destination's peaks.
    :param claimed_peak_ids: Peaks a copied row already owns.
    :return: Import row dicts for the unassigned remainder.
    """
    return [
        {
            "sample_peak_id": str(row.sample_peak_id),
            "sample_peak_mz": float(row.mz),
            "sample_peak_intensity": float(row.intensity),
            "sample_peak_tof": None,
            "role": ROLE_UNASSIGNED,
            "assigned_formula": None,
            "ion_formula": None,
            "ionization_mechanism_id": None,
            "isotope_label": None,
            "isotope_formula": None,
            "source": None,
            "fit_score": None,
            "mz_error_ppm": None,
            "abundance_error": None,
            "tier": TIER_UNASSIGNED,
            "target_compound_id": None,
            "target_ion_id": None,
            "owner_sample_peak_id": None,
            "alternatives": None,
            "provenance": None,
        }
        for row in dest_peaks_df.itertuples(index=False)
        if str(row.sample_peak_id) not in claimed_peak_ids
    ]


# -------------------------------------------------------------------
# Publish: one complete run per destination, through the import pipeline
# -------------------------------------------------------------------


async def _publish_copied_run(
    destination_sample_item_id: str,
    rows: list[dict],
    bands: dict,
    manifest: dict,
    config: dict,
    user_id: int | None,
) -> str:
    """Publish one destination's copied run through the import service.

    Chunked exactly as an external client would chunk, under a fresh
    ``import_id`` per copy (re-copying after further curation is simply
    another publish). Publishing through the pipeline rather than inserting
    rows is the point: validation, admission, attribution, fold-in and
    retention all apply to a copy exactly as to any import.

    A failure mid-assembly abandons the partial run best-effort - a stranded
    ``importing`` run would block the destination against every later run
    until retention's grace - and the original error propagates.

    :param destination_sample_item_id: The destination sample.
    :param rows: The complete run's import row dicts.
    :param bands: The source run's tier bands, declared on the new run.
    :param manifest: The copy manifest, disclosed as the run's calibration.
    :param config: The copy parameters, stored as the run's config.
    :param user_id: The launching user, for the import's log line.
    :return: The published run's id.
    """
    import_id = f"copy-{gen_id(16)}"
    run_id: str | None = None
    sent = 0
    try:
        while sent < len(rows):
            chunk_rows = rows[sent : sent + MAX_IMPORT_ROWS_PER_REQUEST]
            body = ImportRunBody(
                engine=COPY_ENGINE,
                engine_version=COPY_ENGINE_VERSION,
                tier_bands=TierBands(**bands),
                calibration=manifest,
                config=config,
                rows=chunk_rows,
                chunk=ImportChunk(
                    run_id=run_id,
                    import_id=import_id,
                    index=sent,
                    complete=sent + len(chunk_rows) >= len(rows),
                ),
            )
            result = await import_assignment_run(
                sample_item_id=destination_sample_item_id,
                body=body,
                user_id=user_id,
                allow_reserved_engine=COPY_ENGINE,
            )
            run_id = result["data"][0]["peak_assignment_run_id"]
            sent += len(chunk_rows)
    except Exception:
        if run_id is not None:
            try:
                await abandon_import_run(destination_sample_item_id, run_id)
            except Exception as cleanup_error:  # noqa: BLE001 - best-effort
                runtime.logger.warning(
                    f"Could not abandon partial copied run '{run_id}' for "
                    f"sample '{destination_sample_item_id}': {cleanup_error}"
                )
        raise
    return run_id


# -------------------------------------------------------------------
# Per-destination pipeline
# -------------------------------------------------------------------


def _gate_and_score(match_isotope_df: pd.DataFrame, match_params) -> pd.DataFrame:
    """Gate and fit-score the seeded match frame, exactly as Stage A does."""
    gated = apply_match_params(match_isotope_df, match_params)
    return score_ions_by_fit(gated)


async def _seeded_rescore(
    destination: Sample, source_rows: list[dict], match_params
) -> tuple[
    dict[tuple[str, str], str],
    dict[str, float | None],
    dict[tuple[str, str], dict],
    tuple[float, float | None],
]:
    """Measure the copied formulas against the destination's own peaks.

    The engine's Stage-A chain over a seeded frame and nothing more: ions for
    the copied formula x mechanism list, one ``compute_match_isotopes`` pass
    (a single peak-file load covers every formula), ``apply_match_params``
    gating, ``score_ions_by_fit``. No candidate enumeration, no untargeted
    stage, no re-arbitration - the winners are already decided.

    :param destination: The sample being scored against.
    :param source_rows: The rows being copied, which name the seeds.
    :param match_params: The destination's resolved match parameters.
    :return: ``(ion by seed, fit by ion, errors by pairing, (mu, sigma))``.
        The mass-accuracy pair is the fit over the seeded frame; sigma is
        None when fewer than the scorer's minimum anchors matched, which the
        run's disclosure reports (design note, open question 2).
    """
    seeds = {
        (row["assigned_formula"], row["ionization_mechanism_id"])
        for row in source_rows
        if row["assigned_formula"] and row["ionization_mechanism_id"]
    }
    if not seeds:
        return {}, {}, {}, (0.0, None)

    mechanisms_by_id = await _fetch_mechanisms_by_id(
        {mechanism_id for _, mechanism_id in seeds}
    )
    resolution_type = (
        "LOW" if get_instrument_type(destination.filename) == "tof" else "HIGH"
    )
    seeded_df, ion_by_seed = await asyncio.to_thread(
        _build_seeded_isotopes_df,
        seeds,
        mechanisms_by_id,
        resolution_type,
        match_params.isotope_abundance_threshold,
    )
    if seeded_df.empty:
        return ion_by_seed, {}, {}, (0.0, None)

    matched_df = await compute_match_isotopes(
        filename=destination.filename,
        target_isotopes_df=seeded_df,
        polarity=destination.polarity,
    )
    if matched_df.empty:
        return ion_by_seed, {}, {}, (0.0, None)

    scored_df = await asyncio.to_thread(_gate_and_score, matched_df, match_params)
    fit_by_ion, errors_by_pairing = _rescore_maps(scored_df)
    mass_accuracy = (
        fit_sample_mass_accuracy(scored_df) if not scored_df.empty else (0.0, None)
    )
    return ion_by_seed, fit_by_ion, errors_by_pairing, mass_accuracy


async def _copy_to_destination(
    source: Sample,
    source_run: PeakAssignmentRun,
    source_rows: list[dict],
    mu_source_ppm: float,
    bands: dict,
    destination_sample_item_id: str,
    rescore: bool,
    user_id: int | None,
) -> dict:
    """Remap, re-score and publish the source's rows onto one destination.

    :param source: The copy source sample.
    :param source_run: The source's completed run being copied.
    :param source_rows: Its current assignment rows (placeholders excluded).
    :param mu_source_ppm: The source run's median mass error, computed once.
    :param bands: The source run's normalized tier bands.
    :param destination_sample_item_id: The destination sample.
    :param rescore: True for the seeded re-score (B2); False for the internal
        literal mode (B1).
    :param user_id: The launching user.
    :return: A per-destination outcome record for the fan-out report.
    """
    destination = await fetch_sample(destination_sample_item_id)
    outcome = {
        "sample_item_id": destination_sample_item_id,
        "sample_item_name": destination.sample_item_name,
    }

    # Re-checked here because the partition may be minutes old by the time the
    # fan-out reaches this destination; the import's own validation backstops
    # both anyway (a blank is refused, a wrong-polarity mechanism is a 422).
    if destination.polarity != source.polarity:
        return {
            **outcome,
            "status": "skipped",
            "reason": _polarity_reason(destination.polarity, source.polarity),
        }
    if destination.instrument_function_id is None:
        return {**outcome, "status": "skipped", "reason": _REASON_BLANK}

    dest_peaks_df = await asyncio.to_thread(load_sample_peaks, destination)
    if dest_peaks_df.empty:
        return {
            **outcome,
            "status": "skipped",
            "reason": "peak file holds no peaks",
        }

    # -- Seeded re-score first: it needs no mapping, and its fitted offset is
    # what estimates a run-less destination's axis offset for the remap.
    match_params = await default_match_params(destination_sample_item_id)
    ion_by_seed: dict[tuple[str, str], str] = {}
    fit_by_ion: dict[str, float | None] = {}
    errors_by_pairing: dict[tuple[str, str], dict] = {}
    fitted_mu, fitted_sigma = 0.0, None
    if rescore:
        (
            ion_by_seed,
            fit_by_ion,
            errors_by_pairing,
            (fitted_mu, fitted_sigma),
        ) = await _seeded_rescore(destination, source_rows, match_params)

    # -- The destination's axis offset: its own latest run's median error
    # where a run exists, else the seeded fit's estimate (the targeted-path
    # fallback the note names), else zero. Sigma is reported for disclosure -
    # below the scorer's anchor minimum the fit returns none and the scorer
    # used its fixed fallback, which the note's open question 2 says to
    # surface rather than hide.
    destination_errors = await _destination_run_mz_errors(destination_sample_item_id)
    if destination_errors:
        mu_destination_ppm = statistics.median(destination_errors)
        mu_destination_from = "run"
    elif fitted_sigma is not None:
        mu_destination_ppm = fitted_mu
        mu_destination_from = "seeded_fit"
    else:
        mu_destination_ppm = 0.0
        mu_destination_from = "fallback_zero"

    # -- Remap: occurrence fast-path where both samples are folded, the
    # mu-corrected m/z match for everything else.
    tol_fn = await _destination_tolerance_fn(destination.filename)
    dest_peak_ids = {str(peak_id) for peak_id in dest_peaks_df["sample_peak_id"]}
    source_mz_by_peak = {
        row["sample_peak_id"]: row["sample_peak_mz"] for row in source_rows
    }
    occurrence_map = {
        source_peak: dest_peak
        for source_peak, dest_peak in (
            await _occurrence_peak_map(
                source.sample_item_id, destination_sample_item_id
            )
        ).items()
        # The peak file is the truth the import validates against; an
        # occurrence pointing at a peak a re-processed file no longer has
        # falls through to the m/z path.
        if source_peak in source_mz_by_peak and dest_peak in dest_peak_ids
    }
    unmapped = {
        peak_id: mz
        for peak_id, mz in source_mz_by_peak.items()
        if peak_id not in occurrence_map
    }
    mz_map = await asyncio.to_thread(
        map_peaks_by_mz,
        unmapped,
        dest_peaks_df,
        mu_source_ppm,
        mu_destination_ppm,
        tol_fn,
    )
    peak_map = {**mz_map, **occurrence_map}

    kept, owner_destination, counts = remap_source_rows(source_rows, peak_map)
    counts["mapped_by_occurrence"] = sum(
        1 for row, _ in kept if row["sample_peak_id"] in occurrence_map
    )

    dest_peaks = {
        str(row.sample_peak_id): (float(row.mz), float(row.intensity))
        for row in dest_peaks_df.itertuples(index=False)
    }
    copied_rows = build_copied_rows(
        kept,
        owner_destination,
        dest_peaks,
        ion_by_seed,
        fit_by_ion,
        errors_by_pairing,
        bands,
        source.sample_item_id,
        rescore,
    )
    placeholder_rows = build_placeholder_rows(
        dest_peaks_df, {row["sample_peak_id"] for row in copied_rows}
    )
    counts["unassigned_placeholders"] = len(placeholder_rows)

    manifest = {
        "copy": {
            "source_sample_item_id": source.sample_item_id,
            # Named as well as identified: the run selector's badge renders
            # "copied from <sample>", and an id there tells a reader nothing.
            # A rename afterwards leaves this stale, which is the right
            # trade - the manifest records what was true when the copy ran.
            "source_sample_item_name": source.sample_item_name,
            "source_peak_assignment_run_id": source_run.peak_assignment_run_id,
            "source_engine": source_run.engine,
            "source_engine_version": source_run.engine_version,
            "mode": "seeded_rescore" if rescore else "literal",
            "mu_source_ppm": round(mu_source_ppm, 4),
            "mu_destination_ppm": round(float(mu_destination_ppm), 4),
            "mu_destination_from": mu_destination_from,
            "sigma_ppm": round(fitted_sigma, 4) if fitted_sigma is not None else None,
            "sigma_fallback": fitted_sigma is None,
            "tolerance": {
                "kind": "resolution_adaptive",
                "drift_margin_ppm": DEFAULT_DRIFT_MARGIN_PPM,
            },
            "mapping": counts,
        },
        # The half of the disclosure contract every import owes: the
        # destination's server-side verification state at publish time.
        "destination_mz_calibration": destination.mz_calibration,
    }
    config = {
        "mode": "seeded_rescore" if rescore else "literal",
        "source_sample_item_id": source.sample_item_id,
        "source_peak_assignment_run_id": source_run.peak_assignment_run_id,
    }

    run_id = await _publish_copied_run(
        destination_sample_item_id,
        copied_rows + placeholder_rows,
        bands,
        manifest,
        config,
        user_id,
    )
    runtime.logger.info(
        f"Copied assignment run '{source_run.peak_assignment_run_id}' of sample "
        f"'{source.sample_item_name}' onto '{destination.sample_item_name}' as "
        f"run '{run_id}': {counts['mapped']} of {counts['source_rows']} rows "
        f"mapped, {len(placeholder_rows)} unassigned."
    )
    return {
        **outcome,
        "status": "copied",
        "peak_assignment_run_id": run_id,
        "rows": len(copied_rows) + len(placeholder_rows),
        "mapping": counts,
    }


# -------------------------------------------------------------------
# The fan-out
# -------------------------------------------------------------------

# Batches with a copy fan-out in flight in this worker. The advisory claim
# below extends the same refusal across workers; this answers the
# double-clicked dialog without a round trip (mirrors batch.py).
_copy_fanouts_in_flight: set[str] = set()


async def _source_run_and_rows(
    source_run_id: str,
) -> tuple[PeakAssignmentRun | None, list[dict]]:
    """The source run and its current rows, read at execution time.

    Read fresh rather than carried from the request: the copy propagates the
    run's *current* rows, which is what makes a curation write that lands
    between the response and the fan-out travel rather than be lost.
    Placeholder rows are excluded - destinations get placeholders rebuilt for
    their own peaks.

    :param source_run_id: The run the partition promised to copy.
    :return: ``(run, assignment rows)``; run is None when it no longer exists.
    """
    async with async_session() as session:
        run = await session.get(PeakAssignmentRun, source_run_id)
        if run is None or run.status != "completed":
            return None, []
        rows = (
            (
                await session.execute(
                    select(PeakAssignment).where(
                        PeakAssignment.peak_assignment_run_id == source_run_id,
                        PeakAssignment.role != ROLE_UNASSIGNED,
                    )
                )
            )
            .scalars()
            .all()
        )
        detached = [row.to_dict() for row in rows]
        session.expunge_all()
    return run, detached


async def _already_running_result(sample_batch_id: str, source_name: str) -> dict:
    """Refusal payload for a batch whose copy fan-out is already in flight."""
    message = (
        f"An assignment copy or batch assignment is already running for the "
        f"batch of sample '{source_name}'."
    )
    runtime.logger.info(message)
    return {
        "status": "skipped",
        "message": message,
        "data": {"copied_count": 0, "failed_count": 0, "skipped_count": 0},
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


def _fanout_result(
    sample_batch_id: str,
    source_name: str,
    outcomes: list[dict],
) -> dict:
    """Aggregate per-destination outcomes into the fan-out's report.

    The counts follow the batch assign's status mapping so the notification
    severity means the same thing on both surfaces.
    """
    copied = [o for o in outcomes if o["status"] == "copied"]
    failed = [o for o in outcomes if o["status"] == "failed"]
    skipped = [o for o in outcomes if o["status"] == "skipped"]

    if failed and not copied:
        status = "failed"
    elif failed:
        status = "partial"
    elif not copied and skipped:
        status = "skipped"
    else:
        status = "success"

    message = (
        f"Copied assignments from sample '{source_name}' to {len(copied)} of "
        f"{len(outcomes)} batch sample{'s' if len(outcomes) != 1 else ''} "
        f"({len(skipped)} skipped, {len(failed)} failed)."
    )
    runtime.logger.info(message)
    return {
        "status": status,
        "message": message,
        "data": {
            "copied_count": len(copied),
            "failed_count": len(failed),
            "skipped_count": len(skipped),
            "outcomes": outcomes,
        },
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
    error_reload=[("peak_assignment", "sample_batch_id")],
)
async def copy_assignments_to_batch(
    sample_item_id: str,
    partition: CopyPartition | None = None,
    rescore: bool = True,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Copy the source sample's assignments onto its batch's other samples.

    One destination at a time, each an independent remap -> re-score ->
    publish whose failure is isolated and reported rather than aborting the
    rest - the batch assign's failure model. N sequential publishes mean N
    sequential batch fold-ins; the per-batch fold lock serializes them safely,
    and deferring consensus to one pass is the note's documented follow-up if
    whole-batch copies become routine (open question 1), not v1.

    Holds the batch-level assignment claim for the duration, so a copy and a
    batch assignment of the same batch refuse each other instead of
    interleaving their per-sample admissions; per-destination run state guards
    everything finer-grained.

    :param sample_item_id: The source sample.
    :param partition: The eligibility split the request reported, executed
        as promised. Computed here when omitted (a caller with nobody to
        report to).
    :param rescore: True runs the seeded re-score (B2, the only exposed
        mode); False carries the source's numbers verbatim (B1), kept as the
        internal degenerate mode the design note describes.
    :param independent_transaction: Notification/reload handling flag for the
        background-task decorator.
    :param user_id: The launching user.
    :param process_id: Process identifier for progress tracking.
    :param parent_id: Parent process identifier.
    :return: The fan-out outcome with per-destination results.
    """
    source = await fetch_sample(sample_item_id)
    sample_batch_id = source.sample_batch_id
    if sample_batch_id is None:
        # The route refuses this up front; guarded here too so a direct caller
        # cannot claim a nonsense batch key or fan out over batchless samples.
        message = (
            f"Sample '{source.sample_item_name}' does not belong to a sample "
            "batch, so there is nothing to copy its assignments to."
        )
        runtime.logger.warning(message)
        return {
            "status": "failed",
            "message": message,
            "data": {"copied_count": 0, "failed_count": 0, "skipped_count": 0},
            "_notification_data": {},
        }

    if sample_batch_id in _copy_fanouts_in_flight:
        return await _already_running_result(sample_batch_id, source.sample_item_name)
    _copy_fanouts_in_flight.add(sample_batch_id)
    try:
        async with assignment_claim("batch", sample_batch_id) as acquired:
            if not acquired:
                return await _already_running_result(
                    sample_batch_id, source.sample_item_name
                )
            return await _run_copy_fanout(
                source=source,
                partition=partition,
                rescore=rescore,
                user_id=user_id,
                process_id=process_id,
                parent_id=parent_id,
            )
    finally:
        _copy_fanouts_in_flight.discard(sample_batch_id)


async def _run_copy_fanout(
    source: Sample,
    partition: CopyPartition | None,
    rescore: bool,
    user_id: int | None,
    process_id: str | None,
    parent_id: str | None,
) -> dict:
    """Body of the fan-out, run with the batch claim already held."""
    partition = partition or await partition_copy_destinations(source)

    if partition.source_run_id is None:
        message = (
            f"Sample '{source.sample_item_name}' has no completed assignment "
            "run to copy."
        )
        runtime.logger.warning(message)
        return {
            "status": "failed",
            "message": message,
            "data": {"copied_count": 0, "failed_count": 0, "skipped_count": 0},
            "_notification_data": {"sample_batch_id": source.sample_batch_id},
        }

    source_run, source_rows = await _source_run_and_rows(partition.source_run_id)
    bands = normalize_tier_bands(source_run.tier_bands) if source_run else None
    if bands is not None and not {"assigned", "candidate"} <= bands.keys():
        bands = None
    if source_run is None or not source_rows or not bands:
        detail = (
            "no longer exists or is not completed"
            if source_run is None
            else (
                "holds no assignment rows"
                if not source_rows
                else "declares no tier bands, so copied rows cannot be tiered"
            )
        )
        message = (
            f"Cannot copy assignments of sample '{source.sample_item_name}': "
            f"run '{partition.source_run_id}' {detail}."
        )
        runtime.logger.warning(message)
        return {
            "status": "failed",
            "message": message,
            "data": {"copied_count": 0, "failed_count": 0, "skipped_count": 0},
            "_notification_data": {"sample_batch_id": source.sample_batch_id},
        }

    source_errors = [
        row["mz_error_ppm"] for row in source_rows if row["mz_error_ppm"] is not None
    ]
    mu_source_ppm = statistics.median(source_errors) if source_errors else 0.0

    runtime.logger.info(
        f"Copying assignment run '{source_run.peak_assignment_run_id}' of "
        f"sample '{source.sample_item_name}' to "
        f"{len(partition.admitted)} of {len(partition.destinations)} batch "
        f"samples ({'seeded re-score' if rescore else 'literal'})."
    )

    # The route hands one over so its per-destination progress nests under the
    # process the response named. A caller with nobody to report to gets a
    # fresh one, because the notification's process id is a required string
    # and its default only applies when the field is left out entirely.
    process_id = process_id or gen_id(8)

    outcomes: list[dict] = []
    total = len(partition.destinations)
    for index, candidate in enumerate(partition.destinations):
        notification = UserNotification(
            process_id=process_id,
            parent_id=parent_id,
            type="copy_assignments_to_batch",
            status="pending",
            message=(
                f"Copying assignments to sample {index + 1}/{total} of the "
                f"batch of '{source.sample_item_name}'."
            ),
            data={
                "sample_batch_id": source.sample_batch_id,
                "_room_ids": [source.sample_batch_id],
                "_user_id": user_id,
                "_total_samples": total,
                "_item_index": index,
            },
        )
        await send_progress_user_notification(notification)

        if candidate.reason is not None:
            outcomes.append(
                {
                    "sample_item_id": candidate.sample_item_id,
                    "sample_item_name": candidate.sample_item_name,
                    "status": "skipped",
                    "reason": candidate.reason,
                }
            )
        else:
            try:
                outcomes.append(
                    await _copy_to_destination(
                        source=source,
                        source_run=source_run,
                        source_rows=source_rows,
                        mu_source_ppm=mu_source_ppm,
                        bands=bands,
                        destination_sample_item_id=candidate.sample_item_id,
                        rescore=rescore,
                        user_id=user_id,
                    )
                )
            except Exception as error:
                # A per-destination failure must not abort the rest of the
                # fan-out. CancelledError is a BaseException and propagates,
                # so a cancelled fan-out stops rather than logging N failures.
                # An admission refusal - a run that started on the destination
                # after the partition was taken - is the note's "skipped and
                # reported, not failed", so it keeps that classification here.
                detail = getattr(error, "detail", None) or str(error)
                admission_refusal = isinstance(error, DuplicateException)
                runtime.logger.warning(
                    f"Copying assignments to sample "
                    f"'{candidate.sample_item_name}' "
                    f"{'was refused' if admission_refusal else 'failed'}: {detail}"
                )
                outcomes.append(
                    {
                        "sample_item_id": candidate.sample_item_id,
                        "sample_item_name": candidate.sample_item_name,
                        "status": "skipped" if admission_refusal else "failed",
                        "reason": str(detail),
                    }
                )

        await send_progress_user_notification(notification, (index + 1) / total)

    return _fanout_result(source.sample_batch_id, source.sample_item_name, outcomes)
