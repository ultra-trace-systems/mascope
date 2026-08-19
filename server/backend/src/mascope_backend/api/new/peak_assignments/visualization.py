"""Composition-driven fit scoring and visualization (B2).

Verifies ANY peak assignment - including untargeted winners that carry no
``target_ion_id`` - by scoring an assigned composition's isotope envelope
against the sample on the fly, instead of reading a persisted target ion. The
isotope-visualization core is shared with the targeted path via
``emit_isotope_visualization``; only the isotope source differs.

These endpoints (``POST .../fit/aggregate`` and ``.../fit/visualize``) are
API/SDK surface without an in-app UI: no frontend view calls them. They serve
HTTP API clients directly and are the designed entry point for SDK-side
assignment verification (docs/dev/sdk_peak_assignment.md, deferred
``fit_aggregate``).
"""

from types import SimpleNamespace

import pandas as pd

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.controllers.visualization.visualization_controller import (
    emit_isotope_visualization,
)
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.match.params import (
    apply_match_params,
    default_match_params,
)
from mascope_backend.db import IonizationMechanism, TargetCompound, async_session
from mascope_backend.db.id import gen_id
from mascope_file.name import get_instrument_type
from mascope_file.string import norm
from mascope_match import compute_match_isotopes
from mascope_match.params import BaseMatchParams


def _none(value):
    """NaN/None -> None (JSON-safe); otherwise a plain float/str."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


async def _composition_match_isotopes(
    sample,
    assigned_formula: str,
    ionization_mechanism_id: str,
    match_params: BaseMatchParams,
) -> tuple[pd.DataFrame, object, object]:
    """Match an assigned composition's isotope envelope against the sample.

    Generates the ion's isotopologues in memory (no persistence) from the neutral
    formula + ionization mechanism, then matches them against the sample. Returns
    ``(match_isotope_df, ion, mechanism)``; the frame is empty when the formula
    yields no ion at the sample's resolution.
    """
    async with async_session() as session:
        mechanism = await session.get(IonizationMechanism, ionization_mechanism_id)
    if mechanism is None:
        raise NotFoundException(
            f"Ionization mechanism '{ionization_mechanism_id}' not found"
        )

    compound = TargetCompound(
        target_compound_id=gen_id(),
        target_compound_formula=norm(assigned_formula),
    )
    ions, isotopes = generate_target_ions_from_composition(compound, [mechanism])
    resolution = "LOW" if get_instrument_type(sample.filename) == "tof" else "HIGH"
    isotopes = [iso for iso in isotopes if iso.resolution == resolution]
    if not ions or not isotopes:
        return pd.DataFrame(), None, mechanism
    ion = ions[0]

    target_isotopes_df = pd.DataFrame(
        [
            {
                "target_isotope_id": iso.target_isotope_id,
                "target_ion_id": iso.target_ion_id,
                "target_isotope_formula": iso.target_isotope_formula,
                "mz": iso.mz,
                "relative_abundance": iso.relative_abundance,
                "resolution": iso.resolution,
                "target_ion_formula": ion.target_ion_formula,
                "ionization_mechanism_id": ionization_mechanism_id,
            }
            for iso in isotopes
        ]
    )
    match_isotope_df = await compute_match_isotopes(
        filename=sample.filename,
        target_isotopes_df=target_isotopes_df,
        match_params=match_params,
        polarity=sample.polarity,
    )
    match_isotope_df = match_isotope_df.drop(columns=["index"], errors="ignore")
    return match_isotope_df, ion, mechanism


@api_controller()
async def aggregate_composition_fit(
    sample_item_id: str,
    assigned_formula: str,
    ionization_mechanism_id: str,
    match_params: BaseMatchParams | None = None,
) -> dict:
    """Isotope-table data for an assigned composition.

    Mirrors the nested ``{match_ions, match_isotopes}`` shape the targeted
    ion aggregate returns, but scores an on-the-fly composition rather than a
    persisted target ion.
    """
    sample = await fetch_sample(sample_item_id)
    if match_params is None:
        match_params = await default_match_params(sample_item_id)

    match_isotope_df, ion, mechanism = await _composition_match_isotopes(
        sample, assigned_formula, ionization_mechanism_id, match_params
    )
    if match_isotope_df.empty or ion is None:
        return {
            "message": "No isotope matches for the composition.",
            "data": {
                "matches": {"match_ions": 0, "match_isotopes": 0},
                "match_ions": [],
                "match_isotopes": [],
            },
        }

    filtered = apply_match_params(match_isotope_df, match_params)
    ion_score = float((filtered["match_score"] * filtered["relative_abundance"]).sum())
    match_ions = [
        {
            "target_compound_formula": norm(assigned_formula),
            "target_ion_id": ion.target_ion_id,
            "target_ion_formula": ion.target_ion_formula,
            "ionization_mechanism": mechanism.ionization_mechanism,
            "filter_params": {},
            "match": {
                "sample_item_id": sample_item_id,
                "match_score": ion_score,
                "sample_peak_intensity_sum": float(
                    filtered["sample_peak_intensity"].fillna(0.0).sum()
                ),
            },
        }
    ]
    match_isotopes = [
        {
            "target_ion_id": ion.target_ion_id,
            "target_isotope_id": row.get("target_isotope_id"),
            "target_isotope_formula": row.get("target_isotope_formula"),
            "mz": _none(row.get("mz")),
            "relative_abundance": _none(row.get("relative_abundance")),
            "resolution": row.get("resolution"),
            "match": {
                "sample_item_id": sample_item_id,
                "sample_peak_mz": _none(row.get("sample_peak_mz")),
                "sample_peak_intensity": _none(row.get("sample_peak_intensity")),
                "sample_peak_intensity_relative": _none(
                    row.get("sample_peak_intensity_relative")
                ),
                "sample_peak_tof": _none(row.get("sample_peak_tof")),
                "match_abundance_error": _none(row.get("match_abundance_error")),
                "match_mz_error": _none(row.get("match_mz_error")),
                "match_score": _none(row.get("match_score")),
            },
        }
        for _, row in filtered.sort_values("mz").iterrows()
    ]
    return {
        "message": (
            f"Fit for composition '{assigned_formula}' retrieved successfully."
        ),
        "data": {
            "matches": {
                "match_ions": len(match_ions),
                "match_isotopes": len(match_isotopes),
            },
            "match_ions": match_ions,
            "match_isotopes": match_isotopes,
        },
    }


@api_controller_background_task(error_notification_rooms=["user_id"])
async def visualize_composition_focus(
    sample_item_id: str,
    assigned_formula: str,
    ionization_mechanism_id: str,
    peak_min_intensity: float,
    mz_tolerance: float,
    isotope_ratio_tolerance: float,
    match_params: BaseMatchParams | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    sid: str | None = None,
) -> None:
    """Emit the sum-spectrum and time-series for an assigned composition.

    The composition equivalent of ``visualize_ion_focus``: builds the matched
    isotope set from the formula + mechanism and hands it to the shared
    ``emit_isotope_visualization`` core, so a composition's spectra and time
    series arrive over the socket exactly like a targeted ion's.
    """
    sample = await fetch_sample(sample_item_id)
    if match_params is None:
        match_params = await default_match_params(sample_item_id)

    match_isotope_df, _ion, _mech = await _composition_match_isotopes(
        sample, assigned_formula, ionization_mechanism_id, match_params
    )

    # Only isotopes that matched an observed peak feed the visualization core
    # (mirrors the targeted _fetch_isotopes, which inner-joins MatchIsotope).
    #
    # _composition_match_isotopes returns a bare DataFrame - no columns at all -
    # when the composition yields no ions or no isotopes at the sample's
    # resolution, which a TOF sample or a formula the ion generator rejects will
    # do. Sorting that by a column it does not have raises, and this runs as a
    # background task, so the user gets an error toast instead of an empty plot.
    # The sibling aggregate_composition_fit already returns an empty result here.
    if match_isotope_df.empty:
        matched = match_isotope_df
    else:
        matched = match_isotope_df[
            match_isotope_df["sample_peak_mz"].notna()
            & (match_isotope_df["sample_peak_id"].fillna("") != "")
        ].sort_values("relative_abundance", ascending=False)

    isotopes = [
        SimpleNamespace(**{key: _none(value) for key, value in row.items()})
        for _, row in matched.iterrows()
    ]
    await emit_isotope_visualization(
        sample,
        isotopes,
        peak_min_intensity=peak_min_intensity,
        mz_tolerance=mz_tolerance,
        isotope_ratio_tolerance=isotope_ratio_tolerance,
        user_id=user_id,
        sid=sid,
    )
