"""The pre-passes a run makes before Stage A: the reagent ions and the artifacts.

The reagent pre-pass claims the reagent's own ions and the ladders the
profile names, and matches the source ions the batch decided are there; the
artifact pass claims the instrument's artifact lines. Stage A then reads only
the peaks they left. A run claims the batch's source ions after Stage A
and the search, on its rows (``batch_decisions.claim_source_ions``).
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pandas as pd

from mascope_backend.api.new.peak_assignments.artifact_pass import (
    build_artifact_assignments,
    claim_artifact_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_decisions import (
    claim_source_ions,
)
from mascope_backend.api.new.peak_assignments.model_readers import (
    _claim_reagent_lines,
    _claimed_lines_offset,
    _reagent_purity,
)
from mascope_backend.api.new.peak_assignments.profiles import ResolvedProfile
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    BatchSpecies,
    FragmentClaims,
    build_reagent_assignments,
    claim_batch_species,
    claim_ladder_peaks,
    ion_ladders_for,
    record_displaced,
)
from mascope_backend.api.new.peak_assignments.sample_mechanisms import _source_axis
from mascope_backend.runtime import runtime
from mascope_tools.composition.mass_accuracy import ReagentOffset
from mascope_tools.composition.reagents import ReagentHit


def _log_fragments(claims: FragmentClaims, sample_name: str) -> None:
    """Say what the fragment claim read as the source's, where it read any."""
    if claims.summary["claimed"]:
        runtime.logger.info(
            f"Sample '{sample_name}': {claims.summary['claimed']} committed peaks "
            f"read as fragments of {', '.join(claims.summary['ladders'])} "
            f"({claims.summary['claimed_isotopologues']} of their lines with them); "
            f"held: {claims.summary['held']}"
        )


@dataclass(frozen=True)
class SourceIonHits:
    """The source ions a file's batch decided are there, matched on the
    file's corrected mass by the claim stage and not yet claimed.

    :param hits: The ions and their isotope lines, monoisotopic before their
        lines (``reagents.match_batch_species``), indexing the sample's peaks
        positionally.
    :param decided: Ion formula -> the batch's decision, for every ion the
        batch named.
    """

    hits: tuple[ReagentHit, ...] = ()
    decided: Mapping[str, BatchSpecies] = field(default_factory=dict)


def _reagent_assignments(
    peaks_df: pd.DataFrame,
    resolved_profile: ResolvedProfile,
    sample_item_id: str,
    peak_assignment_run_id: str,
    *,
    resolution: Callable[[float], float] | None,
    own_mechanisms: list[SimpleNamespace],
    sample_name: str,
    batch_species: Sequence[BatchSpecies] = (),
) -> tuple[list[dict], set[str], ReagentOffset | None, SourceIonHits]:
    """The reagent pre-pass: the peaks the source made, claimed before the stages.

    Shared by the run-backed orchestrator and the run-less ingest fold for the
    same reason Stage A is: the two ledgers have to agree on which peaks are
    reagent, or an ingest fold and an explicit run would disagree about what the
    sample contains.

    :param peaks_df: Every observed peak of the sample.
    :param resolved_profile: The run's resolved chemistry, which names the
        source's reagent and (for a labelled one) its isotopic purity.
    :param sample_item_id: The sample these rows belong to.
    :param peak_assignment_run_id: The run they are stamped with.
    :param resolution: The file's resolving power as a function of m/z, at
        which an ion's isotope line is one peak of the file; None where it has
        none.
    :param own_mechanisms: :func:`_searched_mechanisms` of the mode's own
        channels: the channels the ladder claim reads, whether the run
        abstracts a hydride and what a stage could read a rung as. A
        ``swap_adducts`` decoy passes the ones resolved before its swap: the
        source's lines are the sample's, so a decoy claims what its control
        claims.
    :param sample_name: For the log line.
    :param batch_species: The source ions the sample's batch decided are
        there, one entry per ion (``batch_decisions.BatchDecisions
        .source_ions``): the library ions it decided present (step 4.2h) and
        the rungs its water ladder passed in the sample's acquisition segment
        (step 4.2j). Matched where the file's own claims did not take them,
        on its corrected mass (:func:`reagent_pass.claim_batch_species`), and
        returned unclaimed: a run claims them after Stage A and the search,
        where the batch claim's keep rules can read the run's rows
        (``batch_decisions.claim_source_ions``, MAS-1089), and a path with no
        known set of its own claims them before it proposes
        (:func:`_batch_source_rows`). The offset is read off the file's own
        claims alone.
    :return: The reagent rows of the file's own claims, the peaks they take
        out of both stages, where the claimed lines put the mass axis, which
        Stage A scores at where the target library fits no offset
        (:func:`reagent_line_offset`; None for a profile with no reagent
        library, and read off the library's lines alone, as it was measured: a
        ladder's rungs do not move it), and the batch's source ions matched
        on the file's peaks.

    The claim reads the peaks on the axis as stored (:func:`_source_axis`), so
    a decoy claims the lines its control claimed, at the offset its control
    measured them at; each row then names the m/z the run reads its peak at,
    as every other row of the run does.
    """
    source = _source_axis(peaks_df)
    hits, calibration = _claim_reagent_lines(
        source, resolved_profile, resolution=resolution
    )
    rungs, ladder_summary = claim_ladder_peaks(
        source,
        ion_ladders_for(resolved_profile.profile.name),
        calibration=calibration,
        claimed=hits,
        channels=[m.ionization_mechanism for m in own_mechanisms],
        claim_ppm=resolved_profile.mz_precision_ppm,
        purity=_reagent_purity(resolved_profile),
        resolution=resolution,
    )
    if ladder_summary is not None and ladder_summary["claimed"]:
        runtime.logger.info(
            f"Sample '{sample_name}': {ladder_summary['claimed']} peaks claimed "
            f"as rungs of {', '.join(ladder_summary['ladders'])} "
            f"({ladder_summary['claimed_isotopologues']} of their lines with "
            f"them); held: {ladder_summary['held']}"
        )
    if calibration is not None and calibration.anchors:
        runtime.logger.info(
            "Reagent pre-pass anchored at "
            f"{calibration.offset_ppm:+.1f} ppm (+/- {calibration.tolerance_ppm:.1f}) "
            "on "
            + ", ".join(f"{label} {error:+.1f}" for label, error in calibration.anchors)
        )
    offset = _claimed_lines_offset(hits, calibration, resolved_profile)
    if offset is not None and offset.mu_ppm is not None:
        runtime.logger.info(
            f"Reagent pre-pass lines put the mass axis at {offset.mu_ppm:+.2f} ppm "
            f"over {offset.lines} lines"
            + ("" if offset.beyond_width else ", inside the width")
        )
    pooled = claim_batch_species(
        source,
        batch_species,
        calibration=calibration,
        claimed=hits + rungs,
        claim_ppm=resolved_profile.mz_precision_ppm,
        purity=_reagent_purity(resolved_profile),
        resolution=resolution,
    )
    rows = build_reagent_assignments(
        hits + rungs,
        source,
        sample_item_id=sample_item_id,
        peak_assignment_run_id=peak_assignment_run_id,
    )
    _at_read_mz(rows, source, peaks_df)
    found = SourceIonHits(
        tuple(pooled), {one.cluster.formula: one for one in batch_species}
    )
    return rows, {row["sample_peak_id"] for row in rows}, offset, found


def _at_read_mz(rows: list[dict], source: pd.DataFrame, peaks_df: pd.DataFrame) -> None:
    """Name the m/z the run reads each claimed peak at, as every other row of
    the run does, where the claim read the peaks on the axis as stored
    (:func:`_source_axis`)."""
    if source is peaks_df:
        return
    read_mz = dict(
        zip(
            peaks_df["sample_peak_id"].astype(str),
            peaks_df["mz"].astype(float),
            strict=True,
        )
    )
    for row in rows:
        row["sample_peak_mz"] = read_mz[row["sample_peak_id"]]


def _batch_source_rows(
    found: SourceIonHits,
    hits: Sequence[ReagentHit],
    peaks_df: pd.DataFrame,
    sample: Any,
    peak_assignment_run_id: str,
    displaced: Mapping[str, dict] | None = None,
) -> list[dict]:
    """The rows of the source ions claimed on the batch's word, each naming
    the decision as its reason (:func:`reagent_pass.build_reagent_assignments`),
    at the m/z the run reads their peaks at.

    :param found: What the claim stage matched (:func:`_reagent_assignments`).
    :param hits: Those of its hits the claim takes: all of them where the
        path claims before it proposes, the ones the keep rules leave where it
        claims after the known set (``batch_decisions.claim_source_ions``).
    :param peaks_df: Every observed peak of the sample, as the run reads them.
    :param sample: The sample (``sample_item_id``, ``sample_item_name``).
    :param displaced: Sample peak -> the run's reading a claim displaced
        from it, which its row records (``reagent_pass.record_displaced``).
    """
    if not hits:
        return []
    source = _source_axis(peaks_df)
    rows = build_reagent_assignments(
        list(hits),
        source,
        sample_item_id=sample.sample_item_id,
        peak_assignment_run_id=peak_assignment_run_id,
        batch={
            hit.cluster.formula: found.decided[hit.cluster.formula]
            for hit in hits
            if hit.cluster.formula in found.decided
        },
    )
    _at_read_mz(rows, source, peaks_df)
    for row in rows:
        reading = (displaced or {}).get(row["sample_peak_id"])
        if reading is not None and not row.get("owner_peak_assignment_id"):
            record_displaced(row, reading)
    reasons = Counter(
        found.decided[hit.cluster.formula].reason
        for hit in hits
        if hit.cluster.formula in found.decided
    )
    runtime.logger.info(
        f"Sample '{sample.sample_item_name}': {len(hits)} peaks claimed on the "
        "batch's "
        "decisions ("
        + ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
        + ")"
    )
    return rows


def _batch_claims(rows: list[dict]) -> dict[str, int]:
    """How many ions rows claimed on the batch's word name each reason."""
    counted = Counter(
        row["provenance"]["reagent"]["reason"]
        for row in rows
        if not row.get("owner_peak_assignment_id")
    )
    return dict(sorted(counted.items()))


def _claim_batch_source(
    found: SourceIonHits,
    stage_a: list[dict],
    stage_b: list[dict],
    peaks_df: pd.DataFrame,
    sample: Any,
    run_id: str,
    *,
    claimed_peak_ids: set[str],
    control: frozenset[str] | None,
) -> tuple[list[dict], list[dict], list[dict], dict[str, str]]:
    """Claim the batch's source ions on the rows of the known set and the
    search, by the keep rules the batch's own claims take a member by
    (``batch_decisions.claim_source_ions``, MAS-1089): the rows they take a
    peak from leave Stage A's and the search's, and every stage after reads
    the peak as the source's.

    :param found: What the claim stage matched (:func:`_reagent_assignments`).
    :param control: A decoy arm's control's claims on the batch's word.
    :return: The claimed rows, Stage A's and the search's rows left, and what
        became of each ion by its peak.
    """
    hits, stage_a, stage_b, outcomes, displaced = claim_source_ions(
        stage_a,
        stage_b,
        found.hits,
        found.decided,
        peak_ids=peaks_df["sample_peak_id"].astype(str).tolist(),
        claimed_peak_ids=claimed_peak_ids,
        control=control,
    )
    rows = _batch_source_rows(found, hits, peaks_df, sample, run_id, displaced)
    counted = Counter(outcomes.values())
    runtime.logger.info(
        f"Sample '{sample.sample_item_name}' read {len(outcomes)} source ions "
        "its batch "
        "decided are there: "
        + ", ".join(f"{count} {why}" for why, count in sorted(counted.items()))
    )
    return rows, stage_a, stage_b, outcomes


def _take_rows(ctx: Any, rows: list[dict]) -> None:
    """The rows of the batch's source ions a claim took, among the source's."""
    ctx.reagent_rows = [*ctx.reagent_rows, *rows]
    ctx.reagent_peak_ids |= {row["sample_peak_id"] for row in rows}
    if claims := _batch_claims(rows):
        ctx.batch_applied["claimed"] = claims


def take_batch_source(ctx: Any, found: SourceIonHits) -> None:
    """Claim every source ion the batch decided there, in the claim stage: a
    path with no known set for the keep rules to ask
    (``pipeline.claims_after_the_known_set``).

    :param ctx: The run (``pipeline.RunContext``).
    :param found: What the claim stage matched (:func:`_reagent_assignments`).
    """
    rows = _batch_source_rows(found, found.hits, ctx.peaks_df, ctx.sample, ctx.run_id)
    _take_rows(ctx, rows)


def claim_batch_source_on_known_set(ctx: Any) -> None:
    """The batch's source ions, claimed on the rows of the known set and the
    search (:func:`_claim_batch_source`); a decoy arm claims the peaks its
    control claimed (``decoy.DecoyPlan.control_source_claims``).

    :param ctx: The run (``pipeline.RunContext``), in its elect stage.
    """
    if not ctx.batch_source.hits:
        return
    plan = ctx.decoy_plan
    rows, ctx.stage_a, ctx.stage_b, outcomes = _claim_batch_source(
        ctx.batch_source,
        ctx.stage_a,
        ctx.stage_b,
        ctx.peaks_df,
        ctx.sample,
        ctx.run_id,
        claimed_peak_ids=ctx.claimed_peak_ids,
        control=None if plan is None else plan.control_source_claims,
    )
    _take_rows(ctx, rows)
    ctx.batch_applied["source"] = dict(sorted(Counter(outcomes.values()).items()))


def _artifact_assignments(
    peaks_df: pd.DataFrame,
    instrument_type: str | None,
    sample_item_id: str,
    peak_assignment_run_id: str,
    claimed_peak_ids: set[str] | None = None,
) -> tuple[list[dict], set[str]]:
    """The artifact pre-pass: the detector's ringing, claimed before the stages.

    Shared by the run-backed orchestrator and the run-less ingest fold for the
    same reason the reagent pre-pass is: a peak is an instrument artifact
    whichever way the sample was assigned, and the two ledgers have to agree.

    :param peaks_df: Every observed peak of the sample.
    :param instrument_type: The sample's instrument class; only an FT
        instrument's spectrum rings (see :mod:`artifact_pass`).
    :param sample_item_id: The sample these rows belong to.
    :param peak_assignment_run_id: The run they are stamped with.
    :param claimed_peak_ids: What the reagent pre-pass took, so the two passes
        cannot both write a row for one peak.
    :return: The artifact rows, and the peaks they take out of both stages.
    """
    rows = build_artifact_assignments(
        claim_artifact_peaks(peaks_df, instrument_type, claimed_peak_ids),
        sample_item_id=sample_item_id,
        peak_assignment_run_id=peak_assignment_run_id,
    )
    return rows, {row["sample_peak_id"] for row in rows}
