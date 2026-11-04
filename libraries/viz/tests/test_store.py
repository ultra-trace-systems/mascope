"""
The campaign store, synced from the synthetic campaign.

:class:`FakeClient` stands in for ``MascopeClient``: it answers the listings
``resolve`` reads and the SDK wrappers ``sync`` calls (``batches.ion_series``,
``batch_peaks.series``, ``.list``, ``.runs``) from a campaign's tables, laid
out as the server would give them. Each batch's ledger also holds three
batch peaks the store must leave out of ``peak_series``: an isotopologue of
the first species, which repeats its parent's formula, one with no formula,
and one whose mechanism the server's mechanisms listing does not have.
The campaign's two assignment runs are two engine versions, and every batch
has a run ID of its own, as on a server.
"""

import dataclasses
import json
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fixtures.campaign import HALIDE_NEGATIVE, Campaign, generate

from mascope_sdk.exceptions import AuthenticationError, MascopeAPIError, NotFoundError
from mascope_viz.data import Scope, resolve
from mascope_viz.data import store as store_module
from mascope_viz.data.store import (
    BATCH_TABLES,
    SCHEMAS,
    CampaignStore,
    StoreStatus,
    compound_class,
    default_root,
    element_counts,
    formula_key,
    ion_key,
    reading_of,
    rollup,
    server_name,
    store_path,
    sync,
)
from mascope_viz.errors import NotAuthenticated


#: The engine version each of the campaign's runs reads formulas with.
VERSIONS = {"run-1": "0.5.0", "run-2": "0.6.0"}
MECHANISM_ID = "im-mech"
PROVENANCE = {
    "deployment_id": "deployment-x",
    "generated_utc": "2026-10-20T21:00:20Z",
    "produced_with": {
        "mascope_version": "v1.12.0",
        "match_score_version": 1,
        "peak_assignment_engine_version": "0.5.0",
    },
    "provenance_version": 1,
}
SERIES_CALLS = {"ion_series", "batch_peaks.series", "batch_peaks.list"}


def _stamped(frame: pd.DataFrame) -> pd.DataFrame:
    frame.attrs["provenance"] = dict(PROVENANCE)
    return frame


class FakeClient:
    """A Mascope server holding one campaign, behind the SDK's methods.

    :ivar calls: Each call, as ``(method, batch ID or None)``.
    :ivar fail: ``(method, batch ID)`` to the exception that call raises.
    :ivar modified: Each batch's modified stamp, as the listing gives it.
    :ivar hidden: Sample IDs the listing leaves out.
    :ivar runs: Each batch's current run ID.
    :ivar deleted: Batches the listing no longer has.
    :ivar batch_polarity: A batch's polarity in the listing, where it is not
        the campaign's.
    :ivar no_ledger: Answer the runs listing as a server without batch
        ledgers does, with a 404.
    :ivar newer: Runs a batch's listing has above its own two, newest
        first: a batch run started or ended since.
    :ivar imports: The batches whose current run is an import, with the
        number of rows it was sent.
    """

    url = "http://mascope.test:8090"
    workspace_id = "ws1"
    workspace_name = "Workspace"

    def __init__(self, campaign: Campaign, *, twin: bool = False):
        self.campaign = campaign
        self.calls: list[tuple[str, str | None]] = []
        self.fail: dict[tuple[str, str], Exception] = {}
        self.hidden: set[str] = set()
        self.deleted: set[str] = set()
        self.batch_polarity: dict[str, str] = {}
        self.no_ledger = False
        self.newer: dict[str, list[dict[str, Any]]] = {}
        self.imports: dict[str, int] = {}
        self.cleared = 0
        params = campaign.params
        self.dataset_rows = [
            {
                "dataset_id": "d1",
                "dataset_name": "Campaign",
                "instrument": params.instrument,
            }
        ]
        tables = campaign.tables
        self.mechanism = params.mechanism
        self.polarity = "-" if params.charge < 0 else "+"

        samples = tables["samples"].copy()
        samples["sample_item_id"] = samples["sample_item_id"].astype(str)
        samples["sample_batch_id"] = samples["sample_batch_id"].astype(str)
        samples["polarity"] = self.polarity
        samples["ionization_mode_id"] = "im-1"
        samples["sample_item_name"] = samples["sample_item_id"]
        self.batch_ids = list(dict.fromkeys(samples["sample_batch_id"]))
        self.by_batch = {b: rows for b, rows in samples.groupby("sample_batch_id")}
        batch_of = pd.Series(
            samples["sample_batch_id"].to_numpy(), index=samples["sample_item_id"]
        )
        self.modified = {
            b: pd.Timestamp("2026-07-02T00:00:00Z") for b in self.batch_ids
        }

        files = tables["files"].copy()
        files["filename"] = [f"{f}.raw" for f in files["sample_file_id"]]
        files["instrument_type"] = params.instrument_type
        files["polarity"] = self.polarity
        files["datetime"] = (
            files["datetime_utc"].dt.tz_convert(params.timezone).dt.tz_localize(None)
        )
        self.files = files.drop(columns="in_batch")

        species = tables["species"].set_index("series_key")
        self.species = species

        ions = tables["ion_series"].copy()
        keys = ions["series_key"].astype(str)
        ions = pd.DataFrame(
            {
                "sample_item_id": ions["sample_item_id"].astype(str),
                "target_ion_id": keys.str.removeprefix("ion:"),
                "target_compound_name": keys.map(species["name"]),
                "target_compound_formula": keys.map(species["formula"]),
                "ion_formula": keys.map(species["ion_formula"]),
                "ionization_mechanism": keys.map(species["mechanism"]),
                # The server's no-match record reads 0, not NaN
                "intensity": ions["intensity"].fillna(0.0),
                "match_category": ions["match_category"],
            }
        )
        self.ions = {
            b: rows.reset_index(drop=True)
            for b, rows in ions.groupby(ions["sample_item_id"].map(batch_of))
        }

        peaks = tables["peak_series"]
        peaks = peaks[peaks["intensity"].notna()]
        keys = peaks["series_key"].astype(str)
        formulas = keys.str.removeprefix("formula:").str.split("|").str[0]
        points = pd.DataFrame(
            {
                "sample_item_id": peaks["sample_item_id"].astype(str),
                "batch_peak_id": peaks["batch_peak_id"].astype(str),
                "sample_peak_id": [f"sp{i}" for i in range(len(peaks))],
                "batch_mz": keys.map(species["mz"]).astype(float),
                "consensus_formula": formulas,
                "consensus_ion_formula": keys.map(species["ion_formula"]),
                "consensus_tier": peaks["tier"].astype(str),
                "intensity": peaks["intensity"],
                "tier": peaks["tier"].astype(str),
            }
        )
        run_of = peaks["run_id"].astype(str)
        batch = points["sample_item_id"].map(batch_of)
        self.points = {}
        self.anchors = {}
        self.runs = {}
        for b, rows in points.groupby(batch):
            self.runs[b] = f"{run_of.loc[rows.index[0]]}:{b}"
            self.points[b], self.anchors[b] = self._ledger(b, rows, twin)

    def _ledger(self, batch_id: str, rows: pd.DataFrame, twin: bool) -> tuple[Any, Any]:
        """A batch's points and anchors, with an isotopologue anchor, an
        anchor without a formula and one whose mechanism the mechanisms
        listing does not have beside the campaign's own, and with *twin*
        a second anchor of the first species' formula that is no
        isotopologue."""
        anchors = (
            rows.drop_duplicates("batch_peak_id")
            .rename(columns={"batch_mz": "mz"})[
                [
                    "batch_peak_id",
                    "mz",
                    "consensus_formula",
                    "consensus_ion_formula",
                    "consensus_tier",
                ]
            ]
            .assign(ionization_mechanism_id=MECHANISM_ID, isotopologue_of=None)
        )
        parent = anchors.iloc[0]
        first = rows[rows["batch_peak_id"] == parent["batch_peak_id"]].head(50)
        extra = [
            first.assign(
                batch_peak_id=f"{batch_id}-iso",
                batch_mz=parent["mz"] + 1.00336,
                intensity=first["intensity"] * 0.05,
            ),
            first.assign(
                batch_peak_id=f"{batch_id}-unassigned",
                batch_mz=parent["mz"] + 0.5,
                consensus_formula=None,
                consensus_ion_formula=None,
                consensus_tier="unassigned",
                tier="unassigned",
            ),
            first.assign(
                batch_peak_id=f"{batch_id}-nomech", batch_mz=parent["mz"] + 0.7
            ),
        ]
        twins = []
        if twin:
            extra.append(
                first.assign(
                    batch_peak_id=f"{batch_id}-twin",
                    batch_mz=parent["mz"] + 0.02,
                    intensity=first["intensity"] * 0.5,
                )
            )
            twins.append({**parent.to_dict(), "batch_peak_id": f"{batch_id}-twin"})
        anchors = pd.concat(
            [
                anchors,
                pd.DataFrame(
                    twins
                    + [
                        {
                            **parent.to_dict(),
                            "batch_peak_id": f"{batch_id}-iso",
                            "mz": parent["mz"] + 1.00336,
                            "isotopologue_of": parent["batch_peak_id"],
                        },
                        {
                            "batch_peak_id": f"{batch_id}-unassigned",
                            "mz": parent["mz"] + 0.5,
                            "consensus_formula": None,
                            "consensus_ion_formula": None,
                            "consensus_tier": "unassigned",
                            "ionization_mechanism_id": MECHANISM_ID,
                            "isotopologue_of": None,
                        },
                        # A mechanism the server's listing does not have
                        {
                            **parent.to_dict(),
                            "batch_peak_id": f"{batch_id}-nomech",
                            "mz": parent["mz"] + 0.7,
                            "ionization_mechanism_id": "im-unlisted",
                        },
                    ]
                ),
            ],
            ignore_index=True,
        )
        return pd.concat([rows, *extra], ignore_index=True), anchors

    # --- what the store and resolve call ---------------------------------

    def clear_cache(self) -> None:
        self.cleared += 1

    def _call(self, method: str, batch_id: str | None = None) -> None:
        self.calls.append((method, batch_id))
        error = self.fail.get((method, batch_id))
        if error is not None:
            raise error

    def series_calls(self) -> list[tuple[str, str | None]]:
        return [call for call in self.calls if call[0] in SERIES_CALLS]

    @property
    def datasets(self):
        return _Resource(list=lambda: pd.DataFrame(self.dataset_rows))

    @property
    def batches(self):
        def listing(dataset_id):
            self._call("batches.list")
            return pd.DataFrame(
                [
                    {
                        "sample_batch_id": b,
                        "sample_batch_name": f"Campaign {b}",
                        "dataset_id": "d1",
                        "polarity": self.batch_polarity.get(b, self.polarity),
                        "sample_batch_utc_modified": self.modified[b].isoformat(),
                    }
                    for b in self.batch_ids
                    if b not in self.deleted
                ]
            )

        def ion_series(batch_id, *, collection=None, ions=None):
            self._call("ion_series", batch_id)
            assert collection in (None, "c1")
            return _stamped(self.ions[batch_id].copy())

        return _Resource(list=listing, ion_series=ion_series)

    @property
    def samples(self):
        def listing(batch=None, *, dataset=None, **_):
            self._call("samples.list", batch)
            rows = self.samples_of(batch)
            return rows.copy() if not rows.empty else None

        return _Resource(list=listing)

    def samples_of(self, batch_id: str) -> pd.DataFrame:
        rows = self.by_batch[batch_id]
        return rows[~rows["sample_item_id"].isin(self.hidden)]

    @property
    def sample_files(self):
        def listing(*, datetime_min=None, datetime_max=None, instrument=None, **_):
            self._call("sample_files.list")
            rows = self.files
            if datetime_min is not None:
                rows = rows[rows["datetime_utc"] >= pd.Timestamp(datetime_min)]
            if datetime_max is not None:
                rows = rows[rows["datetime_utc"] <= pd.Timestamp(datetime_max)]
            return _stamped(rows.reset_index(drop=True))

        return _Resource(list=listing)

    @property
    def collections(self):
        def listing(*, batch_id=None, **_):
            return pd.DataFrame(
                [
                    {
                        "target_collection_id": "c1",
                        "target_collection_name": "Targets",
                        "target_collection_type": "TARGETS",
                        "workspace_id": "ws1",
                    }
                ]
            )

        return _Resource(list=listing)

    @property
    def ionization(self):
        def mechanisms():
            self._call("ionization.list")
            return pd.DataFrame(
                [
                    {
                        "ionization_mechanism_id": MECHANISM_ID,
                        "ionization_mechanism": self.mechanism,
                    },
                    {
                        "ionization_mechanism_id": "im-other",
                        "ionization_mechanism": "x",
                    },
                ]
            )

        return _Resource(list=mechanisms)

    @property
    def batch_peaks(self):
        def runs(batch_id):
            self._call("batch_peaks.runs", batch_id)
            if self.no_ledger:
                raise NotFoundError("no route")
            run_id = self.runs[batch_id]
            rows = self.imports.get(batch_id)
            # Newest first: any newer runs, a rebuild that failed, then the
            # current run
            return pd.DataFrame(
                [
                    *self.newer.get(batch_id, []),
                    {
                        "batch_peak_run_id": f"failed:{batch_id}",
                        "action": "rebuild",
                        "engine": "mascope",
                        "engine_version": "0.9.0",
                        "status": "failed",
                        "current": False,
                        "config": None,
                    },
                    {
                        "batch_peak_run_id": run_id,
                        "action": "fold" if rows is None else "import",
                        "engine": "mascope",
                        "engine_version": VERSIONS[run_id.split(":")[0]],
                        "status": "completed",
                        "current": True,
                        # As the import route records it
                        "config": None
                        if rows is None
                        else {
                            "engine": "mascope",
                            "mz_tolerance_ppm": 5.0,
                            "rows": rows,
                            "client": {"ppm": 2},
                        },
                    },
                ]
            )

        def series(batch_id, *, tier=None, min_n_present=2, run_id=None):
            self._call("batch_peaks.series", batch_id)
            assert run_id == self.runs[batch_id]
            frame = self.points[batch_id].copy()
            frame.attrs["run_id"] = run_id
            return _stamped(frame)

        def listing(batch_id, *, tier=None, min_n_present=1, run_id=None):
            self._call("batch_peaks.list", batch_id)
            assert run_id == self.runs[batch_id]
            return self.anchors[batch_id].copy()

        return _Resource(runs=runs, series=series, list=listing)


class _Resource:
    def __init__(self, **methods):
        self.__dict__.update(methods)


# --- fixtures ---------------------------------------------------------------------

#: A short campaign for the tests that sync more than once: four days, the
#: second unfiled, the run changing on the third, fifteen species.
SHORT = dataclasses.replace(
    HALIDE_NEGATIVE,
    days=4,
    gap_day=1,
    run_change_day=2,
    n_species=15,
    n_targets=5,
    episodes=(),
)

SCOPE = Scope(dataset="Campaign")


@pytest.fixture(scope="session")
def short_campaign() -> Campaign:
    return generate(SHORT, seed=1)


@pytest.fixture
def clock(monkeypatch):
    """The store's clock, a month after the campaigns end unless set."""
    now = {"t": pd.Timestamp("2026-08-01T00:00:00Z")}
    monkeypatch.setattr(store_module, "_now", lambda: now["t"])
    return now


@pytest.fixture
def pauses(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(store_module.time, "sleep", slept.append)
    return slept


@pytest.fixture
def short(short_campaign, clock, pauses, tmp_path):
    """A fresh server over the short campaign, its scope synced once."""
    client = FakeClient(short_campaign)
    report = sync(client, SCOPE, root=tmp_path)
    client.calls.clear()
    return client, report


@pytest.fixture(scope="module")
def synced(campaign, tmp_path_factory):
    """A month of each chemistry, synced once; tests only read it."""
    patch = pytest.MonkeyPatch()
    patch.setattr(store_module, "_now", lambda: pd.Timestamp("2026-08-01T00:00:00Z"))
    patch.setattr(store_module.time, "sleep", lambda seconds: None)
    try:
        client = FakeClient(campaign)
        report = sync(client, SCOPE, root=tmp_path_factory.mktemp("stores"))
    finally:
        patch.undo()
    return campaign, client, report


def _ordered(frame: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    frame = frame.copy()
    for column in frame:
        if isinstance(frame[column].dtype, pd.CategoricalDtype):
            frame[column] = frame[column].astype(object)
    return frame.sort_values(by, kind="stable").reset_index(drop=True)


# --- the tables -------------------------------------------------------------------


def test_the_month_round_trips_through_the_store(synced):
    campaign, _, report = synced
    store = report.store
    truth = campaign.tables

    samples = _ordered(store.table("samples"), ["sample_item_id"])
    want = _ordered(truth["samples"], ["sample_item_id"])
    for column in (
        "sample_item_id",
        "sample_batch_id",
        "sample_file_id",
        "t_mid_utc",
        "datetime_utc",
        "t0",
        "t1",
        "tic",
        "instrument",
        "instrument_type",
        "filename",
    ):
        pd.testing.assert_series_equal(
            samples[column], want[column], check_dtype=False, check_names=False
        )

    ions = _ordered(store.table("ion_series"), ["sample_item_id", "series_key"])
    want = _ordered(truth["ion_series"], ["sample_item_id", "series_key"])
    pd.testing.assert_frame_equal(ions, want, check_dtype=False)

    peaks = _ordered(store.table("peak_series"), ["sample_item_id", "batch_peak_id"])
    want = truth["peak_series"]
    want = _ordered(
        want[want["intensity"].notna()], ["sample_item_id", "batch_peak_id"]
    )
    assert len(peaks) == len(want)
    for column in (
        "sample_item_id",
        "series_key",
        "intensity",
        "tier",
        "batch_peak_id",
    ):
        pd.testing.assert_series_equal(
            peaks[column], want[column], check_dtype=False, check_names=False
        )
    # Each batch's own run, which reads as the campaign's run it stands for
    assert (peaks["run_id"].str.split(":").str[0] == want["run_id"]).all()
    assert (
        peaks["run_id"].str.split(":").str[1]
        == peaks["batch_peak_id"].map(lambda b: f"batch-{b.split('-')[1]}")
    ).all()


def test_the_ledger_peaks_without_a_series_of_their_own_are_left_out(synced):
    _, _, report = synced
    peaks = report.store.table("peak_series")

    held = set(peaks["batch_peak_id"].astype(str))
    assert not any(b.endswith(("-iso", "-unassigned", "-nomech")) for b in held)


def test_species_describe_every_series_key(synced):
    campaign, _, report = synced
    species = report.store.table("species").set_index("series_key")
    truth = campaign.tables["species"].set_index("series_key")

    keys = set(report.store.table("ion_series")["series_key"].astype(str)) | set(
        report.store.table("peak_series")["series_key"].astype(str)
    )
    assert set(species.index) == keys
    assert species.index.is_unique
    organic = [k for k in keys if truth.loc[k, "class"] != "reagent"]
    for column in (
        "name",
        "formula",
        "ion_formula",
        "mechanism",
        "class",
        "n_c",
        "n_h",
        "n_n",
        "n_o",
        "n_s",
        "o_c",
        "h_c",
        "osc",
    ):
        pd.testing.assert_series_equal(
            species.loc[organic, column],
            truth.loc[organic, column],
            check_dtype=False,
            check_names=False,
        )
    formulas = [k for k in keys if k.startswith("formula:")]
    np.testing.assert_allclose(species.loc[formulas, "mz"], truth.loc[formulas, "mz"])
    # The target ions' m/z is not in what the ion series route gives
    assert species.loc[[k for k in keys if k.startswith("ion:")], "mz"].isna().all()


def test_the_reagent_ion_is_inorganic(halide_campaign, synced):
    campaign, _, report = synced
    if campaign is not halide_campaign:
        pytest.skip("the positive chemistry has no reagent ion")
    reagent = campaign.truth.drift.series_key

    row = report.store.table("species").set_index("series_key").loc[reagent]

    assert row["class"] == "inorganic"
    assert row["n_c"] == 0
    assert np.isnan(row["o_c"])


def test_series_keys_are_spelt_as_the_note_spells_them(synced):
    _, _, report = synced
    ions = report.store.table("ion_series")["series_key"].astype(str)
    peaks = report.store.table("peak_series")["series_key"].astype(str)

    assert ions.str.fullmatch(r"ion:ti\d{4}").all()
    assert peaks.str.fullmatch(r"formula:[^|]+\|\[M\+[IH]\][-+]").all()
    assert ion_key("ti0001") == "ion:ti0001"
    assert formula_key("C5H8O3", "[M+I]-") == "formula:C5H8O3|[M+I]-"
    assert formula_key(" C5H8 O3 ", "[M+I]-") == "formula:C5H8 O3|[M+I]-"


def test_a_no_match_record_is_nan_not_zero(synced):
    _, _, report = synced
    ions = report.store.table("ion_series")

    assert ions.loc[ions["match_category"] == 0, "intensity"].isna().all()
    assert ions.loc[ions["match_category"] > 0, "intensity"].notna().all()
    assert (ions["intensity"] != 0).all()


def test_samples_are_timed_at_the_middle_of_their_window(synced):
    _, _, report = synced
    samples = report.store.table("samples")

    middle = pd.to_timedelta((samples["t0"] + samples["t1"]) / 2, unit="s")
    assert (samples["t_mid_utc"] == samples["datetime_utc"] + middle).all()
    assert str(samples["t_mid_utc"].dt.tz) == "UTC"


def test_a_sample_without_a_window_is_timed_at_its_file_start():
    listed = pd.DataFrame(
        [
            {
                "sample_item_id": "s1",
                "datetime_utc": pd.Timestamp("2026-06-01T00:00:00Z"),
                "t0": None,
                "t1": None,
            }
        ]
    )

    table = store_module._sample_table(listed)

    assert table["t_mid_utc"].iloc[0] == pd.Timestamp("2026-06-01T00:00:00Z")


def test_files_say_which_a_batch_holds(synced):
    campaign, _, report = synced
    files = report.store.table("files")
    gap = campaign.truth.gap

    assert len(files) == len(campaign.tables["files"])
    unfiled = files[~files["in_batch"]]
    assert len(unfiled) == gap.files
    assert unfiled["datetime_utc"].between(gap.start, gap.end, inclusive="left").all()


def test_each_batch_is_a_file_per_table(synced):
    campaign, client, report = synced
    path = report.store.path

    for table in BATCH_TABLES:
        names = sorted(p.stem for p in (path / table).glob("*.parquet"))
        assert names == sorted(client.batch_ids)
    assert (path / "files.parquet").exists()
    days = sorted(p.stem for p in (path / "rollups").glob("*.parquet"))
    sampled = pd.to_datetime(campaign.tables["samples"]["t_mid_utc"]).dt.floor("1D")
    assert days == sorted({f"{day:%Y-%m-%d}" for day in sampled})


def test_store_json_records_the_scope_server_provenance_and_stamps(synced):
    _, client, report = synced
    meta = json.loads((report.store.path / "store.json").read_text())

    assert meta["key"] == report.resolved.key
    assert meta["scope"] == report.resolved.scope.content()
    assert meta["server"]["url"] == client.url
    assert meta["server"]["workspace_id"] == client.workspace_id
    assert meta["provenance"] == PROVENANCE
    assert set(meta["batches"]) == set(client.batch_ids)
    stamp = meta["batches"][client.batch_ids[0]]
    assert stamp["n_samples"] == len(client.samples_of(client.batch_ids[0]))
    assert (
        pd.Timestamp(stamp["sample_batch_utc_modified"])
        == client.modified[client.batch_ids[0]]
    )
    assert stamp["run"]["run_id"] == client.runs[client.batch_ids[0]]
    assert stamp["newest_run"] == {
        "run_id": f"failed:{client.batch_ids[0]}",
        "action": "rebuild",
        "status": "failed",
    }
    assert stamp["reading"] == "mascope 0.5.0"
    assert stamp["provenance"] == PROVENANCE
    assert stamp["synced_utc"] is not None
    assert meta["pending_rollups"] == []


def test_every_parquet_file_names_its_batch_run_and_provenance(synced):
    _, client, report = synced
    import pyarrow.parquet as pq

    batch_id = client.batch_ids[-1]
    path = report.store.path / "peak_series" / f"{batch_id}.parquet"
    about = json.loads(pq.read_schema(path).metadata[store_module.METADATA_KEY])

    assert about == {
        "sample_batch_id": batch_id,
        "run_id": client.runs[batch_id],
        "provenance": PROVENANCE,
    }


def test_table_reads_some_batches_or_keys(synced):
    _, client, report = synced
    store = report.store
    first = client.batch_ids[0]
    key = str(store.table("ion_series")["series_key"].iloc[0])

    some = store.table("ion_series", batches=[first])
    keyed = store.table("ion_series", keys=[key])

    assert set(some["sample_item_id"]) <= set(
        client.samples_of(first)["sample_item_id"]
    )
    assert set(keyed["series_key"]) == {key}
    assert store.table("peak_series", keys=["formula:none|x"]).empty
    with pytest.raises(ValueError, match="no series keys"):
        store.table("samples", keys=[key])
    for table in ("files", "rollups"):
        with pytest.raises(ValueError, match="not kept per batch"):
            store.table(table, batches=[first])
    with pytest.raises(ValueError, match="No table"):
        store.table("spectra")


def test_a_key_read_in_several_batches_takes_its_newest_batchs_species_row(
    short, tmp_path
):
    client, first = short
    oldest, newest = client.batch_ids[0], client.batch_ids[-1]
    ions = client.ions[newest]
    target = ions["target_ion_id"].iloc[0]
    renamed = ions["target_ion_id"] == target
    client.ions[newest] = ions.assign(
        target_compound_name=ions["target_compound_name"].where(~renamed, "Renamed")
    )
    client.modified[newest] += pd.Timedelta(hours=1)

    report = sync(client, SCOPE, root=tmp_path)
    species = report.store.table("species").set_index("series_key")

    assert report.fetched == {newest: "modified"}
    assert species.index.is_unique
    assert species.loc[ion_key(target), "name"] == "Renamed"
    older = report.store.table("species", batches=[oldest]).set_index("series_key")
    assert older.loc[ion_key(target), "name"] != "Renamed"


# --- the rollups ------------------------------------------------------------------


@pytest.fixture(scope="module")
def raw_values(synced) -> pd.DataFrame:
    """Every series value the synced month holds, with its sample's time and
    its batch's reading (``none`` for the ion keys), read with pandas alone."""
    _, _, report = synced
    store = report.store
    samples = store.table("samples")
    meta = store.meta["batches"]
    reading = {
        str(s): meta[str(b)]["reading"]
        for s, b in zip(samples["sample_item_id"], samples["sample_batch_id"])
    }
    when = pd.Series(
        samples["t_mid_utc"].to_numpy(), index=samples["sample_item_id"].astype(str)
    )
    raw = []
    for table in ("ion_series", "peak_series"):
        rows = store.table(table)
        ids = rows["sample_item_id"].astype(str)
        raw.append(
            pd.DataFrame(
                {
                    "t": ids.map(when).to_numpy(),
                    "series_key": rows["series_key"].astype(str).to_numpy(),
                    "reading": ids.map(reading).to_numpy()
                    if table == "peak_series"
                    else "none",
                    "intensity": rows["intensity"].to_numpy(),
                }
            )
        )
    return pd.concat(raw, ignore_index=True)


@pytest.mark.parametrize("offset, name", [("1h", "hour"), ("1D", "day")])
def test_the_rollups_equal_pandas_resampling_of_the_raw_rows(
    synced, raw_values, offset, name
):
    _, _, report = synced
    store = report.store
    raw = raw_values

    resampled = raw.set_index("t").groupby(["series_key", "reading"])["intensity"]
    expected = pd.DataFrame(
        {
            "median": resampled.resample(offset).median(),
            "mean": resampled.resample(offset).mean(),
            "p10": resampled.resample(offset).quantile(0.1),
            "p90": resampled.resample(offset).quantile(0.9),
            "n": resampled.resample(offset).count(),
        }
    )
    expected = expected[expected["n"] > 0].reset_index()

    rollups = store.table("rollups")
    rollups = rollups[rollups["bin"] == name].copy()
    rollups["reading"] = rollups["reading"].astype(object).fillna("none")
    rollups["series_key"] = rollups["series_key"].astype(str)
    got = rollups.set_index(["series_key", "reading", "t"]).sort_index()
    want = expected.set_index(["series_key", "reading", "t"]).sort_index()
    assert len(got) == len(want)
    pd.testing.assert_frame_equal(
        got[["median", "mean", "p10", "p90", "n"]],
        want[["median", "mean", "p10", "p90", "n"]],
        check_dtype=False,
        check_index_type=False,
        check_freq=False,
    )


def test_the_rollups_never_join_two_readings_in_one_bin():
    t = pd.Timestamp("2026-06-01T10:00:00Z")
    values = pd.DataFrame(
        {
            "t_mid_utc": [
                t,
                t + pd.Timedelta(minutes=10),
                t + pd.Timedelta(minutes=20),
            ],
            "series_key": ["formula:C2H4O2|[M+I]-"] * 3,
            "reading": ["mascope 0.5.0", "mascope 0.5.0", "mascope 0.6.0"],
            "intensity": [1.0, 3.0, 100.0],
        }
    )

    hours = rollup(values).query("bin == 'hour'").set_index("reading")

    assert hours.loc["mascope 0.5.0", "median"] == 2.0
    assert hours.loc["mascope 0.5.0", "n"] == 2
    assert hours.loc["mascope 0.6.0", "median"] == 100.0


def test_a_bin_without_a_value_has_no_rollup():
    t = pd.Timestamp("2026-06-01T10:00:00Z")
    values = pd.DataFrame(
        {
            "t_mid_utc": [t, t + pd.Timedelta(hours=2)],
            "series_key": ["ion:ti0001"] * 2,
            "reading": [None, None],
            "intensity": [5.0, np.nan],
        }
    )

    hours = rollup(values).query("bin == 'hour'")

    assert list(hours["t"]) == [t]
    assert hours["reading"].isna().all()
    assert list(hours.columns) == SCHEMAS["rollups"].names


# --- incremental sync -------------------------------------------------------------


def test_a_second_sync_of_an_unchanged_scope_makes_no_series_calls(short, tmp_path):
    client, first = short

    second = sync(client, SCOPE, root=tmp_path)

    assert set(first.fetched) == set(client.batch_ids)
    assert set(first.fetched.values()) == {"new"}
    assert second.fetched == {}
    assert set(second.unchanged) == set(client.batch_ids)
    assert client.series_calls() == []
    assert client.cleared == 2


def test_a_batch_whose_sample_count_changed_is_refetched_alone(short, tmp_path):
    client, first = short
    batch_id = client.batch_ids[1]
    gone = client.samples_of(batch_id)["sample_item_id"].iloc[-1]
    client.hidden.add(gone)

    report = sync(client, SCOPE, root=tmp_path)

    assert report.fetched == {batch_id: "sample count"}
    assert {b for _, b in client.series_calls()} == {batch_id}
    samples = report.store.table("samples", batches=[batch_id])
    assert gone not in set(samples["sample_item_id"])
    for table in ("ion_series", "peak_series"):
        held = report.store.table(table, batches=[batch_id])
        assert gone not in set(held["sample_item_id"].astype(str))


def test_a_batch_modified_since_is_refetched_alone(short, tmp_path):
    client, _ = short
    batch_id = client.batch_ids[0]
    client.modified[batch_id] += pd.Timedelta(hours=1)

    report = sync(client, SCOPE, root=tmp_path)

    assert report.fetched == {batch_id: "modified"}
    assert {b for _, b in client.series_calls()} == {batch_id}


def test_a_batch_whose_ledger_run_changed_is_refetched_alone(short, tmp_path):
    client, _ = short
    batch_id = client.batch_ids[2]
    client.runs[batch_id] = f"run-1:{batch_id}-rebuilt"

    report = sync(client, SCOPE, root=tmp_path)

    assert report.fetched == {batch_id: "ledger run"}
    peaks = report.store.table("peak_series", batches=[batch_id])
    assert set(peaks["run_id"]) == {f"run-1:{batch_id}-rebuilt"}
    assert report.store.meta["batches"][batch_id]["reading"] == "mascope 0.5.0"


def _batch_run(batch_id: str, name: str, status: str) -> dict[str, Any]:
    """A runs listing's row of a search run on *batch_id*, not current."""
    return {
        "batch_peak_run_id": f"{name}:{batch_id}",
        "action": "search_untargeted",
        "engine": "mascope",
        "engine_version": "0.9.0",
        "status": status,
        "current": False,
        "config": {"ppm": 2},
    }


def test_a_batch_run_that_failed_refetches_its_batch_alone(short, tmp_path):
    # The failed search left the live ledger as it had rewritten it, under
    # the run that is still current
    client, _ = short
    batch_id = client.batch_ids[1]
    client.newer[batch_id] = [_batch_run(batch_id, "search", "failed")]

    report = sync(client, SCOPE, root=tmp_path)
    again = sync(client, SCOPE, root=tmp_path)

    assert report.fetched == {batch_id: "batch run"}
    assert {b for _, b in client.series_calls()} == {batch_id}
    assert report.store.meta["batches"][batch_id]["newest_run"] == {
        "run_id": f"search:{batch_id}",
        "action": "search_untargeted",
        "status": "failed",
    }
    assert again.fetched == {}


@pytest.mark.parametrize("ending", ["failed", "completed"])
def test_a_batch_read_while_a_run_rewrites_its_ledger_is_read_again_after(
    short, tmp_path, ending
):
    client, _ = short
    batch_id = client.batch_ids[2]
    client.newer[batch_id] = [_batch_run(batch_id, "search", "running")]

    during = sync(client, SCOPE, root=tmp_path)
    flagged = during.store.status().batches.set_index("sample_batch_id")
    still = sync(client, SCOPE, root=tmp_path)
    if ending == "failed":
        client.newer[batch_id] = [_batch_run(batch_id, "search", "failed")]
    else:
        # It became the current run
        client.newer[batch_id] = []
        client.runs[batch_id] = f"run-1:{batch_id}-searched"
    after = sync(client, SCOPE, root=tmp_path)

    assert during.fetched == {batch_id: "batch run"}
    assert flagged["run_in_flight"].to_dict() == {
        b: b == batch_id for b in client.batch_ids
    }
    assert still.fetched == {}
    assert after.fetched == {
        batch_id: "batch run" if ending == "failed" else "ledger run"
    }
    assert not after.store.status().batches["run_in_flight"].any()


def test_the_batch_holding_today_is_always_refetched(short, clock, tmp_path):
    client, _ = short
    last = client.batch_ids[-1]
    clock["t"] = client.samples_of(last)["datetime_utc"].max() + pd.Timedelta(hours=1)

    report = sync(client, SCOPE, root=tmp_path)
    again = sync(client, SCOPE, root=tmp_path)

    assert report.fetched == {last: "today"}
    assert again.fetched == {last: "today"}


def test_batches_are_fetched_oldest_first_with_a_pause_between(short, pauses, tmp_path):
    client, first = short
    pauses.clear()
    client.calls.clear()
    for batch_id in client.batch_ids:
        client.modified[batch_id] += pd.Timedelta(hours=1)

    sync(client, SCOPE, pause=0.25, root=tmp_path)

    order = [b for method, b in client.calls if method == "ion_series"]
    assert order == client.batch_ids
    assert pauses == [0.25] * (len(client.batch_ids) - 1)


def test_a_batch_the_scope_no_longer_holds_is_removed(short, tmp_path):
    client, _ = short
    gone = client.batch_ids[0]
    day = f"{client.samples_of(gone)['t_mid_utc'].min():%Y-%m-%d}"
    client.deleted.add(gone)

    report = sync(client, SCOPE, root=tmp_path)

    assert report.removed == (gone,)
    assert report.fetched == {}
    for table in BATCH_TABLES:
        assert not (report.store.path / table / f"{gone}.parquet").exists()
    assert gone not in report.store.meta["batches"]
    assert not (report.store.path / "rollups" / f"{day}.parquet").exists()
    assert gone not in set(report.store.table("samples")["sample_batch_id"])


def test_rollups_left_pending_are_computed_by_the_next_sync(short, tmp_path):
    client, first = short
    store = first.store
    day = sorted(p.stem for p in (store.path / "rollups").glob("*.parquet"))[0]
    before = store.table("rollups")
    (store.path / "rollups" / f"{day}.parquet").unlink()
    meta = store.meta
    meta["pending_rollups"] = [day]
    (store.path / "store.json").write_text(json.dumps(meta))

    assert store.status().pending_rollups == (day,)
    assert not store.status().complete
    report = sync(client, SCOPE, root=tmp_path)

    assert report.fetched == {}
    pd.testing.assert_frame_equal(report.store.table("rollups"), before)
    assert report.store.status().complete


# --- failures ---------------------------------------------------------------------


def test_a_failed_batch_is_recorded_and_skipped(
    short_campaign, clock, pauses, tmp_path
):
    client = FakeClient(short_campaign)
    broken = client.batch_ids[1]
    client.fail[("ion_series", broken)] = MascopeAPIError("Internal Server Error")

    report = sync(client, SCOPE, root=tmp_path)
    status = report.store.status()

    assert set(report.failed) == {broken}
    assert "Internal Server Error" in report.failed[broken]
    assert set(report.fetched) == set(client.batch_ids) - {broken}
    assert set(status.failed) == {broken}
    assert not status.complete
    row = status.batches.set_index("sample_batch_id").loc[broken]
    assert row["state"] == "failed"
    assert row["n_samples"] == len(client.samples_of(broken))
    assert row["first_datetime_utc"] == client.samples_of(broken)["datetime_utc"].min()
    assert not (report.store.path / "samples" / f"{broken}.parquet").exists()
    assert broken not in set(report.store.table("samples")["sample_batch_id"])

    del client.fail[("ion_series", broken)]
    client.calls.clear()
    again = sync(client, SCOPE, root=tmp_path)

    assert again.fetched == {broken: "failed before"}
    assert {b for _, b in client.series_calls()} == {broken}
    assert again.store.status().complete


def test_a_failed_refetch_keeps_the_earlier_tables_and_says_stale(short, tmp_path):
    client, first = short
    batch_id = client.batch_ids[0]
    held = first.store.table("ion_series", batches=[batch_id])
    client.modified[batch_id] += pd.Timedelta(hours=1)
    client.fail[("batch_peaks.series", batch_id)] = MascopeAPIError("timed out")

    report = sync(client, SCOPE, root=tmp_path)
    status = report.store.status()

    assert set(report.failed) == {batch_id}
    row = status.batches.set_index("sample_batch_id").loc[batch_id]
    assert row["state"] == "stale"
    assert "timed out" in row["error"]
    pd.testing.assert_frame_equal(
        report.store.table("ion_series", batches=[batch_id]), held
    )


def test_a_failed_runs_listing_leaves_the_batch_as_it_was(short, tmp_path):
    client, first = short
    batch_id = client.batch_ids[2]
    held = first.store.table("peak_series", batches=[batch_id])
    client.fail[("batch_peaks.runs", batch_id)] = MascopeAPIError("Bad Gateway")

    report = sync(client, SCOPE, root=tmp_path)
    status = report.store.status()

    assert set(report.failed) == {batch_id}
    assert "Bad Gateway" in report.failed[batch_id]
    assert report.fetched == {}
    row = status.batches.set_index("sample_batch_id").loc[batch_id]
    assert row["state"] == "unchecked"
    assert "Bad Gateway" in row["error"]
    assert set(status.failed) == {batch_id}
    assert not status.complete
    pd.testing.assert_frame_equal(
        report.store.table("peak_series", batches=[batch_id]), held
    )

    del client.fail[("batch_peaks.runs", batch_id)]
    client.calls.clear()
    again = sync(client, SCOPE, root=tmp_path)

    assert again.fetched == {}
    assert client.series_calls() == []
    assert again.store.status().complete


def test_a_failed_runs_listing_of_a_batch_never_fetched_fails_it(
    short_campaign, clock, pauses, tmp_path
):
    client = FakeClient(short_campaign)
    batch_id = client.batch_ids[0]
    client.fail[("batch_peaks.runs", batch_id)] = MascopeAPIError("Bad Gateway")

    report = sync(client, SCOPE, root=tmp_path)
    row = report.store.status().batches.set_index("sample_batch_id").loc[batch_id]
    del client.fail[("batch_peaks.runs", batch_id)]
    again = sync(client, SCOPE, root=tmp_path)

    assert row["state"] == "failed"
    assert again.fetched == {batch_id: "failed before"}


def test_a_server_without_batch_ledgers_syncs_the_ion_series_alone(
    short_campaign, clock, pauses, tmp_path
):
    client = FakeClient(short_campaign)
    client.no_ledger = True

    report = sync(client, SCOPE, root=tmp_path)
    status = report.store.status()

    assert report.failed == {}
    assert set(report.fetched) == set(client.batch_ids)
    assert report.store.table("peak_series").empty
    assert not report.store.table("ion_series").empty
    assert {method for method, _ in client.series_calls()} == {"ion_series"}
    assert status.batches["reading"].isna().all()
    assert not status.mixed_runs


def test_a_store_of_another_format_is_rebuilt(short, tmp_path):
    client, first = short
    meta = first.store.meta
    meta["format"] = 0
    first.store.meta_path.write_text(json.dumps(meta))
    stray = first.store.path / "samples" / "gone.parquet"
    stray.write_bytes(b"")

    report = sync(client, SCOPE, root=tmp_path)

    assert set(report.fetched.values()) == {"new"}
    assert not stray.exists()
    assert report.store.meta["format"] == store_module.FORMAT


def test_a_day_a_refetched_batch_no_longer_covers_loses_its_rollups(
    short_campaign, clock, pauses, tmp_path
):
    # The last two days filed into one batch
    tables = dict(short_campaign.tables)
    samples = tables["samples"].copy()
    last, joined = sorted(set(samples["sample_batch_id"].astype(str)))[-2:]
    samples["sample_batch_id"] = (
        samples["sample_batch_id"].astype(str).replace({joined: last})
    )
    tables["samples"] = samples
    client = FakeClient(dataclasses.replace(short_campaign, tables=tables))
    first = sync(client, SCOPE, root=tmp_path)
    day_of = samples["t_mid_utc"].dt.strftime("%Y-%m-%d")
    later = sorted(set(day_of[samples["sample_batch_id"] == last]))[-1]
    assert (first.store.path / "rollups" / f"{later}.parquet").exists()

    # The batch loses its second day's samples
    client.hidden |= set(samples.loc[day_of == later, "sample_item_id"].astype(str))
    again = sync(client, SCOPE, root=tmp_path)

    assert again.fetched == {last: "sample count"}
    assert not (again.store.path / "rollups" / f"{later}.parquet").exists()
    assert later not in set(again.store.table("rollups")["t"].dt.strftime("%Y-%m-%d"))


def test_two_batch_peaks_of_one_formula_are_counted(
    short_campaign, clock, pauses, tmp_path
):
    client = FakeClient(short_campaign, twin=True)

    report = sync(client, SCOPE, root=tmp_path)
    status = report.store.status()

    assert (status.batches["n_shared_keys"] == 1).all()
    peaks = report.store.table("peak_series")
    key_of = peaks.drop_duplicates("batch_peak_id").set_index(
        peaks.drop_duplicates("batch_peak_id")["batch_peak_id"].astype(str)
    )["series_key"]
    for batch_id in client.batch_ids:
        parent = client.anchors[batch_id]["batch_peak_id"].iloc[0]
        assert key_of[f"{batch_id}-twin"] == key_of[parent]


def test_a_refused_token_stops_the_sync(short, tmp_path):
    client, _ = short
    batch_id = client.batch_ids[1]
    client.modified[batch_id] += pd.Timedelta(hours=1)
    client.fail[("ion_series", batch_id)] = AuthenticationError("no")

    with pytest.raises(NotAuthenticated):
        sync(client, SCOPE, root=tmp_path)


def test_an_unexpected_error_fails_the_batch_alone(short, tmp_path):
    client, _ = short
    batch_id = client.batch_ids[0]
    client.modified[batch_id] += pd.Timedelta(hours=1)
    client.fail[("batch_peaks.list", batch_id)] = KeyError("consensus_formula")

    report = sync(client, SCOPE, root=tmp_path)

    assert report.failed == {batch_id: "KeyError: 'consensus_formula'"}


def test_an_ambiguous_scope_syncs_nothing(short_campaign, clock, tmp_path):
    client = FakeClient(short_campaign)
    client.dataset_rows = [
        {"dataset_id": "d1", "dataset_name": "Campaign A", "instrument": None},
        {"dataset_id": "d2", "dataset_name": "Campaign B", "instrument": None},
    ]

    report = sync(client, Scope(dataset="Campaign"), root=tmp_path)

    assert report.store is None
    assert report.resolved.ambiguous
    assert client.series_calls() == []
    assert not any(tmp_path.iterdir())
    assert report.to_dict()["candidates"]["dataset"][1]["name"] == "Campaign B"


# --- status -----------------------------------------------------------------------


def test_status_reports_the_fixtures_run_change(synced):
    campaign, client, report = synced
    status = report.store.status()
    change = campaign.truth.run_change

    assert status.mixed_runs
    assert len(status.run_changes) == 1
    found = status.run_changes[0]
    assert found.at == change.at
    assert found.polarity == client.polarity
    assert found.before == f"mascope {VERSIONS[change.before]}"
    assert found.after == f"mascope {VERSIONS[change.after]}"
    runs = status.runs.set_index("reading")
    before = runs.loc[found.before]
    assert before["n_batches"] == campaign.params.run_change_day - (
        campaign.params.gap_day < campaign.params.run_change_day
    )
    assert before["last_datetime_utc"] < change.at
    assert runs.loc[found.after, "first_datetime_utc"] == change.at
    assert status.to_dict()["run_changes"][0]["at"] == change.at.isoformat()


def test_run_changes_are_followed_per_polarity(short_campaign, clock, pauses, tmp_path):
    client = FakeClient(short_campaign)
    first, middle, last = client.batch_ids
    # Two negative batches read by one engine, a positive one between them
    # by another: the negative series never change reading
    client.runs[first] = f"run-1:{first}"
    client.runs[middle] = f"run-2:{middle}"
    client.runs[last] = f"run-1:{last}"
    client.batch_polarity[middle] = "+"

    status = sync(client, SCOPE, root=tmp_path).store.status()

    assert status.mixed_runs
    assert status.run_changes == ()
    assert list(status.batches["polarity"]) == ["-", "+", "-"]

    client.batch_polarity.clear()
    for batch_id in client.batch_ids:
        client.modified[batch_id] += pd.Timedelta(hours=1)
    status = sync(client, SCOPE, root=tmp_path).store.status()

    assert [(c.sample_batch_id, c.polarity) for c in status.run_changes] == [
        (middle, "-"),
        (last, "-"),
    ]


def test_status_says_what_is_cached_and_how_stale(synced):
    campaign, client, report = synced
    status = report.store.status()
    truth = campaign.tables

    assert status.exists and status.complete
    assert status.key == report.resolved.key
    assert status.rows["samples"] == len(truth["samples"])
    assert status.rows["ion_series"] == len(truth["ion_series"])
    assert status.rows["peak_series"] == truth["peak_series"]["intensity"].notna().sum()
    assert status.rows["files"] == len(truth["files"])
    assert status.n_unfiled == campaign.truth.gap.files
    assert status.rows["rollups"] == len(report.store.table("rollups"))
    assert list(status.batches["sample_batch_id"]) == client.batch_ids
    assert (status.batches["state"] == "synced").all()
    assert status.synced_utc == pd.Timestamp("2026-08-01T00:00:00Z")
    assert status.size_bytes > 0
    assert status.provenance == PROVENANCE
    json.dumps(status.to_dict())


def test_a_first_sync_stopped_is_not_complete(short_campaign, clock, pauses, tmp_path):
    client = FakeClient(short_campaign)
    client.fail[("batch_peaks.runs", client.batch_ids[0])] = AuthenticationError("no")

    with pytest.raises(NotAuthenticated):
        sync(client, SCOPE, root=tmp_path)
    status = CampaignStore(
        store_path(client, resolve(client, SCOPE), tmp_path)
    ).status()

    assert status.exists
    assert status.synced_utc is None
    assert not status.complete
    assert status.to_dict()["complete"] is False


def test_a_store_that_does_not_say_when_its_sync_started_is_not_complete(tmp_path):
    synced = pd.Timestamp("2026-08-01T00:00:00Z")

    assert not StoreStatus(path=tmp_path, exists=True, synced_utc=synced).complete


def test_a_later_sync_stopped_is_not_complete(short, clock, tmp_path):
    client, first = short
    clock["t"] += pd.Timedelta(hours=1)
    batch_id = client.batch_ids[1]
    client.modified[batch_id] += pd.Timedelta(hours=1)
    client.fail[("ion_series", batch_id)] = AuthenticationError("no")

    with pytest.raises(NotAuthenticated):
        sync(client, SCOPE, root=tmp_path)
    status = first.store.status()

    assert status.synced_utc == pd.Timestamp("2026-08-01T00:00:00Z")
    assert status.sync_started_utc == clock["t"]
    assert status.failed == {}
    assert not status.complete

    del client.fail[("ion_series", batch_id)]
    assert sync(client, SCOPE, root=tmp_path).store.status().complete


def test_a_sync_stopped_before_its_first_batch_is_not_complete(
    short, clock, monkeypatch, tmp_path
):
    client, first = short
    clock["t"] += pd.Timedelta(hours=1)
    write = store_module._write

    def interrupted(path, *args, **kwargs):
        if path.name == "files.parquet":
            raise KeyboardInterrupt
        write(path, *args, **kwargs)

    monkeypatch.setattr(store_module, "_write", interrupted)

    with pytest.raises(KeyboardInterrupt):
        sync(client, SCOPE, root=tmp_path)

    assert not first.store.status().complete


def test_status_ages_with_the_clock(short, clock):
    _, first = short
    clock["t"] += pd.Timedelta(days=3)

    status = first.store.status()

    assert status.age == pd.Timedelta(days=3)
    assert (status.batches["age"] == pd.Timedelta(days=3)).all()


def test_a_store_never_synced_says_so(tmp_path):
    status = CampaignStore(tmp_path / "nothing").status()

    assert not status.exists
    assert not status.complete
    assert status.to_dict()["exists"] is False


def test_a_store_is_deleted_whole(short, tmp_path):
    _, first = short

    first.store.remove()

    assert not first.store.path.exists()
    assert not first.store.status().exists


# --- where a store lives ----------------------------------------------------------


def test_a_store_is_a_directory_per_server_workspace_and_scope_key(short, tmp_path):
    client, first = short

    assert first.store.path == store_path(client, first.resolved, tmp_path)
    assert (
        first.store.path == tmp_path / "mascope.test_8090" / "ws1" / first.resolved.key
    )


def test_the_default_root_is_the_users_cache_unless_named(monkeypatch, tmp_path):
    monkeypatch.setenv("MASCOPE_VIZ_STORE", str(tmp_path / "stores"))
    assert default_root() == tmp_path / "stores"

    monkeypatch.delenv("MASCOPE_VIZ_STORE")
    monkeypatch.setattr(store_module.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert default_root() == tmp_path / "cache" / "mascope_viz" / "stores"


@pytest.mark.parametrize(
    "url, name",
    [
        ("https://example.mascope.app", "example.mascope.app"),
        ("https://Example.Mascope.app/", "example.mascope.app"),
        ("http://localhost:8090", "localhost_8090"),
        ("https://lab.example.org/mascope", "lab.example.org_mascope"),
    ],
)
def test_a_server_is_named_by_its_host_port_and_path(url, name):
    assert server_name(url) == name


# --- formulas ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "formula, counts",
    [
        ("C5H8O3", {"C": 5, "H": 8, "O": 3}),
        ("C10H15NO5S", {"C": 10, "H": 15, "N": 1, "O": 5, "S": 1}),
        ("(HNO3)2", {"H": 2, "N": 2, "O": 6}),
        ("CO(NH2)2", {"C": 1, "O": 1, "N": 2, "H": 4}),
        ("HO3^N", {"H": 1, "O": 3, "N": 1}),
        ("[13C]C5H6O", {"C": 6, "H": 6, "O": 1}),
        ("[15N]BrHO3", {"N": 1, "Br": 1, "H": 1, "O": 3}),
        ("[18O]C2H4^N", {"O": 1, "C": 2, "H": 4, "N": 1}),
        ("[13C]2C3H8", {"C": 5, "H": 8}),
        ("[N]O3", None),
        ("C2H3ClO2", {"C": 2, "H": 3, "Cl": 1, "O": 2}),
        ("()", None),
        ("C5H8O3)", None),
        ("c5", None),
        (None, None),
    ],
)
def test_element_counts(formula, counts):
    assert element_counts(formula) == counts


@pytest.mark.parametrize(
    "formula, name",
    [
        ("C5H8O3", "CHO"),
        ("C10H15NO5", "CHON"),
        ("C3H6O4S", "CHOS"),
        ("C10H17NO7S", "CHONS"),
        ("C10H16", "CH"),
        ("C2H3ClO2", "CHOX"),
        ("HNO3", "inorganic"),
        ("()", None),
    ],
)
def test_compound_class(formula, name):
    assert compound_class(element_counts(formula)) == name


def test_a_reading_is_the_engine_version_and_configuration():
    assert reading_of(None) is None
    assert reading_of({"engine": "mascope", "engine_version": "0.5.0"}) == (
        "mascope 0.5.0"
    )
    searched = reading_of(
        {"engine": "mascope", "engine_version": "0.5.0", "config": {"ppm": 2}}
    )
    assert searched.startswith("mascope 0.5.0 config ")
    assert searched != reading_of(
        {"engine": "mascope", "engine_version": "0.5.0", "config": {"ppm": 3}}
    )


def test_an_imports_row_count_is_no_part_of_its_reading():
    def imported(rows: int, ppm: float = 5.0) -> dict[str, Any]:
        config = {"engine": "peaky", "mz_tolerance_ppm": ppm, "rows": rows}
        return {"engine": "peaky", "engine_version": "0.7.0", "config": config}

    assert reading_of(imported(120)) == reading_of(imported(121))
    assert reading_of(imported(120)).startswith("peaky 0.7.0 config ")
    assert reading_of(imported(120)) != reading_of(imported(120, ppm=3.0))


def test_batches_imported_alike_do_not_mix_runs(
    short_campaign, clock, pauses, tmp_path
):
    client = FakeClient(short_campaign)
    for n, batch_id in enumerate(client.batch_ids):
        client.runs[batch_id] = f"run-1:{batch_id}"
        client.imports[batch_id] = 100 + n

    status = sync(client, SCOPE, root=tmp_path).store.status()

    assert not status.mixed_runs
    assert status.run_changes == ()
    assert status.batches["reading"].str.startswith("mascope 0.5.0 config ").all()
