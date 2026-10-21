"""
The synthetic campaign recovers what it was built from.

Every known answer in :class:`fixtures.campaign.Truth` is measured back from
the tables alone, the way a view would, so a later test that asserts a chart
against the truth asserts against something the data really holds.
"""

import dataclasses

import numpy as np
import pandas as pd
import pytest
from fixtures.campaign import (
    DAY,
    HALIDE_NEGATIVE,
    TABLES,
    Campaign,
    EpisodeSpec,
    content_digest,
    generate,
    parquet_bytes,
    write_parquet,
)


COLUMNS = {
    "samples": [
        "sample_item_id",
        "sample_batch_id",
        "sample_file_id",
        "t_mid_utc",
        "datetime_utc",
        "t0",
        "t1",
        "tic",
        "polarity",
        "instrument",
        "instrument_type",
        "filename",
    ],
    "files": [
        "sample_file_id",
        "datetime_utc",
        "length",
        "instrument",
        "acquisition_timezone",
        "in_batch",
    ],
    "ion_series": ["sample_item_id", "series_key", "intensity", "match_category"],
    "peak_series": [
        "sample_item_id",
        "series_key",
        "intensity",
        "tier",
        "batch_peak_id",
        "run_id",
    ],
    "species": [
        "series_key",
        "name",
        "formula",
        "ion_formula",
        "mechanism",
        "class",
        "mz",
        "n_c",
        "n_h",
        "n_n",
        "n_o",
        "n_s",
        "o_c",
        "h_c",
        "osc",
    ],
}

# How far either side of an episode its edges are looked for.
MARGIN = pd.Timedelta(hours=6)

# The tables' values at seed 0. A change to the generator that moves any of
# them changes this: update it on purpose, never to get a pass, since every
# later test's numbers rest on these tables.
DIGESTS = {
    "halide_negative": "036e29ed7c8319e8eadbfe71b405fda7ebb4b942e717ec1e2cd50ff9cc6cb8e8",
    "positive": "001d3c8b4b101401b59786c0dcb15b41b8a9108f2d78854cbbed1198067c4871",
}


def _series(campaign: Campaign, table: str, key: str) -> pd.Series:
    """One series' intensity on the campaign's full minute grid, by t_mid.

    Minutes without a sample (the gap day) are NaN, as below-detection is.
    """
    rows = campaign.tables[table]
    rows = rows[rows["series_key"] == key]
    samples = campaign.tables["samples"][["sample_item_id", "t_mid_utc"]]
    joined = rows.merge(
        samples.astype({"sample_item_id": "str"}),
        on="sample_item_id",
        how="inner",
    )
    truth = campaign.truth
    grid = pd.date_range(truth.start + truth.cadence / 2, truth.end, freq=truth.cadence)
    return joined.set_index("t_mid_utc")["intensity"].reindex(grid)


def _join(campaign: Campaign, table: str) -> pd.DataFrame:
    samples = campaign.tables["samples"][
        ["sample_item_id", "sample_batch_id", "t_mid_utc"]
    ]
    rows = campaign.tables[table].astype({"sample_item_id": "str"})
    return rows.merge(samples, on="sample_item_id", how="inner")


def _circular_distance(a: int, b: int) -> int:
    d = abs(a - b) % 24
    return min(d, 24 - d)


# --- Shape ------------------------------------------------------------------


def test_tables_carry_the_store_columns(campaign):
    assert tuple(campaign.tables) == TABLES
    for name, columns in COLUMNS.items():
        assert list(campaign.tables[name].columns) == columns, name


def test_one_sample_a_minute_over_a_month(campaign):
    samples = campaign.tables["samples"]
    truth = campaign.truth
    assert truth.end - truth.start == pd.Timedelta(days=30)
    assert len(samples) == 29 * 1440
    assert samples["sample_item_id"].is_unique
    step = samples["t_mid_utc"].diff().dropna()
    # Every step is a minute, except the one across the gap day.
    assert (step == pd.Timedelta(minutes=1)).sum() == len(samples) - 2
    assert step.max() == pd.Timedelta(days=1, minutes=1)
    # A sample's time is its file's start plus the middle of its window.
    middle = pd.to_timedelta((samples["t0"] + samples["t1"]) / 2, unit="s")
    assert (samples["datetime_utc"] + middle == samples["t_mid_utc"]).all()
    # One batch per UTC day, its samples all on that day.
    per_batch = samples.groupby("sample_batch_id", observed=True)["t_mid_utc"]
    assert per_batch.size().eq(1440).all()
    assert (per_batch.min().dt.floor("D") == per_batch.max().dt.floor("D")).all()
    assert per_batch.ngroups == 29


def test_fifty_species_with_named_cycles(campaign):
    species = campaign.tables["species"]
    truth = campaign.truth
    before = {k for k in truth.cycles if k.startswith("formula:")}
    renamed_to = {new for _, new in truth.run_change.readings}
    assert len(before - renamed_to) == 50
    kinds = {truth.cycles[k].kind for k in before}
    assert kinds == {"day", "night", "flat"}
    keys = set(species["series_key"])
    used = set(campaign.tables["peak_series"]["series_key"].astype("str")) | set(
        campaign.tables["ion_series"]["series_key"].astype("str")
    )
    assert keys == used
    assert species["series_key"].is_unique
    chem = species[species["mechanism"] != "reagent"]
    assert np.allclose(chem["o_c"], chem["n_o"] / chem["n_c"])
    assert np.allclose(chem["h_c"], chem["n_h"] / chem["n_c"])
    assert np.allclose(chem["osc"], 2 * chem["o_c"] - chem["h_c"])


# --- Determinism ------------------------------------------------------------


def test_same_seed_same_parquet_bytes(campaign):
    again = generate(campaign.params, seed=campaign.seed)
    other = generate(campaign.params, seed=campaign.seed + 1)
    for name in TABLES:
        assert parquet_bytes(again.tables[name]) == parquet_bytes(
            campaign.tables[name]
        ), name
    assert parquet_bytes(other.tables["peak_series"]) != parquet_bytes(
        campaign.tables["peak_series"]
    )


def test_content_is_pinned(campaign):
    assert content_digest(campaign) == DIGESTS[campaign.params.name]


def test_parquet_round_trip(campaign, tmp_path):
    paths = write_parquet(campaign, tmp_path)
    assert sorted(paths) == sorted(TABLES)
    for name, path in paths.items():
        back = pd.read_parquet(path)
        table = campaign.tables[name]
        # equals() rather than assert_frame_equal, which takes a minute over
        # the categoricals of two million rows.
        assert list(back.columns) == list(table.columns), name
        assert list(back.dtypes) == list(table.dtypes), name
        assert back.equals(table), name


# --- Known answers ----------------------------------------------------------


def test_gap_day_has_files_but_no_samples_or_batch(campaign):
    gap = campaign.truth.gap
    files = campaign.tables["files"]
    samples = campaign.tables["samples"]
    unfiled = files[~files["in_batch"]]
    assert len(unfiled) == gap.files == 144
    assert unfiled["datetime_utc"].min() == gap.start
    assert (
        unfiled["datetime_utc"].max()
        + pd.to_timedelta(unfiled["length"].iloc[-1], unit="s")
        == gap.end
    )
    in_gap = samples["t_mid_utc"].between(gap.start, gap.end, inclusive="left")
    assert not in_gap.any()
    # The files outside the gap all carry samples; the gap's carry none.
    with_samples = set(samples["sample_file_id"])
    assert set(files.loc[files["in_batch"], "sample_file_id"]) == with_samples
    assert not set(unfiled["sample_file_id"]) & with_samples


def test_diurnal_cycles_peak_where_built(campaign):
    truth = campaign.truth
    rows = _join(campaign, "peak_series")
    hour = rows["t_mid_utc"].dt.tz_convert(truth.timezone).dt.hour
    medians = (
        rows.assign(hour=hour.to_numpy())
        .groupby(["series_key", "hour"], observed=True)["intensity"]
        .median()
        .unstack("hour")
    )
    renamed = {k for pair in truth.run_change.readings for k in pair}
    for key, by_hour in medians.iterrows():
        if key in renamed:
            continue  # each half covers half the month; checked below
        cycle = truth.cycles[key]
        if cycle.kind == "flat":
            assert by_hour.max() / by_hour.min() < 1.1, key
            continue
        peak = int(by_hour.idxmax())
        assert _circular_distance(peak, cycle.peak_hour) <= 1, key
        # The names hold: a day cycle peaks around midday, a night one
        # around midnight, local time.
        noon = 12 if cycle.kind == "day" else 0
        assert _circular_distance(peak, noon) <= 4, key
        trough = by_hour.min()
        if trough > 4 * truth.detection_limit:
            ratio = by_hour.max() / trough
            assert ratio == pytest.approx(cycle.amplitude, rel=0.2), key


def test_episodes_start_end_and_amplitude(campaign):
    truth = campaign.truth
    window = pd.Timedelta(minutes=15)
    in_any = set()
    for episode in truth.episodes:
        edged = 0
        for key in episode.series_keys:
            table = "ion_series" if key.startswith("ion:") else "peak_series"
            series = _series(campaign, table, key)
            # Against the same clock time a day earlier, the cycle cancels.
            ratio = (series / series.shift(1440)).rolling(31, center=True).median()
            inside = ratio[episode.start : episode.end - truth.cadence]
            assert inside.median() == pytest.approx(episode.factor, rel=0.1), key
            in_any.add(key)
            # The edges show where the day before was above the detection
            # limit all through; below it, there is nothing to compare with.
            around = slice(episode.start - MARGIN, episode.end + MARGIN)
            if series.shift(1440)[around].isna().any():
                continue
            above = ratio[around][ratio[around] > np.sqrt(episode.factor)]
            assert abs(above.index.min() - episode.start) <= window, key
            assert abs(above.index.max() + truth.cadence - episode.end) <= window, key
            edged += 1
        assert edged >= 2
    # A species in no episode, read by one run all month, stays at its level
    # through both.
    renamed = {k for pair in truth.run_change.readings for k in pair}
    calm = next(
        k
        for k in truth.cycles
        if k.startswith("formula:") and k not in in_any | renamed
    )
    series = _series(campaign, "peak_series", calm)
    ratio = series / series.shift(1440)
    for episode in truth.episodes:
        inside = ratio[episode.start : episode.end - truth.cadence]
        assert inside.median() == pytest.approx(1.0, abs=0.1)


def test_reagent_drift_slope(halide_campaign):
    truth = halide_campaign.truth
    drift = truth.drift
    series = _series(halide_campaign, "ion_series", drift.series_key).dropna()
    days = (series.index - truth.start) / pd.Timedelta(days=1)
    slope, intercept = np.polyfit(days, series.to_numpy(), 1)
    assert slope == pytest.approx(drift.slope_per_day, rel=0.02)
    assert intercept == pytest.approx(drift.intercept, rel=0.005)
    species = halide_campaign.tables["species"]
    reagent = species[species["mechanism"] == "reagent"]
    assert list(reagent["series_key"]) == [drift.series_key]
    assert list(reagent["ion_formula"]) == ["I-"]


def test_species_sensitivity_follows_the_reagent(halide_campaign):
    """A flat species falls with the reagent; normalised by it, it is flat."""
    truth = halide_campaign.truth
    reagent = _series(halide_campaign, "ion_series", truth.drift.series_key)
    flat = next(
        k
        for k, c in truth.cycles.items()
        if k.startswith("formula:")
        and c.kind == "flat"
        and all(k not in e.series_keys for e in truth.episodes)
        and all(k not in pair for pair in truth.run_change.readings)
    )
    series = _series(halide_campaign, "peak_series", flat)
    first = slice(truth.start, truth.start + pd.Timedelta(days=3))
    last = slice(truth.end - pd.Timedelta(days=3), truth.end)
    raw = series[last].median() / series[first].median()
    expected = reagent[last].median() / reagent[first].median()
    assert expected < 0.7
    assert raw == pytest.approx(expected, rel=0.03)
    normalised = series / reagent
    assert normalised[last].median() / normalised[first].median() == pytest.approx(
        1.0, abs=0.03
    )


def test_positive_chemistry_has_no_reagent(positive_campaign):
    truth = positive_campaign.truth
    assert truth.drift is None
    species = positive_campaign.tables["species"]
    assert not (species["mechanism"] == "reagent").any()
    assert set(species["mechanism"]) == {"[M+H]+"}
    assert species["ion_formula"].str.endswith("+").all()
    # Without a reagent, a flat species holds its level all month.
    flat = next(
        k
        for k, c in truth.cycles.items()
        if k.startswith("formula:")
        and c.kind == "flat"
        and all(k not in e.series_keys for e in truth.episodes)
        and all(k not in pair for pair in truth.run_change.readings)
    )
    series = _series(positive_campaign, "peak_series", flat)
    first = series[truth.start : truth.start + pd.Timedelta(days=3)].median()
    last = series[truth.end - pd.Timedelta(days=3) : truth.end].median()
    assert last / first == pytest.approx(1.0, abs=0.03)


def test_two_chemistries_read_the_same_neutrals_as_different_ions(
    halide_campaign, positive_campaign
):
    halide = halide_campaign.tables["species"].set_index("name")
    positive = positive_campaign.tables["species"].set_index("name")
    assert halide_campaign.params.polarity == "negative"
    assert positive_campaign.params.polarity == "positive"
    assert set(halide_campaign.tables["samples"]["polarity"]) == {"negative"}
    assert set(positive_campaign.tables["samples"]["polarity"]) == {"positive"}
    shared = halide.index.intersection(positive.index)
    shared = [n for n in shared if n.startswith("C")]
    assert shared
    # [M+I]- minus [M+H]+ is I - H plus two electrons.
    delta = halide.loc[shared, "mz"] - positive.loc[shared, "mz"]
    assert np.allclose(delta, 126.904473 - 1.00782503207 + 2 * 0.000548579909)


def test_tic_step(campaign):
    truth = campaign.truth
    step = truth.tic_step
    tic = campaign.tables["samples"].set_index("t_mid_utc")["tic"]
    hourly = tic.resample("1h").median().dropna()
    jumps = np.log(hourly).diff().abs()
    assert jumps.idxmax() == step.at
    before = tic[: step.at - truth.cadence].median()
    after = tic[step.at :].median()
    assert before == pytest.approx(step.before, rel=0.01)
    assert after == pytest.approx(step.after, rel=0.01)


def test_run_change_rereads_three_formulas(campaign):
    truth = campaign.truth
    change = truth.run_change
    rows = _join(campaign, "peak_series")
    early = rows["t_mid_utc"] < change.at
    runs_before = set(rows.loc[early, "run_id"].astype("str"))
    runs_after = set(rows.loc[~early, "run_id"].astype("str"))
    assert runs_before == {change.before}
    assert runs_after == {change.after}
    # Each batch carries one run.
    per_batch = rows.groupby("sample_batch_id", observed=True)["run_id"].nunique()
    assert (per_batch == 1).all()
    keys_before = set(rows.loc[early, "series_key"].astype("str"))
    keys_after = set(rows.loc[~early, "series_key"].astype("str"))
    assert len(change.readings) == 3
    assert keys_before - keys_after == {old for old, _ in change.readings}
    assert keys_after - keys_before == {new for _, new in change.readings}
    # The batch peak is the same peak under either reading: its level runs on.
    day = pd.Timedelta(days=1)
    for old, new in change.readings:
        last = _series(campaign, "peak_series", old)[change.at - day : change.at]
        first = _series(campaign, "peak_series", new)[change.at : change.at + day]
        assert first.median() == pytest.approx(last.median(), rel=0.15)
        assert truth.cycles[old] == truth.cycles[new]
    # The ion keys are stable by construction: they span the change.
    ions = _join(campaign, "ion_series")
    ion_early = ions["t_mid_utc"] < change.at
    assert set(ions.loc[ion_early, "series_key"]) == set(
        ions.loc[~ion_early, "series_key"]
    )
    episodic = {k for e in truth.episodes for k in e.series_keys}
    assert not episodic & {k for pair in change.readings for k in pair}
    species = campaign.tables["species"].set_index("series_key")
    for old, new in change.readings:
        assert species.loc[old, "n_c"] - 1 == species.loc[new, "n_c"]
        assert species.loc[old, "n_h"] - 4 == species.loc[new, "n_h"]
        assert species.loc[old, "n_o"] + 1 == species.loc[new, "n_o"]


def test_targeted_ions_read_their_batch_peaks(campaign):
    """An ion key and the formula key of the same compound are one peak."""
    truth = campaign.truth
    species = campaign.tables["species"]
    ions = species[species["series_key"].str.startswith("ion:")]
    ions = ions[ions["mechanism"] != "reagent"]
    assert len(ions) == campaign.params.n_targets
    mechanism = campaign.params.mechanism
    for row in ions.itertuples():
        key = f"formula:{row.formula}|{mechanism}"
        ion = _series(campaign, "ion_series", row.series_key)
        peak = _series(campaign, "peak_series", key)
        if any(old == key for old, _ in truth.run_change.readings):
            ion = ion[: truth.run_change.at]
            peak = peak[: truth.run_change.at]
        assert ion.notna().sum() > 0
        pd.testing.assert_series_equal(ion, peak, check_names=False)
        assert truth.cycles[row.series_key] == truth.cycles[key]


def test_below_detection_is_nan(campaign):
    truth = campaign.truth
    lod = truth.detection_limit
    for table in ("peak_series", "ion_series"):
        values = campaign.tables[table]["intensity"]
        assert values.isna().any(), table
        assert (values.dropna() >= lod).all(), table
    peaks = campaign.tables["peak_series"]
    assert peaks.loc[peaks["intensity"].isna(), "tier"].isna().all()
    assert peaks.loc[peaks["intensity"].notna(), "tier"].notna().all()
    ions = campaign.tables["ion_series"]
    assert (ions.loc[ions["intensity"].isna(), "match_category"] == 0).all()
    # Only a species whose trough reaches the limit goes below it.
    params = campaign.params
    lowest = 1 - params.noise_cv * np.sqrt(3) * 2
    if params.reagent:
        lowest *= 1 + params.reagent_slope * params.days
    for key in peaks.loc[peaks["intensity"].isna(), "series_key"].unique():
        cycle = truth.cycles[key]
        trough = truth.levels[key] / cycle.amplitude
        assert trough * lowest < lod, key


def test_parameter_sets_are_replaceable():
    shorter = dataclasses.replace(
        HALIDE_NEGATIVE,
        days=10,
        gap_day=4,
        run_change_day=6,
        tic_step_at=pd.Timedelta(days=2),
        episodes=(),
    )
    campaign = generate(shorter, seed=3)
    assert len(campaign.tables["samples"]) == 9 * 1440
    assert campaign.truth.episodes == ()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"reagent": "Br-"}, "iodide"),
        ({"file_minutes": 7}, "divide a day"),
        ({"gap_day": 30}, "gap_day"),
        ({"run_change_day": 0}, "run_change_day"),
        ({"noise_cv": 0.3}, "noise_cv"),
        ({"gap_day": 6}, "gap day"),
        (
            {"episodes": (EpisodeSpec(pd.Timedelta(days=29), DAY * 31, 2.0, (1,)),)},
            "outside the campaign",
        ),
        (
            {"episodes": (EpisodeSpec(pd.Timedelta(0), DAY, 2.0, (50,)),)},
            "does not exist",
        ),
        ({"n_renamed": 60}, "rename"),
    ],
)
def test_parameters_are_checked(change, message):
    with pytest.raises(ValueError, match=message):
        generate(dataclasses.replace(HALIDE_NEGATIVE, **change))
