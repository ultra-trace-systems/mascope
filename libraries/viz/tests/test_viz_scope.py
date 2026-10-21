"""
Scope and resolve() over the SDK's listings, on a server in a dict.

Only ``requests`` is replaced: a real ``MascopeClient`` reads from
:class:`FakeAPI`, which answers the listing routes with the rows a demo
stack gave (``recorded_listings.json``) laid out as a small campaign on an
instrument in Helsinki time (UTC+2 in early March)::

    Dataset "Campaign 2026" (instrument X)
      2026-03-02 .. 2026-03-05 Br- acquisition   one batch a day, listed out
                                                  of order; samples at 00:30,
                                                  08:00 and 20:00 local
      2026-03-06                                  files acquired, no batch
      Blank (A)
    Dataset "Campaign 2025" (instrument X)
      Blank (A)
    Dataset "Calibration runs" (instrument Y)
      Calibration, Calibration repeat
    Dataset "Imports" (no instrument, no batch)
    Collections: "Organic acids" (the first two days), "Organic acids
                 extra" (the third), "Calibrants" (global; Calibration)
"""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from mascope_viz.data import Candidate, ResolvedScope, Scope, resolve
from mascope_viz.data.scope import BATCH_COLUMNS
from mascope_viz.errors import (
    AccessDenied,
    InvalidScope,
    NotAuthenticated,
    NotFound,
    Unavailable,
)


RECORDED = json.loads((Path(__file__).parent / "recorded_listings.json").read_text())

ZONE = "Europe/Helsinki"

DATASETS = {
    "d1": ("Campaign 2026", "instrument X"),
    "d2": ("Campaign 2025", "instrument X"),
    "d3": ("Calibration runs", "instrument Y"),
    "d4": ("Imports", None),
}
DAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05"]
#: The day acquired but filed into no batch.
GAP_DAY = "2026-03-06"
#: In the order the server lists them: by creation, not by date.
BATCHES = {
    "b3": ("d1", f"{DAYS[2]} Br- acquisition"),
    "b1": ("d1", f"{DAYS[0]} Br- acquisition"),
    "b4": ("d1", f"{DAYS[3]} Br- acquisition"),
    "b2": ("d1", f"{DAYS[1]} Br- acquisition"),
    "b5": ("d1", "Blank (A)"),
    "b6": ("d2", "Blank (A)"),
    "b7": ("d3", "Calibration"),
    "b8": ("d3", "Calibration repeat"),
}
DAILY = {"b1": DAYS[0], "b2": DAYS[1], "b3": DAYS[2], "b4": DAYS[3]}
LOCAL_TIMES = ["00:30", "08:00", "20:00"]
COLLECTIONS = {
    "c1": ("ws1", "Organic acids", ["b1", "b2"]),
    "c2": ("ws1", "Organic acids extra", ["b3"]),
    "c3": (None, "Calibrants", ["b7"]),
}


def _utc(day: str, local: str) -> pd.Timestamp:
    return pd.Timestamp(f"{day} {local}").tz_localize(ZONE).tz_convert("UTC")


def _samples() -> dict[str, dict[str, Any]]:
    """Every sample, by ID, as ``GET /samples`` lists it."""
    samples: dict[str, dict[str, Any]] = {}

    def add(sample_id, batch, moment, instrument="instrument X", **extra):
        samples[sample_id] = {
            **copy.deepcopy(RECORDED["sample_row"]),
            "sample_item_id": sample_id,
            "sample_item_name": sample_id,
            "sample_batch_id": batch,
            "sample_file_id": f"f-{sample_id}",
            "instrument": instrument,
            "polarity": "-",
            "ionization_mode_id": "im-br",
            "datetime_utc": moment.isoformat(),
            "datetime": moment.tz_convert(ZONE).tz_localize(None).isoformat(),
            **extra,
        }

    for batch, day in DAILY.items():
        for n, local in enumerate(LOCAL_TIMES):
            add(f"s-{batch}-{n}", batch, _utc(day, local))
    # A positive sample of another mode in the second day's batch
    add(
        "s-b2-pos",
        "b2",
        _utc(DAYS[1], "12:00"),
        polarity="+",
        ionization_mode_id="im-h",
    )
    add("s-b5-0", "b5", _utc("2026-02-20", "10:00"))
    add("s-b6-0", "b6", _utc("2025-02-20", "10:00"))
    add("s-b7-0", "b7", _utc("2026-01-10", "10:00"), instrument="instrument Y")
    add("s-b8-0", "b8", _utc("2026-01-11", "10:00"), instrument="instrument Y")
    return samples


SAMPLES = _samples()


def _files() -> list[dict[str, Any]]:
    """Every file, oldest first, as ``GET /sample/files`` lists it: one per
    sample, and four on the day no batch holds."""
    files = []
    for sample in SAMPLES.values():
        files.append(
            {
                **RECORDED["file_row"],
                "sample_file_id": sample["sample_file_id"],
                "filename": f"{sample['sample_file_id']}.raw",
                "instrument": sample["instrument"],
                "datetime_utc": sample["datetime_utc"],
                "datetime": sample["datetime"],
                "polarity": sample["polarity"],
                "acquisition_timezone": ZONE,
            }
        )
    for n, local in enumerate(["01:00", "07:00", "13:00", "19:00"]):
        moment = _utc(GAP_DAY, local)
        files.append(
            {
                **RECORDED["file_row"],
                "sample_file_id": f"f-gap-{n}",
                "filename": f"f-gap-{n}.raw",
                "datetime_utc": moment.isoformat(),
                "datetime": moment.tz_convert(ZONE).tz_localize(None).isoformat(),
                "acquisition_timezone": ZONE,
            }
        )
    return sorted(files, key=lambda row: row["datetime_utc"])


FILES = _files()


def _envelope(data: Any, **extra: Any) -> dict[str, Any]:
    return {"message": "ok", "data": data, **extra}


class FakeAPI:
    """Stands in for ``requests.get`` under the SDK.

    :ivar calls: Each request, as ``(path, params)``.
    :ivar files: The files the files listing lists.
    """

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures: list[tuple[re.Pattern, int]] = []
        self.files = copy.deepcopy(FILES)

    def fail(self, path: str, status: int) -> None:
        """Answer requests whose path fully matches regex *path* with
        *status*."""
        self.failures.append((re.compile(path), status))

    def requests_to(self, path: str) -> list[dict[str, Any]]:
        return [params for called, params in self.calls if re.fullmatch(path, called)]

    def get(self, url: str, params=None, **kwargs) -> requests.Response:
        path = url.split("/api/", 1)[1]
        params = dict(params or {})
        self.calls.append((path, params))
        status, payload = self._route(path, params)
        for pattern, fail_status in self.failures:
            if pattern.fullmatch(path):
                status, payload = fail_status, {"error": "refused by the test"}
        response = requests.Response()
        response.status_code = status
        response.url = url
        response._content = json.dumps(payload).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    def _route(self, path: str, params: dict[str, Any]) -> tuple[int, Any]:
        if path == "workspaces":
            return 200, RECORDED["workspaces"]
        if path == "provenance":
            return 200, RECORDED["provenance"]
        if path == "workspaces/ws1/datasets":
            rows = [
                {
                    **RECORDED["dataset_row"],
                    "dataset_id": d,
                    "dataset_name": name,
                    "instrument": instrument,
                    "workspace_id": "ws1",
                }
                for d, (name, instrument) in DATASETS.items()
            ]
            return 200, _envelope(rows, results=len(rows))
        if path == "sample/batches":
            rows = [
                {
                    **RECORDED["batch_row"],
                    "sample_batch_id": b,
                    "sample_batch_name": name,
                    "dataset_id": d,
                    "polarity": "+-" if b == "b2" else "-",
                }
                for b, (d, name) in BATCHES.items()
                if d == params.get("dataset_id")
            ]
            return 200, _envelope(rows, results=len(rows))
        if path == "samples":
            rows = [
                row
                for row in SAMPLES.values()
                if row["sample_batch_id"] == params.get("sample_batch_id")
            ]
            return 200, _envelope(rows, results=len(rows))
        if path == "sample/files":
            return 200, self._file_page(params)
        if path == "target/collections":
            rows = [
                {
                    **RECORDED["collection_row"],
                    "target_collection_id": c,
                    "target_collection_name": name,
                    "target_collection_type": "CALIBRANTS" if ws is None else "TARGETS",
                    "workspace_id": ws,
                }
                for c, (ws, name, batches) in COLLECTIONS.items()
                if params.get("sample_batch_id") in batches
            ]
            return 200, _envelope(rows, results=len(rows))
        return 404, {"error": "no route"}

    def _file_page(self, params: dict[str, Any]) -> dict[str, Any]:
        rows = self.files
        if "datetime_min" in params:
            lo = pd.Timestamp(params["datetime_min"])
            rows = [row for row in rows if pd.Timestamp(row["datetime_utc"]) >= lo]
        if "datetime_max" in params:
            hi = pd.Timestamp(params["datetime_max"])
            rows = [row for row in rows if pd.Timestamp(row["datetime_utc"]) <= hi]
        if "instrument" in params:
            rows = [row for row in rows if row["instrument"] == params["instrument"]]
        limit, page = int(params["limit"]), int(params["page"])
        return {
            "message": "ok",
            "results": len(rows),
            "data": rows[page * limit : (page + 1) * limit],
        }


@pytest.fixture
def api(monkeypatch) -> FakeAPI:
    from mascope_sdk import _http

    fake = FakeAPI()
    monkeypatch.setattr(_http.requests, "get", fake.get)
    # The SDK backs off between attempts at a transient failure
    monkeypatch.setattr(_http.time, "sleep", lambda seconds: None)
    return fake


@pytest.fixture
def client(api, tmp_path) -> Iterator[Any]:
    from loguru import logger

    from mascope_sdk import MascopeClient
    from mascope_sdk import client as sdk_client

    empty_env = tmp_path / ".env"
    empty_env.write_text("")
    yield MascopeClient(
        url="http://mascope.test",
        access_token="token",
        workspace="Workspace A",
        env_file=empty_env,
        service_name="mascope_sdk",
    )
    # Undo the loguru handler the client adds, as mascope_ai's tests do
    if sdk_client._log_handler_id is not None:
        try:
            logger.remove(sdk_client._log_handler_id)
        except ValueError:
            pass
        sdk_client._log_handler_id = None


def _ids(frame: pd.DataFrame, column: str) -> list[str]:
    return frame[column].tolist()


def _files_of(*batches: str) -> list[str]:
    return [
        row["sample_file_id"]
        for row in sorted(SAMPLES.values(), key=lambda row: row["datetime_utc"])
        if row["sample_batch_id"] in batches
    ]


# --- the Verify line -----------------------------------------------------------


class TestADataset:
    def test_a_dataset_name_resolves_to_its_batches_in_date_order(self, client):
        resolved = resolve(client, Scope(dataset="Campaign 2026"))

        assert not resolved.ambiguous
        # The server lists them by creation; the scope by their samples' time
        assert _ids(resolved.batches, "sample_batch_id") == [
            "b5",
            "b1",
            "b2",
            "b3",
            "b4",
        ]
        assert set(resolved.batches["dataset_name"]) == {"Campaign 2026"}

    def test_each_batch_has_its_count_span_and_polarity(self, client):
        batches = resolve(client, Scope(dataset="Campaign 2026")).batches.set_index(
            "sample_batch_id"
        )

        assert batches.loc["b1", "n_samples"] == 3
        assert batches.loc["b2", "n_samples"] == 4
        assert batches.loc["b1", "first_datetime_utc"] == _utc(DAYS[0], "00:30")
        assert batches.loc["b1", "last_datetime_utc"] == _utc(DAYS[0], "20:00")
        assert batches.loc["b2", "polarity"] == "+-"
        assert str(batches["first_datetime_utc"].dt.tz) == "UTC"
        assert batches.loc["b1", "sample_batch_utc_modified"] == pd.Timestamp(
            RECORDED["batch_row"]["sample_batch_utc_modified"]
        )

    def test_without_a_range_the_files_are_those_of_the_batches_span(self, client, api):
        resolved = resolve(
            client, Scope(dataset="Campaign 2026", batches=re.compile("Br-"))
        )

        # The listing was asked for the span of the batches' samples alone
        (request,) = api.requests_to("sample/files")
        assert pd.Timestamp(request["datetime_min"]) == _utc(DAYS[0], "00:30")
        assert pd.Timestamp(request["datetime_max"]) == _utc(DAYS[3], "20:00")
        assert request["instrument"] == "instrument X"
        assert _ids(resolved.files, "sample_file_id") == _files_of(
            "b1", "b2", "b3", "b4"
        )
        assert resolved.files["in_batch"].all()

    def test_a_dataset_with_no_batch_resolves_to_nothing(self, client, api):
        resolved = resolve(client, Scope(dataset="Imports"))

        assert not resolved.ambiguous
        assert resolved.batches.empty
        assert list(resolved.batches.columns) == BATCH_COLUMNS
        assert resolved.files.empty and resolved.collections.empty
        # Without a range or a sample there is no span to list files of
        assert api.requests_to("sample/files") == []

    def test_the_instruments_time_zone_comes_from_its_files(self, client):
        resolved = resolve(client, Scope(dataset="Campaign 2026"))

        assert resolved.timezone == ZONE
        assert resolved.display_tz == ZONE


class TestATimeRange:
    def test_three_days_select_the_three_daily_batches_and_their_files(self, client):
        resolved = resolve(
            client,
            Scope(dataset="Campaign 2026", start=DAYS[1], end=DAYS[3] + " 23:59:59"),
        )

        assert _ids(resolved.batches, "sample_batch_id") == ["b2", "b3", "b4"]
        assert _ids(resolved.files, "sample_file_id") == _files_of("b2", "b3", "b4")

    def test_a_bound_without_a_zone_is_in_the_instruments_zone(self, client):
        """00:30 local on the first day is 22:30 UTC the day before: a bound
        read as UTC would leave it out."""
        resolved = resolve(
            client, Scope(dataset="Campaign 2026", start=DAYS[1], end=DAYS[2])
        )

        assert resolved.start_utc == pd.Timestamp(f"{DAYS[0]} 22:00", tz="UTC")
        assert resolved.end_utc == pd.Timestamp(f"{DAYS[1]} 22:00", tz="UTC")
        assert _ids(resolved.batches, "sample_batch_id") == ["b2"]
        assert resolved.batches["n_samples"].tolist() == [4]
        assert _ids(resolved.files, "sample_file_id") == _files_of("b2")

    def test_a_bound_with_a_zone_keeps_it(self, client):
        resolved = resolve(
            client,
            Scope(
                dataset="Campaign 2026",
                start=f"{DAYS[1]}T00:00:00+00:00",
                end=f"{DAYS[2]}T00:00:00+00:00",
            ),
        )

        # The 00:30 local sample of the second day is the day before in UTC
        assert resolved.batches.set_index("sample_batch_id")["n_samples"].to_dict() == {
            "b2": 3,
            "b3": 1,
        }

    def test_a_named_zone_reads_the_bounds_in_it(self, client):
        resolved = resolve(
            client,
            Scope(dataset="Campaign 2026", start=DAYS[1], end=DAYS[2], tz="UTC"),
        )

        assert resolved.start_utc == pd.Timestamp(DAYS[1], tz="UTC")
        assert resolved.display_tz == "UTC"
        # The instrument's own zone is still known
        assert resolved.timezone == ZONE

    def test_the_end_is_not_in_the_range(self, client):
        sample = SAMPLES["s-b2-0"]
        resolved = resolve(
            client,
            Scope(dataset="Campaign 2026", end=pd.Timestamp(sample["datetime_utc"])),
        )

        assert "b2" not in set(resolved.batches["sample_batch_id"])
        assert sample["sample_file_id"] not in set(resolved.files["sample_file_id"])

    def test_files_no_batch_holds_are_listed_and_flagged(self, client):
        resolved = resolve(
            client, Scope(dataset="Campaign 2026", start=DAYS[3], end="2026-03-07")
        )

        assert _ids(resolved.batches, "sample_batch_id") == ["b4"]
        flags = resolved.files.set_index("sample_file_id")["in_batch"]
        assert flags[_files_of("b4")].all()
        gap = [f"f-gap-{n}" for n in range(4)]
        assert not flags[gap].any()
        assert len(flags) == 7

    def test_a_range_alone_reads_every_dataset(self, client, api):
        resolved = resolve(client, Scope(start="2026-01-01", end="2026-01-12"))

        assert _ids(resolved.batches, "sample_batch_id") == ["b7", "b8"]
        assert resolved.timezone == ZONE
        assert {
            params["dataset_id"] for params in api.requests_to("sample/batches")
        } == {
            "d1",
            "d2",
            "d3",
            "d4",
        }

    def test_the_zone_most_files_record_is_the_instruments(self, client, api):
        first_day = set(_files_of("b1"))
        for row in api.files:
            if row["sample_file_id"] in first_day:
                row["acquisition_timezone"] = "UTC"

        resolved = resolve(client, Scope(dataset="Campaign 2026"))

        assert resolved.timezone == ZONE

    def test_a_blank_zone_is_no_zone(self, client, api):
        first_day = set(_files_of("b1"))
        for row in api.files:
            if row["sample_file_id"] not in first_day:
                row["acquisition_timezone"] = ""

        resolved = resolve(client, Scope(dataset="Campaign 2026"))

        assert resolved.timezone == ZONE

    def test_the_zone_is_found_around_the_range_west_of_utc_too(self, client, api):
        """Read as UTC, 09:00 at UTC-3 would end the search for the
        instrument's files three hours early, before the noon sample."""
        for row in api.files:
            row["acquisition_timezone"] = "America/Sao_Paulo"

        resolved = resolve(
            client,
            Scope(dataset="Campaign 2026", start=DAYS[1], end=f"{DAYS[1]} 09:00"),
        )

        assert resolved.end_utc == pd.Timestamp(f"{DAYS[1]} 12:00", tz="UTC")
        assert "f-s-b2-pos" in set(resolved.files["sample_file_id"])

    def test_the_files_of_several_instruments_are_in_time_order(self, client, api):
        resolved = resolve(client, Scope(start="2026-01-01", end="2026-03-01"))

        assert {params["instrument"] for params in api.requests_to("sample/files")} == {
            "instrument X",
            "instrument Y",
        }
        assert set(resolved.files["instrument"]) == {"instrument X", "instrument Y"}
        assert resolved.files["datetime_utc"].is_monotonic_increasing

    def test_no_zone_on_the_files_and_none_given_is_refused(self, client, api):
        for row in api.files:
            row["acquisition_timezone"] = None

        with pytest.raises(InvalidScope, match="time zone"):
            resolve(client, Scope(dataset="Campaign 2026", start=DAYS[1]))

    def test_without_a_zone_on_the_files_aware_bounds_still_resolve(self, client, api):
        for row in api.files:
            row["acquisition_timezone"] = None

        resolved = resolve(
            client, Scope(dataset="Campaign 2026", start=f"{DAYS[1]}T00:00Z")
        )

        assert resolved.timezone is None
        assert resolved.display_tz == "UTC"


class TestAmbiguity:
    def test_an_ambiguous_batch_name_returns_both_candidates(self, client, api):
        resolved = resolve(client, Scope(batches="Calibr"))

        assert resolved.ambiguous
        (found,) = resolved.candidates.values()
        assert {(c.id, c.name, c.parent) for c in found} == {
            ("b7", "Calibration", "d3"),
            ("b8", "Calibration repeat", "d3"),
        }
        assert list(resolved.candidates) == ["batches[0]"]
        # Nothing past the ambiguity was read
        assert api.requests_to("samples") == []
        assert resolved.batches.empty and resolved.files.empty

    def test_a_whole_name_wins_over_part_of_another(self, client):
        resolved = resolve(client, Scope(batches="calibration"))

        assert _ids(resolved.batches, "sample_batch_id") == ["b7"]

    def test_two_batches_of_one_whole_name_are_both_candidates(self, client):
        """And the brackets are characters, not a regex group."""
        resolved = resolve(client, Scope(batches="Blank (A)"))

        assert {c.id for c in resolved.candidates["batches[0]"]} == {"b5", "b6"}

    def test_a_dataset_narrows_where_a_batch_is_looked_for(self, client):
        resolved = resolve(client, Scope(dataset="Campaign 2025", batches="Blank (A)"))

        assert _ids(resolved.batches, "sample_batch_id") == ["b6"]

    def test_an_id_wins_over_names(self, client):
        resolved = resolve(client, Scope(batches=["b8"]))

        assert _ids(resolved.batches, "sample_batch_id") == ["b8"]

    def test_a_pattern_takes_every_batch_it_matches(self, client):
        resolved = resolve(client, Scope(batches=re.compile(r"03-0[34] Br")))

        assert not resolved.ambiguous
        assert _ids(resolved.batches, "sample_batch_id") == ["b2", "b3"]

    def test_an_ambiguous_dataset_returns_its_candidates(self, client, api):
        resolved = resolve(client, Scope(dataset="Campaign"))

        assert [c.id for c in resolved.candidates["dataset"]] == ["d1", "d2"]
        assert api.requests_to("sample/batches") == []

    def test_an_ambiguous_instrument_returns_its_candidates(self, client):
        resolved = resolve(client, Scope(instrument="instrument"))

        assert {c.id for c in resolved.candidates["instrument"]} == {
            "instrument X",
            "instrument Y",
        }

    def test_an_ambiguous_collection_returns_its_candidates(self, client):
        resolved = resolve(client, Scope(dataset="Campaign 2026", collection="organic"))

        assert {c.id for c in resolved.candidates["collection"]} == {"c1", "c2"}
        # The rest resolved, the collection kept as it was asked for
        assert len(resolved.batches) == 5
        assert resolved.scope.collection == "organic"

    def test_an_ambiguous_scope_shows_times_in_the_zone_it_names(self, client):
        resolved = resolve(client, Scope(batches="Calibr", tz="Asia/Tokyo"))

        assert resolved.ambiguous
        assert resolved.display_tz == "Asia/Tokyo"

    def test_a_candidate_is_plain_json(self):
        candidate = Candidate("batch", "b7", "Calibration", "d3")

        assert json.loads(json.dumps(candidate.to_dict())) == {
            "kind": "batch",
            "id": "b7",
            "name": "Calibration",
            "parent": "d3",
        }


class TestNarrowing:
    def test_an_instrument_selects_its_datasets_and_files(self, client, api):
        resolved = resolve(client, Scope(instrument="instrument y"))

        assert _ids(resolved.batches, "sample_batch_id") == ["b7", "b8"]
        assert {
            params["dataset_id"] for params in api.requests_to("sample/batches")
        } == {"d3"}
        assert {params["instrument"] for params in api.requests_to("sample/files")} == {
            "instrument Y"
        }
        assert resolved.scope.instrument == "instrument Y"

    def test_an_instrument_drops_the_samples_of_another(self, client):
        resolved = resolve(
            client, Scope(dataset="Calibration runs", instrument="instrument X")
        )

        assert resolved.batches.empty

    def test_a_polarity_selects_its_samples_and_files(self, client):
        resolved = resolve(
            client, Scope(dataset="Campaign 2026", start=DAYS[1], polarity="positive")
        )

        assert _ids(resolved.batches, "sample_batch_id") == ["b2"]
        assert resolved.batches["n_samples"].tolist() == [1]
        assert _ids(resolved.files, "sample_file_id") == ["f-s-b2-pos"]
        assert resolved.scope.polarity == "+"

    @pytest.mark.parametrize("switching", ["+-", "-+"])
    @pytest.mark.parametrize("polarity", ["+", "-"])
    def test_a_polarity_switching_file_is_in_either_polarity(
        self, client, api, switching, polarity
    ):
        for row in api.files:
            if row["sample_file_id"] == "f-s-b2-pos":
                row["polarity"] = switching

        resolved = resolve(
            client,
            Scope(
                dataset="Campaign 2026",
                start=f"{DAYS[1]} 10:00",
                end=f"{DAYS[1]} 14:00",
                polarity=polarity,
            ),
        )

        assert _ids(resolved.files, "sample_file_id") == ["f-s-b2-pos"]

    def test_a_file_of_no_polarity_is_in_none(self, client, api):
        for row in api.files:
            row["polarity"] = None

        resolved = resolve(
            client, Scope(dataset="Campaign 2026", start=DAYS[1], polarity="-")
        )

        assert resolved.files.empty

    def test_a_mode_selects_its_samples(self, client):
        resolved = resolve(client, Scope(dataset="Campaign 2026", mode="im-h"))

        assert _ids(resolved.batches, "sample_batch_id") == ["b2"]
        assert resolved.batches["n_samples"].tolist() == [1]


class TestCollections:
    def test_the_collections_on_the_batches(self, client):
        resolved = resolve(client, Scope(dataset="Campaign 2026"))

        collections = resolved.collections.set_index("target_collection_id")
        assert list(collections.index) == ["c1", "c2"]
        assert collections.loc["c1", "sample_batch_ids"] == ("b1", "b2")
        assert collections.loc["c2", "sample_batch_ids"] == ("b3",)
        assert collections.loc["c1", "workspace_id"] == "ws1"

    def test_a_global_collection_has_no_workspace(self, client):
        resolved = resolve(client, Scope(dataset="Calibration runs"))

        (row,) = resolved.collections.to_dict("records")
        assert row["target_collection_name"] == "Calibrants"
        assert row["workspace_id"] is None

    def test_a_collection_resolves_among_those_on_the_batches(self, client):
        resolved = resolve(
            client, Scope(dataset="Campaign 2026", collection="Organic acids")
        )

        assert resolved.collection == "c1"
        assert resolved.scope.collection == "c1"

    def test_a_collection_on_no_batch_is_not_found(self, client):
        with pytest.raises(NotFound) as caught:
            resolve(client, Scope(dataset="Campaign 2026", collection="Calibrants"))

        assert caught.value.available == ["Organic acids", "Organic acids extra"]


# --- errors ------------------------------------------------------------------


class TestErrors:
    def test_an_unknown_dataset_names_the_candidates(self, client):
        with pytest.raises(NotFound) as caught:
            resolve(client, Scope(dataset="Winter"))

        error = caught.value
        assert "'Calibration runs', 'Campaign 2025', 'Campaign 2026'" in str(error)
        assert error.to_dict() == {
            "error": "not_found",
            "message": error.message,
            "query": "Winter",
            "kind": "dataset",
            "n_available": 4,
            "available": [
                "Calibration runs",
                "Campaign 2025",
                "Campaign 2026",
                "Imports",
            ],
        }

    def test_an_unknown_batch_names_those_of_the_dataset(self, client):
        with pytest.raises(NotFound) as caught:
            resolve(client, Scope(dataset="Calibration runs", batches="Blank"))

        assert caught.value.available == ["Calibration", "Calibration repeat"]

    def test_an_unmatched_pattern_is_not_found(self, client):
        with pytest.raises(NotFound, match="2027"):
            resolve(client, Scope(batches=re.compile("2027")))

    def test_a_refused_token(self, client, api):
        api.fail(r"workspaces/ws1/datasets", 401)

        with pytest.raises(NotAuthenticated, match="Generate a new token"):
            resolve(client, Scope(dataset="Campaign 2026"))

    def test_a_refused_read_names_what_it_was(self, client, api):
        api.fail(r"samples", 403)

        with pytest.raises(AccessDenied, match="list the samples of batch"):
            resolve(client, Scope(dataset="Calibration runs"))

    def test_a_failing_server(self, client, api):
        api.fail(r"sample/files", 500)

        with pytest.raises(Unavailable, match="list the files") as caught:
            resolve(client, Scope(dataset="Calibration runs"))

        assert caught.value.to_dict()["error"] == "unavailable"

    def test_a_batch_gone_since_it_was_listed(self, client, api):
        api.fail(r"samples", 404)

        with pytest.raises(Unavailable, match="resolve the scope again"):
            resolve(client, Scope(dataset="Calibration runs"))

    def test_a_request_the_server_refuses(self, client, api):
        api.fail(r"sample/files", 422)

        with pytest.raises(InvalidScope, match="refused to list the files"):
            resolve(client, Scope(dataset="Calibration runs"))

    def test_a_range_empty_once_in_utc(self, client):
        """The end, 01:00 in Helsinki, is 23:00 UTC the day before."""
        with pytest.raises(InvalidScope, match="not before"):
            resolve(
                client,
                Scope(
                    dataset="Campaign 2026",
                    start=f"{DAYS[1]}T00:00Z",
                    end=f"{DAYS[1]} 01:00",
                ),
            )

    def test_a_wall_time_the_clocks_skip(self, client):
        with pytest.raises(InvalidScope, match="clocks change"):
            resolve(
                client,
                Scope(dataset="Campaign 2026", start="2026-03-29 03:30", tz=ZONE),
            )


# --- the scope and its key -------------------------------------------------------


class TestScope:
    def test_the_key_is_a_stable_short_hash(self):
        """Pinned: a key that changed between runs or releases would orphan
        every store directory named by it."""
        scope = Scope(dataset="d1", start="2026-03-02T00:00Z")

        assert scope.key == Scope(dataset="d1", start="2026-03-02T00:00Z").key
        assert re.fullmatch(r"[0-9a-f]{12}", scope.key)
        assert scope.key == "2d422cc5bab3"

    def test_every_field_that_selects_is_in_the_key(self):
        base = Scope(dataset="d1")
        others = [
            Scope(dataset="d2"),
            Scope(dataset="d1", batches="b1"),
            Scope(dataset="d1", instrument="instrument X"),
            Scope(dataset="d1", start="2026-03-02T00:00Z"),
            Scope(dataset="d1", end="2026-03-02T00:00Z"),
            Scope(dataset="d1", polarity="-"),
            Scope(dataset="d1", mode="im-br"),
            Scope(dataset="d1", collection="c1"),
            Scope(dataset=re.compile("d1")),
            Scope(dataset=re.compile("d1", re.IGNORECASE)),
        ]

        keys = {base.key} | {scope.key for scope in others}
        assert len(keys) == len(others) + 1

    def test_an_aware_bound_is_keyed_in_utc(self):
        assert Scope(dataset="d1", start="2026-03-02T02:00+02:00").key == (
            Scope(dataset="d1", start="2026-03-02T00:00Z").key
        )

    def test_the_display_zone_is_not_in_the_key_of_an_aware_range(self):
        aware = "2026-03-02T00:00Z"

        assert Scope(dataset="d1", start=aware).key == (
            Scope(dataset="d1", start=aware, tz="Asia/Tokyo").key
        )
        # A naive bound means a time in tz, so there it is
        assert Scope(dataset="d1", start="2026-03-02").key != (
            Scope(dataset="d1", start="2026-03-02", tz="Asia/Tokyo").key
        )

    def test_the_resolved_key_is_the_same_for_every_spelling(self, client):
        spellings = [
            Scope(dataset="Campaign 2026", start=DAYS[1], end=DAYS[2]),
            Scope(dataset="campaign 2026", start=DAYS[1], end=DAYS[2]),
            Scope(dataset="d1", start=DAYS[1], end=DAYS[2], tz=ZONE),
            Scope(
                dataset="d1",
                start=f"{DAYS[0]}T22:00Z",
                end=f"{DAYS[1]}T22:00Z",
                tz="UTC",
            ),
        ]

        keys = {resolve(client, scope).key for scope in spellings}
        assert len({scope.key for scope in spellings}) == len(spellings)
        assert len(keys) == 1

    def test_the_resolved_scope_names_by_id(self, client):
        resolved = resolve(
            client,
            Scope(
                dataset="Campaign 2026",
                batches=["2026-03-03", re.compile("03-04")],
                collection="Organic acids",
            ),
        )

        assert resolved.scope.dataset == "d1"
        # A pattern stays one, so that a batch added later is in it too
        assert resolved.scope.batches == ("b2", re.compile("03-04"))
        assert resolved.requested.dataset == "Campaign 2026"
        assert isinstance(resolved, ResolvedScope)
        assert resolved.key in repr(resolved)

    def test_the_batches_of_a_scope_are_a_tuple(self):
        assert Scope(batches="b1").batches == ("b1",)
        assert Scope(batches=["b1", "b2"]).batches == ("b1", "b2")

    def test_times_are_timestamps(self):
        scope = Scope(dataset="d1", start="2026-03-02", end="2026-03-03T00:00Z")

        assert scope.start == pd.Timestamp("2026-03-02")
        assert scope.end == pd.Timestamp("2026-03-03", tz="UTC")

    @pytest.mark.parametrize(
        "kwargs, says",
        [
            ({}, "something to start from"),
            ({"dataset": "d1", "polarity": "both"}, "polarity"),
            ({"dataset": "d1", "tz": "Mars/Olympus"}, "no time zone"),
            ({"dataset": "d1", "start": "2026-03-03", "end": "2026-03-02"}, "before"),
            ({"dataset": "d1", "start": "soon"}, "not a time"),
            ({"batches": []}, "empty"),
            ({"batches": ["b1", 2]}, "each of batches"),
            ({"dataset": 3}, "dataset is a name"),
            ({"dataset": "d1", "mode": ""}, "mode is a non-empty string"),
        ],
    )
    def test_a_scope_that_means_nothing_is_refused(self, kwargs, says):
        with pytest.raises(InvalidScope, match=says):
            Scope(**kwargs)

    def test_an_invalid_scope_is_a_value_error(self):
        with pytest.raises(ValueError):
            Scope()

    def test_a_scope_is_frozen(self):
        scope = Scope(dataset="d1")

        with pytest.raises(AttributeError):
            scope.dataset = "d2"
