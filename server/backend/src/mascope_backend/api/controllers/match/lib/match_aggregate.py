from typing import Optional, Tuple

import pandas as pd

from mascope_backend.api.controllers.match.lib.match_score_v2 import (
    fit_sample_mass_accuracy,
    ion_score_v2,
    match_score_version,
    sample_noise_floor,
)
from mascope_backend.api.models.target.collections.config import (
    target_collection_config,
)
from mascope_backend.api.new.match.params.lib import (
    instrument_default_match_params,
)
from mascope_file.name import get_instrument_type
from mascope_match.params import (
    DEFAULT_POSSIBLE_MATCH_THRESHOLD,
    DEFAULT_POSSIBLE_MATCH_THRESHOLD_V2,
    DEFAULT_PROBABLE_MATCH_THRESHOLD,
    DEFAULT_PROBABLE_MATCH_THRESHOLD_V2,
    BaseMatchParams,
    unmatched_isotope_params,
)


# Value columns of a match_isotope row fetched for aggregation (everything except
# the sample_item_id / target_isotope_id keys). Used to seed an empty stored frame
# so unmatched reconstruction can run even when a sample has no matched isotopes.
MATCH_ISOTOPE_VALUE_COLUMNS = [
    "match_mz_error",
    "match_abundance_error",
    "sample_peak_intensity",
    "sample_peak_intensity_relative",
    "sample_peak_mz",
    "sample_peak_tof",
    "match_score",
    "signal_to_noise",
]


def snr_columns_json_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Turn NaN in the SNR carrier columns into None before serialization.

    ``signal_to_noise`` rides along on computed and DB-read isotope frames for
    the v2 score and is NaN whenever a row has no signal-to-noise data (files
    without noise data, reconstructed unmatched rows, and rows stored before
    the column existed). NaN is not JSON - starlette refuses to serialize it -
    so it must leave the frame as None.
    """
    df = df.copy()
    for column in ("signal_to_noise", "is_satellite"):
        if column in df.columns and df[column].isna().any():
            df[column] = df[column].astype(object).where(df[column].notna(), None)
    return df


def reconstruct_full_isotope_frame(
    match_isotopes_df: pd.DataFrame,
    samples_df: pd.DataFrame,
    targets_df: pd.DataFrame,
) -> pd.DataFrame:
    """Re-add unmatched isotopes (match_score 0) that are no longer stored.

    ``match_isotope`` persists only isotopes with a real peak match. Unmatched
    isotopes (no peak within the match window) carry solely constant/derivable
    placeholder values, so they are reconstructed here from the target isotopes
    instead of being stored. This keeps two things identical to when the rows were
    stored:

    - the Match-tab isotope table shows every expected isotope, and
    - higher-level aggregates are unchanged, since they sum
      ``match_score * relative_abundance`` and peak intensity, to which an unmatched
      row (score 0, intensity 0) contributes exactly 0.

    The reconstructed population mirrors match computation: per sample, the target
    isotopes of the correct instrument resolution whose ``relative_abundance`` is at
    or above the effective abundance threshold (per-ion ``filter_params`` override,
    else the instrument default).

    :param match_isotopes_df: Stored (matched) match_isotope rows keyed by
        (sample_item_id, target_isotope_id) with the match_* / sample_peak_* value
        columns. May be empty (but must carry the expected columns).
    :param samples_df: Samples in scope; needs sample_item_id, instrument, filename.
    :param targets_df: Candidate target isotopes; needs target_isotope_id,
        relative_abundance, resolution, filter_params, mz.
    :return: match_isotopes_df with reconstructed unmatched rows appended (same
        columns; reconstructed rows' sample_peak_mz set to the target m/z).
    """
    if samples_df.empty or targets_df.empty:
        return match_isotopes_df

    key_cols = ["sample_item_id", "target_isotope_id"]
    value_columns = [c for c in match_isotopes_df.columns if c not in key_cols]

    # Build the expected (sample_item_id, target_isotope_id) population, mirroring
    # match computation's per-sample resolution + abundance-threshold filtering.
    expected_frames = []
    for sample in samples_df.itertuples(index=False):
        instrument = sample.instrument
        resolution = "LOW" if get_instrument_type(sample.filename) == "tof" else "HIGH"
        default_threshold = instrument_default_match_params(
            instrument
        ).isotope_abundance_threshold

        applicable = targets_df[targets_df["resolution"] == resolution]
        if applicable.empty:
            continue

        def _effective_threshold(filter_params: object) -> float:
            # Per-ion override for this instrument, else the instrument default.
            if (
                isinstance(filter_params, dict)
                and isinstance(filter_params.get(instrument), dict)
                and "isotope_abundance_threshold" in filter_params[instrument]
            ):
                return filter_params[instrument]["isotope_abundance_threshold"]
            return default_threshold

        thresholds = applicable["filter_params"].map(_effective_threshold)
        applicable = applicable[applicable["relative_abundance"] >= thresholds]
        if applicable.empty:
            continue

        expected_frames.append(
            pd.DataFrame(
                {
                    "sample_item_id": sample.sample_item_id,
                    "target_isotope_id": applicable["target_isotope_id"].to_numpy(),
                    "_target_mz": applicable["mz"].to_numpy(),
                }
            )
        )

    if not expected_frames:
        return match_isotopes_df

    # One row per (sample, isotope); the same isotope can appear in targets_df
    # multiple times (shared across collections), so drop duplicate keys.
    expected_df = pd.concat(expected_frames, ignore_index=True).drop_duplicates(
        subset=key_cols
    )

    # Union of stored and expected keys so no stored (matched) row is ever dropped,
    # even one outside the recomputed expected population.
    keys = (
        pd.concat([match_isotopes_df[key_cols], expected_df[key_cols]])
        .drop_duplicates()
        .reset_index(drop=True)
        .merge(expected_df, on=key_cols, how="left")
    )
    full_df = keys.merge(match_isotopes_df, on=key_cols, how="left")

    # A stored row always has a non-null match_score (NOT NULL column); a
    # reconstructed unmatched row is missing every value column after the merge.
    unmatched_mask = full_df["match_score"].isna()
    defaults = unmatched_isotope_params.model_dump()
    for column in value_columns:
        if column == "sample_peak_mz":
            # Unmatched isotopes report the target m/z as the (absent) peak m/z,
            # matching assign_defaults_to_unmatched at compute time.
            full_df.loc[unmatched_mask, column] = full_df.loc[
                unmatched_mask, "_target_mz"
            ]
        elif column in defaults:
            full_df.loc[unmatched_mask, column] = defaults[column]

    return full_df.drop(columns=["_target_mz"])


# The v2 (fit) score needs per-isotopologue evidence. Lighter aggregation paths (and
# some callers) do not carry these columns; there v2 is skipped and v1 stands.
_V2_REQUIRED_COLS = frozenset(
    {"match_mz_error", "relative_abundance", "sample_peak_intensity"}
)


def effective_match_score_version(isotope_df: pd.DataFrame) -> int:
    """The scale the ion ``match_score`` aggregated from ``isotope_df`` will be on.

    The process-wide switch (:func:`match_score_version`) is only half the answer: v2
    is skipped on frames that do not carry the per-isotopologue evidence it needs, and
    an empty frame has nothing to score, so those aggregates stay on the v1 scale even
    with the switch flipped. Score selection and category thresholds must both branch
    on *this*, never on the switch alone -- the v2 bands are deliberately lower
    (possible 0.5 vs v1's 0.7), so a frame scored v1 but thresholded v2 silently
    promotes every ion in [0.5, 0.7) to "possible".
    """
    if match_score_version() != 2:
        return 1
    if isotope_df.empty or not _V2_REQUIRED_COLS.issubset(isotope_df.columns):
        return 1
    return 2


def _can_score_v2(df: pd.DataFrame) -> bool:
    """True iff the v2 fit score can actually be computed from this frame.

    The emptiness half of :func:`effective_match_score_version` is load-bearing here:
    on an empty-but-columned frame ``groupby(...).apply(...)`` yields a DataFrame
    rather than a Series, and the ``.rename("match_score_v2")`` that follows is then
    read as an index mapper and raises.
    """
    return effective_match_score_version(df) == 2


def _v2_ion_scores(isotope_df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Per-ion v2 fit scores for ``isotope_df``, keyed by ``keys``.

    The mass-accuracy fit and the noise floor are measured PER SAMPLE, which is what
    their names and docstrings promise: ``fit_sample_mass_accuracy`` returns one
    file's systematic mass offset and its robust spread, and ``sample_noise_floor``
    one file's baseline. An aggregation frame here routinely spans a whole batch --
    ``aggregate_matches`` is invoked with a ``sample_batch_id`` -- so it can mix files
    and even instrument classes. Fitting once over that union would subtract one
    batch-wide mu from a well-centred file's mass errors and stretch or squeeze every
    Gaussian to the batch-average sigma, making an ion's score depend on which other
    samples happened to be aggregated with it. Grouping by ``sample_item_id`` fixes
    both, and separates instruments as a side effect (a sample has exactly one file
    and one instrument). This mirrors the per-sample engine path in
    ``api/new/peak_assignments/engine.py``, so batch and single-sample scoring agree.

    Grouping is on ``keys`` only -- the same narrow id columns the v1 aggregation
    groups on -- rather than on every label column that happens to ride along, and
    ``include_groups=False`` keeps the key columns out of the per-group frame
    (pandas 2.2 deprecation; the default flips in pandas 3).

    The per-sample split is skipped when ``keys`` does not carry ``sample_item_id``:
    such a key set cannot name a per-sample score in the first place, so the caller's
    own v1 aggregation is already asserting the frame is one sample, and splitting it
    would only produce duplicate keys for the merge to fan out on.
    """
    chunks = (
        [group for _, group in isotope_df.groupby("sample_item_id", sort=False)]
        if "sample_item_id" in keys
        else [isotope_df]
    )
    scores = []
    for chunk in chunks:
        mu, sigma = fit_sample_mass_accuracy(chunk)
        noise = sample_noise_floor(chunk)
        scores.append(
            chunk.groupby(keys, sort=False, dropna=False).apply(
                lambda g: ion_score_v2(g, sigma_ppm=sigma, mu=mu, noise=noise),
                include_groups=False,
            )
        )
    return pd.concat(scores).rename("match_score_v2").reset_index()


async def set_ions_match_category(
    match_ions_df: pd.DataFrame,
    match_params: Optional[BaseMatchParams] = None,
    *,
    score_version: Optional[int] = None,
) -> pd.DataFrame:
    """Set the match_category field for each ion in the DataFrame.

    This function determines the match_category for each ion based on match score and
    predefined thresholds. It uses provided filters, or defaults if none are provided,
    and falls back to ion-specific filters when available.

    :param match_ions_df: DataFrame containing ion data with match scores.
    :type match_ions_df: pd.DataFrame
    :param match_params: Optional ion-specific filter parameters.
    :type match_params: Optional[BaseMatchParams]
    :param score_version: The scale ``match_score`` is actually on, as reported by
        :func:`effective_match_score_version` for the isotope frame it was aggregated
        from. Callers that scored the frame themselves must pass it, so the bands and
        the score cannot disagree. ``None`` falls back to the process-wide switch,
        which is only correct for callers that apply v2 unconditionally.
    :type score_version: Optional[int]
    :return: DataFrame with match_category field set for each ion.
    :rtype: pd.DataFrame
    """
    if match_ions_df.empty:
        match_ions_df["match_category"] = pd.Series(dtype=int)
        return match_ions_df

    # --- Resolve per-ion thresholds (vectorized) ---
    if match_params:
        # Provided parameters take priority for every ion.
        probable = pd.Series(
            match_params.probable_match_threshold, index=match_ions_df.index
        )
        possible = pd.Series(
            match_params.possible_match_threshold, index=match_ions_df.index
        )
    else:
        # Ion-specific overrides (filter_params keyed by instrument), falling back to
        # the score-version-aware module defaults: v2 is a fit-quality score whose
        # category bands differ from v1's cutoffs (see params.py).
        if (score_version if score_version is not None else match_score_version()) == 2:
            default_probable = DEFAULT_PROBABLE_MATCH_THRESHOLD_V2
            default_possible = DEFAULT_POSSIBLE_MATCH_THRESHOLD_V2
        else:
            default_probable = DEFAULT_PROBABLE_MATCH_THRESHOLD
            default_possible = DEFAULT_POSSIBLE_MATCH_THRESHOLD
        probable = pd.Series(default_probable, index=match_ions_df.index, dtype=float)
        possible = pd.Series(default_possible, index=match_ions_df.index, dtype=float)
        if "filter_params" in match_ions_df.columns:
            for i, (instrument, filter_params) in enumerate(
                zip(match_ions_df["instrument"], match_ions_df["filter_params"])
            ):
                if isinstance(filter_params, dict) and instrument in filter_params:
                    ion_filters = filter_params[instrument]
                    idx = match_ions_df.index[i]
                    probable.at[idx] = ion_filters["probable_match_threshold"]
                    possible.at[idx] = ion_filters["possible_match_threshold"]

    # Determine match_category from match_score against the resolved thresholds.
    match_score = match_ions_df["match_score"]
    category = pd.Series(0, index=match_ions_df.index, dtype=int)
    category[match_score >= possible] = 1
    category[match_score >= probable] = 2
    match_ions_df["match_category"] = category

    return match_ions_df


async def set_alarm_mode(dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Set the alarm_mode field for each entry in the DataFrame based on the list of
    provided alarms_list.

    :param dataframe: DataFrame containing sample/batch match filter data.
    :type dataframe: pd.DataFrame
    :return: DataFrame with alarm_mode field set.
    :rtype: pd.DataFrame
    """
    alarms_list = target_collection_config.APP_ALARMING_COLLECTION_TYPES
    # Set alarm_mode based on whether the target_collection_type is in the alarms_list
    dataframe["alarm_mode"] = dataframe["target_collection_type"].apply(
        lambda x: True if x in alarms_list else False
    )
    return dataframe


async def aggregate_match_isotopes(
    filtered_match_isotope_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Filter the fields of aggregated filtered match isotopes dataframe.
    (used for backwards compatibility of get_sample_aggregate_matches, may be removed
    further)

    This function processes the sample/batch match filter dataframe to aggregate isotope
    data. It prepares two DataFrames:
    1) match_isotopes_data_df with detailed data for further aggregation,
    2) match_isotopes_df with reduced data for frontend display.

    :param filtered_match_isotope_df: DataFrame containing sample/batch match filter
      data to aggregate.
    :type filtered_match_isotope_df: pd.DataFrame
    :return: Tuple of DataFrames with aggregated matchIsotopes data.
    :rtype: (pd.DataFrame, pd.DataFrame)
      (compute_match_isotopes, apply_match_params and combine data,
      aggregate_match_isotopes)
    """
    # Select relevant columns for detailed aggregation (backend processing)
    match_isotopes_data_df = filtered_match_isotope_df.loc[
        :,
        [
            "sample_item_id",
            "filename",
            "instrument",
            "sample_item_name",
            "sample_item_type",
            "target_collection_id",
            "target_collection_name",
            "target_collection_description",
            "target_collection_type",
            "target_compound_id",
            "target_compound_name",
            "target_compound_formula",
            "target_ion_id",
            "target_ion_formula",
            "filter_params",
            "ionization_mechanism",
            "target_isotope_id",
            "mz",
            "relative_abundance",
            "match_mz_error",
            "match_abundance_error",
            "sample_peak_intensity",
            "sample_peak_intensity_relative",
            "sample_peak_mz",
            "sample_peak_tof",
            "match_category",
            "match_score",
        ],
    ]

    # Prepare a simplified DataFrame for frontend
    match_isotopes_df = match_isotopes_data_df.drop(
        columns=[
            "sample_item_type",
            "target_ion_formula",
            "ionization_mechanism",
            "filter_params",
            "target_compound_name",
            "target_compound_formula",
            "target_collection_id",
            "target_collection_name",
            "target_collection_description",
            "target_collection_type",
        ]
    ).drop_duplicates(subset=["target_isotope_id", "sample_item_id"])

    return match_isotopes_data_df, match_isotopes_df


async def aggregate_match_ions(
    filtered_match_isotope_df: pd.DataFrame,
    match_params: Optional[BaseMatchParams] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Aggregate fields for match ions from aggregated filtered match isotopes dataframe.
    Provided filters are passed to set_ions_match_category, if none match_params are
    provided, stored ion-specific or default params will be applied.

    It prepares two DataFrames:
    1) match_ions_data_df with detailed data for correct further aggregation on
       collection/sample level,
    2) match_ions_df with reduced data and dropped duplicates (same compound in
       different collections).

    The match_score is calculated as a weighted sum of individual isotopes' match scores
    weighted by their relative abundance.
    The sample_peak_intensity is summed across all isotopes in the group, the _sum is
    added to the field name.

    :param filtered_match_isotope_df: DataFrame containing isotope data to aggregate.
    :type filtered_match_isotope_df: pd.DataFrame
    :param match_params: Optional ion-specific filter parameters to
      set_ions_match_category.
    :type match_params: Optional[BaseMatchParams]
    :return: Tuple of DataFrames with aggregated match ions data.
    :rtype: (pd.DataFrame, pd.DataFrame)
    """
    # Precompute the abundance-weighted score once so the groupby can sum it
    # directly instead of re-indexing relative_abundance per group.
    weighted = filtered_match_isotope_df.assign(
        _weighted_score=filtered_match_isotope_df["match_score"]
        * filtered_match_isotope_df["relative_abundance"]
    )
    # Group on the id columns only - every label column is functionally
    # dependent on them (same id => same label), so aggregating labels as
    # "first" yields the same rows while the groupby hashes 4 narrow keys
    # instead of 15 wide string columns.
    match_ions_data_df = (
        weighted.groupby(
            [
                "sample_item_id",
                "target_ion_id",
                "target_compound_id",
                "target_collection_id",
            ]
        )
        .agg(
            match_score=("_weighted_score", "sum"),
            sample_peak_intensity_sum=("sample_peak_intensity", "sum"),
            filter_params=("filter_params", "first"),
            sample_item_name=("sample_item_name", "first"),
            sample_item_type=("sample_item_type", "first"),
            filename=("filename", "first"),
            instrument=("instrument", "first"),
            target_ion_formula=("target_ion_formula", "first"),
            ionization_mechanism=("ionization_mechanism", "first"),
            target_compound_formula=("target_compound_formula", "first"),
            target_compound_name=("target_compound_name", "first"),
            target_collection_name=("target_collection_name", "first"),
            target_collection_description=("target_collection_description", "first"),
            target_collection_type=("target_collection_type", "first"),
        )
        .reset_index()
    )
    # Phase C experiment: optionally replace the per-ion match_score with the
    # consolidated mascope_tools v2 score (detectability-gated, SNR-aware, calibrated).
    # Additive + gated: v1 above is computed unchanged; v2 overwrites only when enabled.
    score_version = effective_match_score_version(filtered_match_isotope_df)
    if score_version == 2:
        # The same 4 narrow id keys the v1 groupby above uses; the label columns are
        # functionally dependent on them, so grouping on them too only widens the hash.
        keys = [
            "sample_item_id",
            "target_ion_id",
            "target_compound_id",
            "target_collection_id",
        ]
        v2 = _v2_ion_scores(filtered_match_isotope_df, keys)
        match_ions_data_df = match_ions_data_df.merge(v2, on=keys, how="left")
        match_ions_data_df["match_score"] = match_ions_data_df["match_score_v2"].fillna(
            match_ions_data_df["match_score"]
        )
        match_ions_data_df = match_ions_data_df.drop(columns=["match_score_v2"])

    # Prepare a simplified DataFrame for storing in database (keep instrument and
    # match_params for correct set_ions_match_category)
    # Drop duplicates for match ions based on target_ion_id for each sample, so each
    # sample would have the unique match ions
    # (even if there is same compound=>ion present in different target collections of
    # the sample)
    match_ions_df = match_ions_data_df.drop(
        columns=[
            "target_compound_id",
            "target_collection_id",
            "target_collection_name",
            "target_collection_description",
            "target_collection_type",
        ]
    ).drop_duplicates(subset=["target_ion_id", "sample_item_id"])

    # set match_category field for ions, banded for the scale the score actually
    # landed on (v2 bands only apply when the v2 score was actually computed)
    match_ions_data_df = await set_ions_match_category(
        match_ions_data_df, match_params, score_version=score_version
    )
    match_ions_df = await set_ions_match_category(
        match_ions_df, match_params, score_version=score_version
    )

    return match_ions_data_df, match_ions_df


def aggregate_match_ions_light(
    filtered_match_isotope_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Aggregate fields for match ions from the lighter then in main pipeline
    (aggregate_match_ions) computed and filtered match isotope DataFrame.
    Used in the aggregate_sample_match_compound to aggregate simple match_ions_data_df
    after compute_match_isotopes and apply_match_params.

    This function groups the filtered match isotope DataFrame by 'target_ion_id' and
    aggregates relevant data, such as summing up 'sample_peak_intensity' and computing
    a weighted 'match_score'.

    :param filtered_match_isotope_df: DataFrame containing filtered match isotope data.
    :type filtered_match_isotope_df: pd.DataFrame
    :return: DataFrame with aggregated match ions data.
    :rtype: pd.DataFrame
    """
    weighted = filtered_match_isotope_df.assign(
        _weighted_score=filtered_match_isotope_df["match_score"]
        * filtered_match_isotope_df["relative_abundance"]
    )
    match_ions_df = (
        weighted.groupby("target_ion_id")
        .agg(
            match_score=("_weighted_score", "sum"),
            sample_peak_intensity_sum=("sample_peak_intensity", "sum"),
        )
        .reset_index()
    )

    # Phase C experiment: optionally replace per-ion match_score with the v2 score
    # (additive + gated; v1 unchanged).
    if _can_score_v2(filtered_match_isotope_df):
        v2 = _v2_ion_scores(filtered_match_isotope_df, ["target_ion_id"])
        match_ions_df = match_ions_df.merge(v2, on="target_ion_id", how="left")
        match_ions_df["match_score"] = match_ions_df["match_score_v2"].fillna(
            match_ions_df["match_score"]
        )
        match_ions_df = match_ions_df.drop(columns=["match_score_v2"])

    return match_ions_df


async def aggregate_match_compounds(
    match_ions_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Aggregate fields for match compounds from aggregated match ions dataframe.

    This function sorts the ions dataframe by match_category and match_score in
    descending order and then groups by target_compound_id and other relevant fields to
    compute the aggregated values for match_score, match_category,
    sample_peak_intensity_sum. It preserves the highest match_score of ion from the most
    alarming match_category (the most alarming ion) and sums up the
    sample_peak_intensity_sum for the entire group.

    It prepares two DataFrames:
    1) match_compounds_data_df with detailed data for correct further aggregation on
       collection/sample lvl,
    2) match_compounds_df with reduced data and dropped duplicates (same compound in
       different collections).


    :param match_ions_df: DataFrame containing ion data to aggregate.
    :type match_ions_df: pd.DataFrame
    :return: pandas DataFrame with aggregated match compounds data.
    :rtype: pd.DataFrame
    """
    # Group on the id columns only - label columns are functionally dependent
    # on them and aggregate as "first" (see aggregate_match_ions).
    match_compounds_data_df = (
        match_ions_df.sort_values(
            by=["match_category", "match_score"], ascending=[False, False]
        )
        .groupby(
            [
                "target_compound_id",
                "sample_item_id",
                "target_collection_id",
            ],
            sort=False,
        )
        .agg(
            # The frame is pre-sorted by (match_category, match_score) desc, so
            # "first" preserves the most alarming row's score and category.
            match_score=("match_score", "first"),
            match_category=("match_category", "first"),
            sample_peak_intensity_sum=("sample_peak_intensity_sum", "sum"),
            target_compound_name=("target_compound_name", "first"),
            target_compound_formula=("target_compound_formula", "first"),
            filename=("filename", "first"),
            sample_item_name=("sample_item_name", "first"),
            sample_item_type=("sample_item_type", "first"),
            target_collection_name=("target_collection_name", "first"),
            target_collection_description=("target_collection_description", "first"),
            target_collection_type=("target_collection_type", "first"),
        )
        .reset_index()
    )
    # Explicitly cast match_category to int
    match_compounds_data_df["match_category"] = match_compounds_data_df[
        "match_category"
    ].astype(int)

    # Prepare a simplified DataFrame for storing in database
    # Drop duplicates for match compounds based on target_ion_id for each sample, so
    # each sample would have the unique match compounds
    # (even if there is same compound present in different target collections of the
    # sample)
    match_compounds_df = match_compounds_data_df.drop(
        columns=[
            "target_collection_id",
            "target_collection_name",
            "target_collection_description",
            "target_collection_type",
        ]
    ).drop_duplicates(subset=["target_compound_id", "sample_item_id"])

    return match_compounds_data_df, match_compounds_df


def aggregate_match_compounds_light(match_ions_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate fields for match compounds from the lighter then in main pipeline
    (aggregate_match_compounds) match ions DataFrame. Used in the
    aggregate_sample_match_compound to aggregate simpler match_compounds_data_df from
    result of aggregate_match_ions_light.

    This function groups the match ions DataFrame by 'target_compound_id' and aggregates
    relevant data, such as computing a weighted 'match_score' and summing up
    'sample_peak_intensity'.

    :param match_ions_df: DataFrame containing match ions data.
    :type match_ions_df: pd.DataFrame
    :return: DataFrame with aggregated match compounds data.
    :rtype: pd.DataFrame
    """
    match_compounds_df = (
        match_ions_df.groupby("target_compound_id")
        .agg(
            match_score=("match_score", "max"),
            sample_peak_intensity_sum=("sample_peak_intensity_sum", "sum"),
        )
        .reset_index()
    )

    return match_compounds_df


async def aggregate_match_collections(compounds_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate fields for match collections from aggregated match compounds dataframe.

    This function sorts the compounds dataframe by match_category and match_score in
    descending order and then groups by target_collection_id and other relevant fields
    to compute the aggregated values for match_score, match_category,
    sample_peak_intensity_sum. It preserves the highest match_score of compound from the
    most alarming match_category (the most alarming compound in collection) and sums up
    the sample_peak_intensity_sum for the entire group.

    :param compounds_df: DataFrame containing aggregated match compound data.
    :type compounds_df: pd.DataFrame
    :return: DataFrame with aggregated match collections data.
    :rtype: pd.DataFrame
    """
    match_collections_df = (
        compounds_df.sort_values(
            by=["match_category", "match_score"], ascending=[False, False]
        )
        .groupby(
            [
                "sample_item_id",
                "target_collection_id",
                "target_collection_name",
                "target_collection_description",
                "target_collection_type",
            ],
            sort=False,
        )
        .agg(
            # Pre-sorted desc, so "first" is the most alarming compound.
            match_score=("match_score", "first"),
            match_category=("match_category", "first"),
            sample_peak_intensity_sum=("sample_peak_intensity_sum", "sum"),
        )
        .reset_index()
    )
    # Explicitly cast match_category to int
    match_collections_df["match_category"] = match_collections_df[
        "match_category"
    ].astype(int)

    return match_collections_df


async def aggregate_match_samples(compounds_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate fields for match samples from compounds dataframe.

    This function sorts the compounds dataframe by alarm_mode, match_category and
    match_score in descending order and then groups by sample_item_id and other relevant
    fields to compute the aggregated values for match_score, match_category,
    sample_peak_intensity_sum. It preserves the highest match_score of compound where
    alarm_mode and match_category is the highest (the most alarming compound of sample)
    and sums up the sample_peak_intensity_sum for the entire group.

    :param compounds_df: DataFrame containing aggregated match compound data.
    :type compounds_df: pd.DataFrame
    :return: pandas DataFrame with aggregated match samples data.
    :rtype: pd.DataFrame
    """
    # Set the alarm_mode based on alarms_list and target_collection_type
    compounds_df = await set_alarm_mode(compounds_df)
    match_samples_df = (
        compounds_df.sort_values(
            by=["alarm_mode", "match_category", "match_score"],
            ascending=[False, False, False],
        )
        .drop_duplicates(subset=["target_compound_id", "sample_item_id"])
        .groupby(
            [
                "filename",
                "sample_item_id",
                "sample_item_name",
            ],
            sort=False,
        )
        .agg(
            # Pre-sorted by (alarm_mode, match_category, match_score) desc, so
            # "first" is the most alarming compound of the sample.
            match_score=("match_score", "first"),
            match_category=("match_category", "first"),
            sample_peak_intensity_sum=("sample_peak_intensity_sum", "sum"),
        )
        .reset_index()
    )
    # Cast match_category to int
    match_samples_df["match_category"] = match_samples_df["match_category"].astype(int)

    return match_samples_df


async def compile_samples_df(
    samples_df: pd.DataFrame,
    match_samples_df: pd.DataFrame,
) -> pd.DataFrame:
    """Compile samples dataframe data (from database SampleView) with aggregated match
    results.

    This function merges the samples dataframe with the aggregated match results from
    the match_samples dataframe. It adds the match_score, match_category,
    sample_peak_intensity_sum fields to each sample. The 'matched' field is calculated
    to indicate whether the sample has any match results.

    The aggregation logic in match_samples_df ensures that each sample's aggregated
    fields represent:
      - The highest match_score of compound from the most alarming match_category
        (the most alarming compound of sample)
      - The sum of sample_peak_intensity for all compounds of the sample

    NaN values in aggregated fields are replaced with 0, indicating no matches or data
    for those fields.

    :param samples_df: DataFrame containing original database sample data.
    :type samples_df: pd.DataFrame
    :param match_samples_df: DataFrame containing aggregated match results for samples.
    :type match_samples_df: pd.DataFrame
    :return: DataFrame with sample data and aggregated match results.
    :rtype: pd.DataFrame
    """
    # Select relevant columns from match_samples_df
    match_samples_df_short = match_samples_df[
        [
            "sample_item_id",
            "match_score",
            "match_category",
            "sample_peak_intensity_sum",
        ]
    ]

    # Merge with samples_df
    samples_df = pd.merge(
        samples_df, match_samples_df_short, how="left", on="sample_item_id"
    )

    # Add matched column
    samples_df["matched"] = samples_df["match_score"].apply(
        lambda x: int(not pd.isna(x))
    )

    # Replace NaNs with 0
    samples_df[
        [
            "tic",
            "match_score",
            "match_category",
            "sample_peak_intensity_sum",
        ]
    ] = samples_df[
        [
            "tic",
            "match_score",
            "match_category",
            "sample_peak_intensity_sum",
        ]
    ].fillna(0)

    return samples_df
