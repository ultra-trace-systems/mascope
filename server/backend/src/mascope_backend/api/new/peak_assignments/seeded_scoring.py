"""The engine's Stage-A scoring chain, run over a seeded formula x mechanism list.

Scoring a composition that no target ion exists for - the batch ledger's
propagation of a found or curated identity to the samples holding the peak, the
inspector's untargeted shortlist - is the same four steps
every time: generate the ions and their isotopologues in memory, match them
against the sample's peaks in ONE ``compute_match_isotopes`` pass, gate the
frame with ``apply_match_params``, and fit-score it with ``score_ions_by_fit``.
Only the seeds, and what the caller reads off the result, differ.

Declared here rather than inside either caller because the steps are subtle in
ways that must not diverge between them: the resolution filter is the
instrument's, the abundance floor is the sample's own match-params floor, and
the synthetic ion ids exist only to group the scored frame back to its seeds.
A second copy of that would drift, and the two callers would then measure the
same composition differently.

Nothing here writes. The ids are synthetic, the frames are in memory, and the
only database access is reading the mechanism rows the seeds name.
"""

import asyncio
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import select

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.new.match.params.lib import apply_match_params
from mascope_backend.api.new.peak_assignments.engine import score_ions_by_fit
from mascope_backend.db import IonizationMechanism, async_session
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_type
from mascope_match import compute_match_isotopes


def finite_or_none(value) -> float | None:
    """Coerce to a finite float, else None (NaN never reaches a stored row)."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def score_or_none(value) -> float | None:
    """Coerce a fit score to a finite float clamped to [0, 1], else None."""
    score = finite_or_none(value)
    if score is None:
        return None
    return min(1.0, max(0.0, score))


def resolution_type_of(sample) -> str:
    """The isotopologue resolution a sample's instrument is scored at."""
    return "LOW" if get_instrument_type(sample.filename) == "tof" else "HIGH"


def build_seeded_isotopes_df(
    seeds: set[tuple[str, str]],
    mechanisms_by_id: dict[str, SimpleNamespace],
    resolution_type: str,
    abundance_threshold: float,
) -> tuple[pd.DataFrame, dict[tuple[str, str], str]]:
    """Expand a formula x mechanism list into a Stage-A isotope frame.

    The same target-ion/IsoSpec path the curated library and the reference
    mirror use, shaped like ``_fetch_known_target_isotopes`` output so
    ``compute_match_isotopes`` and the fit scorer consume it unchanged. Ion and
    isotope ids are synthetic, used only to group the scored frame back to
    seeds - they are never persisted. A formula that fails to generate is
    skipped (its rows score to no evidence), never fails the caller.

    :param seeds: Distinct ``(formula, ionization_mechanism_id)`` pairs.
    :param mechanisms_by_id: The mechanisms those pairs name.
    :param resolution_type: "LOW" (TOF) or "HIGH", the sample's.
    :param abundance_threshold: Minimum relative abundance to participate, the
        sample's match-params floor as in Stage A.
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
            target_compound_id="seed",
            target_compound_formula=formula,
        )
        try:
            ions, isotopes = generate_target_ions_from_composition(compound, mechanisms)
        except Exception as error:  # noqa: BLE001 - a bad formula skips, never fails
            runtime.logger.debug(f"Skipping seed formula '{formula}': {error}")
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
                    # The seed is a bare formula, not a curated target; a row
                    # that has target FKs of its own keeps them.
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


async def fetch_mechanisms_by_id(
    mechanism_ids: set[str],
) -> dict[str, SimpleNamespace]:
    """Resolve mechanism rows for the seeded generation, detached.

    :param mechanism_ids: The mechanism ids the seeds name.
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


def gate_and_score(match_isotope_df: pd.DataFrame, match_params) -> pd.DataFrame:
    """Gate and fit-score a seeded match frame, exactly as Stage A does."""
    gated = apply_match_params(match_isotope_df, match_params)
    return score_ions_by_fit(gated)


def scored_maps(
    scored_df: pd.DataFrame,
) -> tuple[dict[str, float | None], dict[tuple[str, str], dict]]:
    """Index a scored frame for the per-peak evidence lookup.

    The fit is ion-level - after ``score_ions_by_fit`` every isotopologue of an
    ion carries the ion's consolidated fit - while the mass and abundance
    errors are per isotopologue, read off the row that paired to the observed
    peak the caller is asking about. A peak no scored isotopologue paired to
    gets no errors (None): the envelope's evidence for that peak was measured
    elsewhere or not at all, and inventing a number here would be exactly the
    arithmetic the seeded re-score exists to avoid.

    :param scored_df: The gated, fit-scored match frame.
    :return: ``(fit by ion id, per (ion id, sample peak id) errors)``.
    """
    if scored_df.empty or "target_ion_id" not in scored_df.columns:
        return {}, {}

    fit_by_ion: dict[str, float | None] = {}
    for ion_id, group in scored_df.groupby("target_ion_id", sort=False):
        fit_by_ion[str(ion_id)] = score_or_none(group["match_score"].iloc[0])

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
        tof = finite_or_none(getattr(row, "sample_peak_tof", None))
        errors[key] = {
            "mz_error_ppm": finite_or_none(getattr(row, "match_mz_error", None)),
            "abundance_error": finite_or_none(
                getattr(row, "match_abundance_error", None)
            ),
            "sample_peak_tof": tof if tof is not None and tof > 0 else None,
        }
    return fit_by_ion, errors


async def score_seeds(
    sample,
    seeds: set[tuple[str, str]],
    match_params,
) -> tuple[
    dict[tuple[str, str], str],
    dict[str, float | None],
    dict[tuple[str, str], dict],
    pd.DataFrame,
]:
    """Measure a formula x mechanism list against a sample's own peaks.

    The engine's Stage-A chain over a seeded frame and nothing more: ions for
    the seed list, one ``compute_match_isotopes`` pass (a single peak read
    covers every seed), ``apply_match_params`` gating, ``score_ions_by_fit``.
    No candidate enumeration, no untargeted stage, no arbitration.

    :param sample: The sample being scored against.
    :param seeds: Distinct ``(formula, ionization_mechanism_id)`` pairs.
    :param match_params: The sample's resolved match parameters.
    :return: ``(ion by seed, fit by ion, errors by pairing, the scored frame)``.
    """
    if not seeds:
        return {}, {}, {}, pd.DataFrame()

    mechanisms_by_id = await fetch_mechanisms_by_id(
        {mechanism_id for _, mechanism_id in seeds}
    )
    seeded_df, ion_by_seed = await asyncio.to_thread(
        build_seeded_isotopes_df,
        seeds,
        mechanisms_by_id,
        resolution_type_of(sample),
        match_params.isotope_abundance_threshold,
    )
    if seeded_df.empty:
        return ion_by_seed, {}, {}, pd.DataFrame()

    matched_df = await compute_match_isotopes(
        filename=sample.filename,
        target_isotopes_df=seeded_df,
        polarity=sample.polarity,
    )
    if matched_df.empty:
        return ion_by_seed, {}, {}, pd.DataFrame()

    scored_df = await asyncio.to_thread(gate_and_score, matched_df, match_params)
    fit_by_ion, errors_by_pairing = scored_maps(scored_df)
    return ion_by_seed, fit_by_ion, errors_by_pairing, scored_df
