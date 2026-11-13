import pandas as pd

from mascope_backend.api.controllers.match.isotopes.match_isotopes_controller import (
    create_match_isotopes,
)
from mascope_backend.api.lib.api_features import (
    api_controller,
)
from mascope_backend.api.models.match.isotopes.match_isotopes_pydantic_model import (
    MatchIsotopeBase,
)
from mascope_backend.db import Sample
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_match import (
    compute_match_isotopes,
)


def select_match_isotopes_to_persist(match_isotope_df: pd.DataFrame) -> pd.DataFrame:
    """
    Selects which computed match isotope rows are persisted to the database.

    Kept rows are:
    - every isotope that scored above zero: a real, in-window match, and
    - one sentinel row per ion whose isotopes ALL scored zero: the main isotope
      (highest relative_abundance; ties broken by lowest mz, then
      target_isotope_id) with its computed values (score 0).

    Stored rows double as "this ion was evaluated for this sample" markers:
    fetch_sample_unmatched_target_isotopes skips every isotope of an ion that
    has any stored row, so a refresh only computes ions that were never
    evaluated (or whose rows an invalidation path deleted). Zero-score rows
    beyond the sentinel carry no analytical value - a score of 0 means either
    no peak in the match window, or errors so large (m/z >= 100 ppm, abundance
    >= 100%) that apply_match_params can never raise them to a match at any
    read-time tolerance - so they are dropped here and reconstructed on read in
    aggregate_match_isotope_filtered_data. This matches the "found peak"
    definition used by export_goldens (match_score > 0). A dropped row on its
    mass loses no intensity either: apply_match_params also zeroes the
    intensity of a line whose abundance error reaches
    RATIO_MISS_DROPS_INTENSITY (1.0, where its score is 0), so the stored ion
    sums equal the live ones (#3000).

    INVARIANT: dropping zero-score rows preserves read-time tolerance loosening
    only because the UI sliders cap at exactly the scoring's zero points - m/z
    tolerance <= 100 ppm (mz_term = 0 at 100 ppm) and isotope ratio tolerance
    <= 1.0 (abundance_term = 0 at 100%). Every record reachable as a nonzero
    match at any slider setting has score > 0 and is kept. If those slider
    maxima are ever raised (see SidebarMatchParams.vue), or an instrument
    default isotope_abundance_threshold is lowered in mascope_match.params,
    previously-skipped records become reachable-but-unstored and the evaluated
    markers go stale - such releases require a forced full rematch.

    :param match_isotope_df: Computed match isotopes for one sample; must carry
        the target columns target_ion_id, relative_abundance and mz.
    :type match_isotope_df: pd.DataFrame
    :return: The subset of rows to persist.
    :rtype: pd.DataFrame
    """
    matched_df = match_isotope_df[match_isotope_df["match_score"] > 0]
    zero_ion_df = match_isotope_df[
        ~match_isotope_df["target_ion_id"].isin(matched_df["target_ion_id"])
    ]
    sentinel_df = zero_ion_df.sort_values(
        by=["target_ion_id", "relative_abundance", "mz", "target_isotope_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).drop_duplicates(subset="target_ion_id", keep="first")
    return pd.concat([matched_df, sentinel_df])


@api_controller()
async def compute_and_create_sample_match_isotope_data(
    sample: Sample,
    target_isotopes_df: pd.DataFrame,
    existing_reference_df: pd.DataFrame | None = None,
    notification: UserNotification | None = None,
) -> dict[str, pd.DataFrame]:
    """
    Computes match isotopes for a given sample against a set of target isotopes.

    It updates the computation progress if progress properties are provided. Match isotopes are then saved to the database.

    :param sample: Sample model object
    :type sample: Sample
    :param target_isotopes_df: A DataFrame containing target isotope information for match computation.
    :type target_isotopes_df: DataFrame
    :param existing_reference_df: Optional DataFrame containing existing main isotope reference data
        (target_ion_id, sample_peak_intensity, relative_abundance) for proper abundance error
        calculation when computing additional isotopes for ions that already have matches.
    :type existing_reference_df: pd.DataFrame | None
    :param notification: Optional notification for sending progress user notifications of match computation.
    :type notification: UserNotification | None
    :return: Dictionary containing match_isotopes DataFrames
    :rtype: dict[str, pd.DataFrame]
    """
    #  Sent progress user notification if notification is provided
    if notification:
        await send_progress_user_notification(notification, 0.33)

    # Compute match isotopes for the given sample and target isotopes
    runtime.logger.info(f"Computing match isotopes for file: {sample.filename}")

    match_isotope_df = await compute_match_isotopes(
        filename=sample.filename,
        target_isotopes_df=target_isotopes_df,
        polarity=sample.polarity,
        existing_reference_df=existing_reference_df,
    )
    if match_isotope_df.empty:
        # Normal outcome for blanks and sparse samples
        runtime.logger.info(
            f"No match isotopes found for sample '{sample.sample_item_name}'"
        )

    # Send progress user notification after computing match isotopes
    if notification:
        await send_progress_user_notification(notification, 0.66)

    if not match_isotope_df.empty:
        match_isotope_df["sample_item_id"] = sample.sample_item_id
        persist_isotope_df = select_match_isotopes_to_persist(match_isotope_df)
        if not persist_isotope_df.empty:
            # Convert the DataFrame to a list of Pydantic models
            match_isotopes = [
                MatchIsotopeBase(**row)
                for row in persist_isotope_df.to_dict(orient="records")
            ]
            await create_match_isotopes(match_isotopes)

    # Send progress user notification indicating completion of compute_and_create_sample_match_isotope_data process
    if notification:
        await send_progress_user_notification(notification, 0.95)

    return {
        "match_isotopes": match_isotope_df,
    }
