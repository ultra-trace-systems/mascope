"""
A Mascope server holding one synthetic campaign, behind the SDK's methods.

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

from typing import Any

import pandas as pd

from fixtures.campaign import Campaign
from mascope_sdk.exceptions import NotFoundError
from mascope_viz.data import Scope


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

#: The scope of the fake server's one dataset.
SCOPE = Scope(dataset="Campaign")


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
