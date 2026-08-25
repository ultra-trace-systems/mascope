"""Peak match query helpers.

Queries the database for isotope-level match data for a given sample,
applies match filtering parameters, and groups results by peak ID.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import and_, label, select

from mascope_backend.api.lib.utils import strings_json_safe
from mascope_backend.api.new.match.params.lib import apply_match_params
from mascope_backend.db import (
    MatchCollection,
    MatchIsotope,
    TargetCollection,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)


async def query_peak_matches(
    sample_item_id: str,
    instrument: str,
    peak_ids: list[str],
) -> list[list[dict]]:
    """Query and group match data for a list of peaks.

    Returns a list aligned with *peak_ids* where each element is a (possibly
    empty) list of match dictionaries.

    :param sample_item_id: The sample to query matches for.
    :param instrument: Instrument name (passed through for match filtering).
    :param peak_ids: Ordered list of peak IDs to align results to.
    :return: Per-peak list of match dictionaries.
    """
    async with async_session() as session:
        query = (
            select(
                MatchIsotope.sample_peak_id,
                MatchIsotope.match_mz_error,
                MatchIsotope.match_abundance_error,
                MatchIsotope.match_score,
                MatchIsotope.sample_peak_intensity,
                TargetIsotope.target_isotope_id,
                TargetIsotope.relative_abundance,
                TargetIsotope.target_isotope_formula,
                TargetIon.target_ion_id,
                TargetIon.target_ion_formula,
                TargetIon.ionization_mechanism_id,
                TargetIon.filter_params,
                TargetCompound.target_compound_id,
                TargetCompound.target_compound_name,
                TargetCompound.target_compound_formula,
                TargetCollection.target_collection_id,
                label(
                    "instrument",
                    instrument,  # type: ignore
                ),  # Add instrument as a column for filtering logic
            )
            .select_from(MatchIsotope)
            .join(
                TargetIsotope,
                TargetIsotope.target_isotope_id == MatchIsotope.target_isotope_id,
            )
            .join(
                TargetIon,
                TargetIon.target_ion_id == TargetIsotope.target_ion_id,
            )
            .join(
                TargetCompound,
                TargetCompound.target_compound_id == TargetIon.target_compound_id,
            )
            .join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetCompound.target_compound_id,
            )
            .join(
                TargetCollection,
                TargetCollection.target_collection_id
                == TargetCompoundInTargetCollection.target_collection_id,
            )
            .join(
                MatchCollection,
                and_(
                    MatchCollection.sample_item_id == MatchIsotope.sample_item_id,
                    MatchCollection.target_collection_id
                    == TargetCollection.target_collection_id,
                ),
            )
            .where(
                and_(
                    MatchIsotope.sample_item_id == sample_item_id,
                    MatchIsotope.match_score > 0,
                )
            )
        )
        result = await session.execute(query)
        match_rows = result.all()

    match_df = apply_match_params(pd.DataFrame([row._asdict() for row in match_rows]))

    if match_df.empty:
        return [[] for _ in peak_ids]

    match_df = match_df[match_df.match_category > 0]
    if match_df.empty:
        return [[] for _ in peak_ids]

    # Sort by score descending so "first" in the aggregation and the
    # per-peak list order both reflect the best-scoring match.
    match_df = match_df.sort_values("match_score", ascending=False)

    agg = (
        match_df.groupby(["sample_peak_id", "target_isotope_id"], sort=False)
        .agg(
            match_score_isotope=("match_score", "first"),
            relative_abundance=("relative_abundance", "first"),
            target_isotope_formula=("target_isotope_formula", "first"),
            target_ion_id=("target_ion_id", "first"),
            target_ion_formula=("target_ion_formula", "first"),
            target_compound_id=("target_compound_id", "first"),
            target_compound_name=("target_compound_name", "first"),
            target_compound_formula=("target_compound_formula", "first"),
            ionization_mechanism_id=("ionization_mechanism_id", "first"),
            target_collection_ids=(
                "target_collection_id",
                lambda tci: pd.unique(tci).tolist(),
            ),
        )
        .reset_index()
    )

    # target_compound_name is nullable, so under pandas 3 the NULLs arrive as
    # NaN rather than None and would break serialization of the response.
    agg = strings_json_safe(agg)

    grouped = agg.groupby("sample_peak_id", sort=False)
    peak_id_to_match = {
        pid: grp.drop(columns=["sample_peak_id"]).to_dict(orient="records")
        for pid, grp in grouped
    }

    return [peak_id_to_match.get(pid, []) for pid in peak_ids]
