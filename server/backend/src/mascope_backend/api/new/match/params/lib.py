import pandas as pd
from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_file.name import get_instrument_type, resolve_instrument_type
from mascope_match.params import (
    BaseMatchParams,
    OrbiMatchParams,
    TofMatchParams,
)


def instrument_default_match_params(
    instrument_name: str, instrument_type: str | None = None
) -> BaseMatchParams:
    """Default match parameters for an instrument.

    ``instrument_type`` is the class the reader recorded for the instrument's
    files; pass it whenever the row at hand carries it. Without it the name
    has to say, which names from before the field do and reported names need
    not.

    :param instrument_name: The instrument name
    :type instrument_name: str
    :param instrument_type: "orbi" or "tof" when known
    :type instrument_type: str | None
    :raises ValueError: Neither the type nor the name says which class it is
    :return: The class's default parameters
    :rtype: BaseMatchParams
    """
    if instrument_type is None:
        instrument_type = resolve_instrument_type(instrument_name)
    if instrument_type == "orbi":
        return OrbiMatchParams()
    if instrument_type == "tof":
        return TofMatchParams()
    raise ValueError(
        f"Unknown instrument type {instrument_type!r} for instrument {instrument_name}"
    )


def _instrument_types_in(df: pd.DataFrame) -> dict[str, str]:
    """Instrument class per instrument name, from what the frame carries.

    A recorded ``instrument_type`` column first; else the files' names, whose
    props record the class; else nothing, and the name has to say.

    :param df: Match rows with an ``instrument`` column
    :type df: pd.DataFrame
    :return: Instrument name to class, for the names the frame can settle
    :rtype: dict[str, str]
    """
    if "instrument_type" in df.columns:
        pairs = df[["instrument", "instrument_type"]].dropna().drop_duplicates()
        return dict(zip(pairs["instrument"], pairs["instrument_type"]))
    if "filename" in df.columns:
        types = {}
        pairs = df[["instrument", "filename"]].drop_duplicates("instrument")
        for instrument, filename in pairs.itertuples(index=False):
            try:
                types[instrument] = get_instrument_type(filename)
            except ValueError:
                continue
        return types
    return {}


def isotope_abundance_threshold_expr(
    filter_params_col: ColumnElement,
    instrument: str,
    default_threshold: float,
) -> ColumnElement:
    """Build the effective isotope abundance threshold SQL expression for an ion.

    Resolves the per-ion override stored in ``TargetIon.filter_params`` (keyed by
    instrument name) and falls back to the provided instrument default when the ion
    carries no override. Lets strong-signal reagent ions opt into a lower threshold.

    :param filter_params_col: The ``TargetIon.filter_params`` JSON column.
    :param instrument: Instrument name used as the JSON key for ion-scoped overrides.
    :param default_threshold: Instrument default applied when no override is present.
    :return: A COALESCE SQL expression yielding the effective threshold per row.
    :rtype: ColumnElement
    """
    return func.coalesce(
        filter_params_col[instrument]["isotope_abundance_threshold"].as_float(),
        default_threshold,
    )


async def default_match_params(sample_item_id: str):
    sample = await fetch_sample(sample_item_id)
    return instrument_default_match_params(
        sample.instrument, instrument_type=getattr(sample, "instrument_type", None)
    )


def apply_match_params(
    match_isotope_df, match_params: BaseMatchParams = None
) -> pd.DataFrame:
    """
    Apply filtering logic to a isotope-lvl matches DataFrame.

    :param match_isotope_df: DataFrame containing match isotope data.
    :type match_isotope_df: pd.DataFrame
    :param match_params: Optional; Pydantic model of filtering parameters.
    :type match_params: BaseMatchParams
    :return: DataFrame with applied filters.
    :rtype: pd.DataFrame
    """
    if match_isotope_df.empty:
        return match_isotope_df

    df = match_isotope_df.copy()

    # --- Resolve the effective parameters per row ---
    # Priority: provided match parameters > ion-specific parameters for the
    # sample instrument (filter_params) > instrument defaults.
    param_keys = [
        "mz_tolerance",
        "isotope_ratio_tolerance",
        "peak_min_intensity",
        "probable_match_threshold",
        "possible_match_threshold",
    ]
    provided_params = match_params.model_dump() if match_params else None
    if provided_params:
        params_df = pd.DataFrame(
            {key: provided_params[key] for key in param_keys}, index=df.index
        )
    else:
        instruments = df["instrument"].to_numpy()
        types = _instrument_types_in(df)
        defaults = {
            instrument: instrument_default_match_params(
                instrument, instrument_type=types.get(instrument)
            ).model_dump()
            for instrument in df["instrument"].unique()
        }
        if "filter_params" in df.columns:
            filter_params = df["filter_params"].to_numpy()
            row_params = [
                fp[instrument]
                if isinstance(fp, dict) and instrument in fp
                else defaults[instrument]
                for instrument, fp in zip(instruments, filter_params)
            ]
        else:
            row_params = [defaults[instrument] for instrument in instruments]
        params_df = pd.DataFrame(
            [[params[key] for key in param_keys] for params in row_params],
            index=df.index,
            columns=param_keys,
            dtype=float,
        )

    # --- Apply filtering logic (vectorized) ---
    # Rows with NaN in any field required for filtering are marked with a
    # None match_category and left otherwise untouched.
    valid = ~(
        df["match_mz_error"].isna()
        | df["match_abundance_error"].isna()
        | df["sample_peak_intensity"].isna()
        | df["relative_abundance"].isna()
    )

    within_tolerance = (df["match_mz_error"].abs() <= params_df["mz_tolerance"]) & (
        df["match_abundance_error"].abs() <= params_df["isotope_ratio_tolerance"]
    )
    score_accepted = within_tolerance & (
        df["sample_peak_intensity"] >= params_df["peak_min_intensity"]
    )

    df.loc[valid & ~score_accepted, "match_score"] = 0
    df.loc[valid & ~within_tolerance, "sample_peak_intensity"] = 0

    # Determine match category based on the (filtered) match_score
    match_category = pd.Series(0, index=df.index, dtype=object)
    match_category[df["match_score"] >= params_df["possible_match_threshold"]] = 1
    match_category[df["match_score"] >= params_df["probable_match_threshold"]] = 2
    match_category[~valid] = None
    df["match_category"] = match_category

    return df
