"""The pre-passes a run makes before Stage A: the reagent ions and the artifacts.

The reagent pre-pass claims the reagent's own ions, the batch's known species
and the ladders the profile names; the artifact pass claims the instrument's
artifact lines. Stage A then reads only the peaks they left.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from types import SimpleNamespace

import pandas as pd

from mascope_backend.api.new.peak_assignments.artifact_pass import (
    build_artifact_assignments,
    claim_artifact_peaks,
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
)
from mascope_backend.api.new.peak_assignments.sample_mechanisms import _source_axis
from mascope_backend.runtime import runtime
from mascope_tools.composition.mass_accuracy import ReagentOffset


def _log_fragments(claims: FragmentClaims, sample_name: str) -> None:
    """Say what the fragment claim read as the source's, where it read any."""
    if claims.summary["claimed"]:
        runtime.logger.info(
            f"Sample '{sample_name}': {claims.summary['claimed']} committed peaks "
            f"read as fragments of {', '.join(claims.summary['ladders'])} "
            f"({claims.summary['claimed_isotopologues']} of their lines with them); "
            f"held: {claims.summary['held']}"
        )


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
) -> tuple[list[dict], set[str], ReagentOffset | None]:
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
        (step 4.2j). Claimed where the file's own claims did not take them, on
        its corrected mass (:func:`reagent_pass.claim_batch_species`), each
        row naming the decision as its reason. The offset is read off the
        file's own claims alone.
    :return: The reagent rows, the peaks they take out of both stages, and where
        the claimed lines put the mass axis, which Stage A scores at where the
        target library fits no offset (:func:`reagent_line_offset`); None for a
        profile with no reagent library. The offset is read off the library's
        lines alone, as it was measured: a ladder's rungs do not move it.

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
    decided = {one.cluster.formula: one for one in batch_species}
    if pooled:
        reasons = Counter(
            decided[hit.cluster.formula].reason
            for hit in pooled
            if hit.cluster.formula in decided
        )
        runtime.logger.info(
            f"Sample '{sample_name}': {len(pooled)} peaks claimed on the batch's "
            "decisions ("
            + ", ".join(
                f"{count} {reason}" for reason, count in sorted(reasons.items())
            )
            + ")"
        )
    rows = build_reagent_assignments(
        hits + rungs + pooled,
        source,
        sample_item_id=sample_item_id,
        peak_assignment_run_id=peak_assignment_run_id,
        batch={
            hit.cluster.formula: decided[hit.cluster.formula]
            for hit in pooled
            if hit.cluster.formula in decided
        },
    )
    if source is not peaks_df:
        read_mz = dict(
            zip(
                peaks_df["sample_peak_id"].astype(str),
                peaks_df["mz"].astype(float),
                strict=True,
            )
        )
        for row in rows:
            row["sample_peak_mz"] = read_mz[row["sample_peak_id"]]
    return rows, {row["sample_peak_id"] for row in rows}, offset


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
