"""
Target isotopes fetching utilities for match computation.

Provides consolidated queries for determining which target isotopes need match computation
by comparing current target associations against existing matches.
"""

import pandas as pd
from sqlalchemy import select
from sqlalchemy.sql import func

from mascope_backend.api.new.ionization.modes.util import (
    fetch_sample_ionization_mechanism_ids,
)
from mascope_backend.api.new.match.params import default_match_params
from mascope_backend.api.new.match.params.lib import isotope_abundance_threshold_expr
from mascope_backend.db import (
    IonizationMechanism,
    MatchIsotope,
    Sample,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_type
from mascope_match.params import BaseMatchParams


async def fetch_existing_main_isotope_references(
    sample_item_id: str,
    target_ion_ids: list[str],
) -> pd.DataFrame:
    """
    Fetches existing main isotope reference data for abundance error calculation.

    For each target_ion_id that has existing matches, retrieves the main isotope's
    (highest relative_abundance) sample_peak_intensity and relative_abundance values,
    and the peak it was matched to (sample_peak_id, sample_peak_mz), whose per-scan
    series an Orbitrap line's ratio is read against.

    Only matched isotopes (a real, non-empty sample_peak_id) are considered: an
    unmatched isotope has zero intensity and makes a degenerate reference, and
    unmatched rows are no longer stored. The explicit filter keeps the reference
    deterministic for legacy samples that may still carry unmatched rows until the
    backfill removes them.

    :param sample_item_id: Sample item ID to query matches for
    :type sample_item_id: str
    :param target_ion_ids: List of target ion IDs to find references for
    :type target_ion_ids: list[str]
    :return: DataFrame with columns (target_ion_id, sample_peak_intensity,
        relative_abundance, sample_peak_id, sample_peak_mz)
    :rtype: pd.DataFrame
    """
    if not target_ion_ids:
        return pd.DataFrame()

    async with async_session() as session:
        # Subquery to find max relative_abundance per target_ion_id from existing matches
        max_abundance_subquery = (
            select(
                TargetIsotope.target_ion_id,
                func.max(TargetIsotope.relative_abundance).label("max_abundance"),
            )
            .join(
                MatchIsotope,
                MatchIsotope.target_isotope_id == TargetIsotope.target_isotope_id,
            )
            .where(
                MatchIsotope.sample_item_id == sample_item_id,
                TargetIsotope.target_ion_id.in_(target_ion_ids),
                MatchIsotope.sample_peak_id != "",
            )
            .group_by(TargetIsotope.target_ion_id)
            .subquery()
        )

        # Get the main isotope data (highest relative_abundance) per ion
        stmt = (
            select(
                TargetIsotope.target_ion_id,
                MatchIsotope.sample_peak_intensity,
                TargetIsotope.relative_abundance,
                MatchIsotope.sample_peak_id,
                MatchIsotope.sample_peak_mz,
            )
            .join(
                MatchIsotope,
                MatchIsotope.target_isotope_id == TargetIsotope.target_isotope_id,
            )
            .join(
                max_abundance_subquery,
                (TargetIsotope.target_ion_id == max_abundance_subquery.c.target_ion_id)
                & (
                    TargetIsotope.relative_abundance
                    == max_abundance_subquery.c.max_abundance
                ),
            )
            .where(
                MatchIsotope.sample_item_id == sample_item_id,
                MatchIsotope.sample_peak_id != "",
            )
        )

        rows = (await session.execute(stmt)).all()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame([row._asdict() for row in rows])
    runtime.logger.debug(
        f"Found {len(df)} existing main isotope references "
        f"for {len(target_ion_ids)} target ions"
    )
    return df


async def fetch_sample_unmatched_target_isotopes(
    sample: Sample,
    match_params: BaseMatchParams | None = None,
) -> pd.DataFrame:
    """
    Fetches target isotopes that need match computation for a specific sample.
    - Gets isotopes associated with sample's batch target collections
    - Filters by sample ionization mechanisms (from ionization mode)
    - Filters by sample polarity compatibility
    - Applies match parameter filtering based on sample instrument type
    - Excludes isotopes of ions already evaluated for this sample: any stored
      match_isotope row of an ion (a real match or the zero-score sentinel kept
      by select_match_isotopes_to_persist) marks the whole ion as evaluated
    - Excludes isotopes that already have a stored row themselves (safety net
      for half-invalidated states, so recompute never double-inserts)

    :param sample: Sample model object
    :type sample: Sample
    :param match_params: Match parameters containing settings for the matching process, default to None
    :type match_params: BaseMatchParams | None
    :raises ApiException: When no targets associated with batch, no polarity-compatible targets, or all matches already exist
    :return: DataFrame of target isotopes ready for match computation
    :rtype: pd.DataFrame
    """
    if not match_params:
        match_params = await default_match_params(sample.sample_item_id)

    async with async_session() as session:
        ionization_mechanism_ids = await fetch_sample_ionization_mechanism_ids(
            sample.sample_item_id
        )

        # Determine resolution type based on instrument
        resolution_type = (
            "LOW" if (get_instrument_type(sample.filename)) == "tof" else "HIGH"
        )

        # Subquery for previously matched isotopes
        matched_isotopes_subquery = (
            select(MatchIsotope.target_isotope_id)
            .where(MatchIsotope.sample_item_id == sample.sample_item_id)
            .distinct(MatchIsotope.target_isotope_id)
        )

        # Subquery for ions already evaluated for this sample. Any stored
        # isotope row marks its whole ion as evaluated: compute keeps every
        # scoring isotope plus one zero-score sentinel per ion that matched
        # nothing (select_match_isotopes_to_persist), so absence of rows means
        # the ion was never evaluated - or an invalidation path deleted its
        # rows to force recomputation.
        evaluated_ions_subquery = (
            select(TargetIsotope.target_ion_id)
            .join(
                MatchIsotope,
                MatchIsotope.target_isotope_id == TargetIsotope.target_isotope_id,
            )
            .where(MatchIsotope.sample_item_id == sample.sample_item_id)
            .distinct(TargetIsotope.target_ion_id)
        )

        # Effective isotope abundance threshold per ion (ion-scoped override in
        # TargetIon.filter_params, else the instrument default from match_params).
        # Isotopes below the threshold are never matched, pruning the negligible tail.
        abundance_threshold = isotope_abundance_threshold_expr(
            TargetIon.filter_params,
            sample.instrument,
            match_params.isotope_abundance_threshold,
        )

        stmt = (
            select(
                TargetIsotope.target_isotope_id,
                TargetIsotope.target_ion_id,
                TargetIsotope.mz,
                TargetIsotope.relative_abundance,
                TargetIsotope.resolution,
                IonizationMechanism.ionization_mechanism_polarity,
                IonizationMechanism.ionization_mechanism,
            )
            .distinct(TargetIsotope.target_isotope_id)
            .select_from(TargetIsotope)
            .join(TargetIon, TargetIon.target_ion_id == TargetIsotope.target_ion_id)
            .join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
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
                TargetCollectionInSampleBatch,
                TargetCollectionInSampleBatch.target_collection_id
                == TargetCompoundInTargetCollection.target_collection_id,
            )
            .where(
                TargetCollectionInSampleBatch.sample_batch_id == sample.sample_batch_id,
                TargetIon.ionization_mechanism_id.in_(ionization_mechanism_ids),
                IonizationMechanism.ionization_mechanism_polarity == sample.polarity,
                TargetIsotope.resolution == resolution_type,
                # Skip negligible isotopes below the effective abundance threshold
                TargetIsotope.relative_abundance >= abundance_threshold,
                # Exclude every isotope of an already-evaluated ion
                TargetIsotope.target_ion_id.notin_(evaluated_ions_subquery),
                # Safety net: never re-fetch an isotope that still has a stored
                # row (e.g. after a partial invalidation deleted only some of
                # an ion's rows) - recomputing it would double-insert
                TargetIsotope.target_isotope_id.notin_(matched_isotopes_subquery),
            )
        )
        if not (rows := (await session.execute(stmt)).all()):
            return pd.DataFrame()

        target_isotopes_df = pd.DataFrame([row._asdict() for row in rows])

        runtime.logger.info(
            f"Found {len(target_isotopes_df)} unmatched target isotopes for sample '{sample.sample_item_name}' "
            f"(polarity: {sample.polarity})"
        )

        return target_isotopes_df
