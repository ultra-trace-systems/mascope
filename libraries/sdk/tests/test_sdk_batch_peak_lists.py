"""
Hermetic tests for ``load_peaks`` reading a batch's peak lists in one request.

A server that offers ``GET /api/batch-peaks/batch/{id}/peaks`` answers every
peak of every sample of a batch as one Parquet table; ``load_peaks`` reads
each batch that way and lays the table out as its per-sample reads were laid
out, so a script cannot tell the two apart by the frame. An older server
answers that route 404, and ``load_peaks`` reads each sample on its own, as
it always did.

The fake server here serves both from one set of peaks, so the two paths can
be compared row for row. It needs no running stack; the contract test on the
demo stack (``test_contract.py``) compares them against a real server.
"""

import io
import json
import re
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mascope_sdk import _loaders
from mascope_sdk.exceptions import NotFoundError, ServerError
from mascope_sdk.resources.batch_peaks import BatchPeaksResource
from mascope_sdk.resources.ionization import IonizationResource
from mascope_sdk.resources.samples import SamplesResource, score_matches


def _match(isotope: str, ion: str, score: float, abundance: float) -> dict:
    return {
        "target_isotope_id": isotope,
        "match_score_isotope": score,
        "relative_abundance": abundance,
        "target_isotope_formula": "CH4N2O",
        "target_ion_id": ion,
        "target_ion_formula": "CH5N2O+",
        "target_compound_id": "cmp-1",
        "target_compound_name": "Urea",
        "target_compound_formula": "CH4N2O",
        "ionization_mechanism_id": "mech-1",
        "target_collection_ids": ["tc-1"],
        "target_collection_names": ["Reagent ions"],
    }


#: Per sample: its batch, and its peaks as (peak id, m/z, height, s/n, matches).
#: s1 and s2 match the same ion with different scores, so an ion score taken
#: over the batch rather than per sample would show.
SAMPLES = {
    "s1": (
        "b1",
        [
            ("s1-p1", 61.04, 500.0, 12.0, [_match("iso-1", "ion-1", 0.9, 1.0)]),
            ("s1-p2", 62.04, 20.0, None, [_match("iso-2", "ion-1", 0.5, 0.1)]),
            ("s1-p3", 97.0, 7.0, 3.0, []),
        ],
    ),
    "s2": (
        "b1",
        [("s2-p1", 61.04, 400.0, 11.0, [_match("iso-1", "ion-1", 0.4, 1.0)])],
    ),
    "s3": ("b2", [("s3-p1", 120.0, 9.0, 2.0, [])]),
}
BATCHES = {"b1": "Batch one", "b2": "Batch two"}
DATETIMES = {
    "s1": pd.Timestamp("2026-01-01 10:00", tz="UTC"),
    "s2": pd.Timestamp("2026-01-01 11:00", tz="UTC"),
    "s3": pd.Timestamp("2026-01-02 09:00", tz="UTC"),
}

#: The columns of the server's table, as it types them.
SCHEMA = pa.schema(
    [
        ("sample_item_id", pa.string()),
        ("sample_item_name", pa.string()),
        ("datetime_utc", pa.timestamp("us", tz="UTC")),
        ("peak_id", pa.string()),
        ("mz", pa.float64()),
        ("sparsity", pa.float64()),
        ("signal_to_noise", pa.float64()),
        ("segment", pa.int64()),
        ("area", pa.float64()),
        ("height", pa.float64()),
        ("is_satellite", pa.bool_()),
    ]
)
MATCH_SCHEMA = pa.schema(
    [
        ("match_score_isotope", pa.float64()),
        ("relative_abundance", pa.float64()),
        ("target_isotope_id", pa.string()),
        ("target_isotope_formula", pa.string()),
        ("target_ion_id", pa.string()),
        ("target_ion_formula", pa.string()),
        ("target_compound_id", pa.string()),
        ("target_compound_name", pa.string()),
        ("target_compound_formula", pa.string()),
        ("ionization_mechanism_id", pa.string()),
        ("target_collection_ids", pa.list_(pa.string())),
        ("target_collection_names", pa.list_(pa.string())),
    ]
)


class _Response:
    def __init__(self, payload: Any = None, content: bytes = b""):
        self._payload = payload
        self.content = content

    def json(self) -> Any:
        return self._payload


class FakeServer:
    """The peaks API, per sample and per batch, over the same peaks.

    ``batch_reads`` False is a server older than the batch read: it answers
    the route 404, as FastAPI answers a path it does not have.
    """

    def __init__(self, *, batch_reads: bool = True):
        self.batch_reads = batch_reads
        #: Samples whose read the server fails, and how.
        self.unreadable: dict[str, tuple[int, str]] = {}
        #: Batches whose whole read fails.
        self.down: set[str] = set()
        #: List a batch's samples in reverse, as a server may order samples
        #: that started at the same moment differently from the listing.
        self.reverse = False
        self.paths: list[str] = []
        #: Per batch read, the samples whose peaks the server opened, and
        #: the samples the request named (None for none).
        self.opened: list[list[str]] = []
        self.named: list[list[str] | None] = []
        #: Samples gone from the server after the listing: no read names them.
        self.vanished: set[str] = set()
        #: Per request, the options it was sent with beyond its parameters.
        self.options: list[dict] = []

    def http_get(self, url, path, access_token, params=None, **kwargs):
        self.paths.append(path)
        self.options.append(kwargs)
        params = params or {}
        if path == "ionization_mechanisms":
            return _Response(
                {
                    "data": [
                        {
                            "ionization_mechanism_id": "mech-1",
                            "ionization_mechanism": "[M+H]+",
                        }
                    ]
                }
            )
        sample = re.fullmatch(r"samples/([^/]+)/peaks", path)
        if sample:
            return self._sample_read(sample.group(1), params)
        batch = re.fullmatch(r"batch-peaks/batch/([^/]+)/peaks", path)
        if batch:
            if not self.batch_reads:
                raise NotFoundError("Not Found", status_code=404, url=path)
            if batch.group(1) in self.down:
                raise ServerError("Gateway timeout", status_code=504, url=path)
            return self._batch_read(batch.group(1), params)
        raise AssertionError(f"unexpected request to {path!r}")

    def _sample_read(self, sample_id: str, params: dict) -> _Response:
        if sample_id in self.unreadable:
            status, error = self.unreadable[sample_id]
            raise NotFoundError(error, status_code=status, url=sample_id)
        peaks = SAMPLES[sample_id][1]
        data = {
            "peak_id": [p[0] for p in peaks],
            "mz": [p[1] for p in peaks],
            "sparsity": [0.0] * len(peaks),
            "signal_to_noise": [p[3] for p in peaks],
            "segment": None,
            "segments": None,
            "area": [p[2] * 2 for p in peaks] if params["areas"] == "true" else None,
            "height": [p[2] for p in peaks] if params["heights"] == "true" else None,
            "match": [p[4] for p in peaks] if params.get("matches") == "true" else None,
        }
        return _Response({"message": "ok", "warnings": [], "data": data})

    def _batch_read(self, batch_id: str, params: dict) -> _Response:
        matches = params.get("matches") == "true"
        asked = params.get("sample_item_id")
        rows, read, failures, segments = [], [], [], {}
        opened: list[str] = []
        self.opened.append(opened)
        self.named.append(asked)
        listed = list(SAMPLES.items())
        for sample_id, (batch, peaks) in reversed(listed) if self.reverse else listed:
            if batch != batch_id or sample_id in self.vanished:
                continue
            if asked is not None and sample_id not in asked:
                continue
            opened.append(sample_id)
            name = f"Sample {sample_id}"
            if sample_id in self.unreadable:
                status, error = self.unreadable[sample_id]
                failures.append(
                    {
                        "sample_item_id": sample_id,
                        "sample_item_name": name,
                        "status_code": status,
                        "error": error,
                    }
                )
                continue
            for peak_id, mz, height, snr, peak_matches in peaks:
                row = {
                    "sample_item_id": sample_id,
                    "sample_item_name": name,
                    "datetime_utc": DATETIMES[sample_id],
                    "peak_id": peak_id,
                    "mz": mz,
                    "sparsity": 0.0,
                    "signal_to_noise": snr,
                    "segment": None,
                    "area": height * 2,
                    "height": height,
                    "is_satellite": False,
                }
                if not matches:
                    rows.append(row)
                    continue
                for match in peak_matches or [None]:
                    rows.append(
                        {**row, **{k: (match or {}).get(k) for k in MATCH_SCHEMA.names}}
                    )
            read.append({"sample_item_id": sample_id, "rows": len(peaks)})
            segments[sample_id] = None
        schema = pa.schema(list(SCHEMA) + list(MATCH_SCHEMA)) if matches else SCHEMA
        table = pa.Table.from_pylist(rows, schema=schema).replace_schema_metadata(
            {
                b"mascope": json.dumps(
                    {
                        "version": 1,
                        "samples": read,
                        "failures": failures,
                        "segments": segments,
                    }
                )
            }
        )
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        return _Response(content=buffer.getvalue())


class StubClient:
    """What the loaders and the resources read off a client."""

    url = "http://testserver"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}
        self._server_has_batch_peak_lists = True
        self.samples = SamplesResource(self)
        self.batch_peaks = BatchPeaksResource(self)

    @property
    def ionization(self) -> IonizationResource:
        return IonizationResource(self)


def _tasks(sample_ids=("s1", "s2", "s3")) -> list[tuple[pd.Series, str]]:
    """The samples ``_collect_sample_tasks`` lists, as the listing gives them."""
    listing = pd.DataFrame(
        {
            "sample_item_id": list(sample_ids),
            "sample_item_name": [f"Sample {s}" for s in sample_ids],
            "sample_batch_id": [SAMPLES[s][0] for s in sample_ids],
            "datetime_utc": pd.to_datetime(
                [DATETIMES[s].isoformat() for s in sample_ids], utc=True
            ),
        }
    )
    return [(row, BATCHES[row["sample_batch_id"]]) for _, row in listing.iterrows()]


@pytest.fixture
def serve(monkeypatch):
    """A server and a client on it; the samples asked for are those given."""

    def make(*, batch_reads: bool = True, sample_ids=("s1", "s2", "s3")):
        server = FakeServer(batch_reads=batch_reads)
        monkeypatch.setattr("mascope_sdk.resources._base.http_get", server.http_get)
        monkeypatch.setattr(
            _loaders,
            "_collect_sample_tasks",
            lambda *a, **k: (_tasks(sample_ids), "ds-1"),
        )
        return StubClient(), server

    return make


def _load(client, **options) -> pd.DataFrame | None:
    return _loaders.load_peaks(client, "ds-1", confirm_above=None, **options)


def _fan_out(serve, sample_ids=("s1", "s2", "s3"), **options) -> pd.DataFrame:
    """The same load from a server that reads sample by sample."""
    client, _ = serve(batch_reads=False, sample_ids=sample_ids)
    return _load(client, **options)


class TestOneRequestPerBatch:
    def test_each_batch_is_one_request_and_no_sample_is_read_alone(self, serve):
        client, server = serve()

        _load(client)

        assert sorted(server.paths) == [
            "batch-peaks/batch/b1/peaks",
            "batch-peaks/batch/b2/peaks",
        ]

    def test_the_frame_is_the_per_sample_reads_frame(self, serve):
        client, _ = serve()

        bulk = _load(client, matches=False)
        fan_out = _fan_out(serve, matches=False)

        # The one column the batch read adds; satellites are left out
        assert not bulk["is_satellite"].any()
        pd.testing.assert_frame_equal(bulk.drop(columns=["is_satellite"]), fan_out)
        assert bulk.attrs["complete"] is True
        assert bulk.attrs["failures"] == []
        assert bulk.attrs["warnings"] == []
        assert bulk.attrs["segments"] == {"s1": None, "s2": None, "s3": None}

    def test_with_matches_the_frame_is_the_per_sample_reads_frame(self, serve):
        client, _ = serve()

        bulk = _load(client, matches=True)
        fan_out = _fan_out(serve, matches=True)

        # The per-sample reads leave NaN where a sample matched nothing and
        # its match columns were stacked in from the others; both are NA
        pd.testing.assert_frame_equal(
            _na_as_none(bulk.drop(columns=["is_satellite"])), _na_as_none(fan_out)
        )
        # One row per match, the unmatched peak keeping one
        assert bulk["peak_id"].tolist() == ["s1-p1", "s1-p2", "s1-p3", "s2-p1", "s3-p1"]
        assert bulk["ionization_mechanism"].tolist()[:2] == ["[M+H]+", "[M+H]+"]

    def test_matches_are_off_by_default_on_the_batch_read(self, serve):
        client, server = serve()

        peaks = _load(client)

        assert "target_ion_id" not in peaks.columns
        assert "ionization_mechanisms" not in server.paths

    @pytest.mark.parametrize(
        ("left_out", "kept"), [("areas", "height"), ("heights", "area")]
    )
    def test_areas_or_heights_can_be_left_out_as_before(self, serve, left_out, kept):
        client, _ = serve()

        bulk = _load(client, matches=False, **{left_out: False})
        fan_out = _fan_out(serve, matches=False, **{left_out: False})

        assert kept in bulk.columns and len(bulk.columns) == len(fan_out.columns) + 1
        pd.testing.assert_frame_equal(bulk.drop(columns=["is_satellite"]), fan_out)

    def test_the_samples_come_in_the_listings_order_whatever_the_servers(self, serve):
        client, server = serve()
        server.reverse = True

        bulk = _load(client, matches=True)
        fan_out = _fan_out(serve, matches=True)

        assert bulk["sample_item_id"].tolist() == ["s1", "s1", "s1", "s2", "s3"]
        pd.testing.assert_frame_equal(
            _na_as_none(bulk.drop(columns=["is_satellite"])), _na_as_none(fan_out)
        )

    def test_the_frame_says_what_the_per_sample_reads_say(self, serve):
        # One batch: what the batch read says of every sample it read,
        # asked for or not, is not the load's to pass on
        client, _ = serve(sample_ids=("s1",))

        bulk = _load(client, matches=False)
        fan_out = _fan_out(serve, sample_ids=("s1",), matches=False)

        assert "samples" not in bulk.attrs
        assert bulk.attrs == fan_out.attrs

    def test_a_sample_filter_keeps_only_those_samples(self, serve):
        client, server = serve(sample_ids=("s2",))

        peaks = _load(client)

        assert server.paths == ["batch-peaks/batch/b1/peaks"]
        assert peaks["sample_item_id"].unique().tolist() == ["s2"]
        assert peaks.attrs["segments"] == {"s2": None}


class TestASampleFilter:
    def test_only_the_samples_chosen_are_read(self, serve):
        client, server = serve(sample_ids=("s2",))

        peaks = _load(client, samples="s2")

        assert server.paths == ["batch-peaks/batch/b1/peaks"]
        # The batch's other sample was never opened
        assert server.opened == [["s2"]]
        assert peaks["sample_item_id"].unique().tolist() == ["s2"]
        assert peaks.attrs["complete"] is True

    def test_without_one_each_batch_is_read_whole(self, serve):
        client, server = serve()

        _load(client)

        assert sorted(server.opened) == [["s1", "s2"], ["s3"]]
        # A whole batch is asked for by its id alone, however many samples
        assert server.named == [None, None]

    def test_a_large_choice_is_read_in_parts_and_stacked_in_order(
        self, serve, monkeypatch
    ):
        monkeypatch.setattr(_loaders, "_SAMPLES_PER_READ", 1)
        client, server = serve()

        bulk = _load(client, samples="Sample", matches=False)
        fan_out = _fan_out(serve, samples="Sample", matches=False)

        assert server.paths.count("batch-peaks/batch/b1/peaks") == 2
        assert sorted(server.opened) == [["s1"], ["s2"], ["s3"]]
        pd.testing.assert_frame_equal(
            _na_as_none(bulk.drop(columns=["is_satellite"])), _na_as_none(fan_out)
        )

    def test_a_batch_larger_than_one_read_is_read_in_parts(self, serve, monkeypatch):
        # The server builds a read's table in full before it answers: a whole
        # large batch in one request would pass the read timeout
        monkeypatch.setattr(_loaders, "_SAMPLES_PER_READ", 1)
        client, server = serve()

        bulk = _load(client, matches=False)
        fan_out = _fan_out(serve, matches=False)

        assert server.paths.count("batch-peaks/batch/b1/peaks") == 2
        # Each part of the larger batch names its samples; the batch that
        # fits in one read is still asked for by its id alone
        assert sorted(server.named, key=str) == [None, ["s1"], ["s2"]]
        pd.testing.assert_frame_equal(
            _na_as_none(bulk.drop(columns=["is_satellite"])), _na_as_none(fan_out)
        )

    def test_parts_are_read_two_at_a_time(self, serve, monkeypatch):
        # The server reads several samples of each part at once: eight parts
        # at a time would open several times the stores the fan-out did
        monkeypatch.setattr(_loaders, "_SAMPLES_PER_READ", 1)
        client, _ = serve()
        workers: list[int] = []
        real = _loaders.run_concurrent

        def recorded(fn, items, *, max_workers, **kwargs):
            workers.append(max_workers)
            return real(fn, items, max_workers=max_workers, **kwargs)

        monkeypatch.setattr(_loaders, "run_concurrent", recorded)

        _load(client, max_workers=8)

        assert workers == [2]

    def test_a_part_that_failed_fails_its_samples_alone(self, serve, monkeypatch):
        monkeypatch.setattr(_loaders, "_SAMPLES_PER_READ", 1)
        client, server = serve(sample_ids=("s1", "s2"))
        real = server._batch_read

        def s2_down(batch_id, params):
            if params.get("sample_item_id") == ["s2"]:
                raise ServerError("Gateway timeout", status_code=504, url=batch_id)
            return real(batch_id, params)

        server._batch_read = s2_down

        peaks = _load(client, samples="Sample")

        assert peaks["sample_item_id"].unique().tolist() == ["s1"]
        assert [
            (f["sample_item_id"], f["error_type"]) for f in peaks.attrs["failures"]
        ] == [("s2", "ServerError")]


class TestATimeout:
    def test_a_read_that_timed_out_is_not_sent_again(self, serve):
        # Sent again, the server would build the same table from the start,
        # beside the build still running for the read that timed out
        client, server = serve()

        _load(client)

        reads = [
            options
            for path, options in zip(server.paths, server.options)
            if path.startswith("batch-peaks/")
        ]
        assert reads and all(o.get("retry_timeouts") is False for o in reads)


class TestAnOlderServer:
    def test_it_is_read_sample_by_sample_with_matches_as_before(self, serve):
        client, server = serve(batch_reads=False)

        peaks = _load(client)

        assert server.paths.count("batch-peaks/batch/b1/peaks") == 1
        assert sorted(p for p in server.paths if p.startswith("samples/")) == [
            "samples/s1/peaks",
            "samples/s2/peaks",
            "samples/s3/peaks",
        ]
        assert "target_ion_id" in peaks.columns
        assert "is_satellite" not in peaks.columns

    def test_it_is_not_asked_for_a_batch_read_again(self, serve):
        client, server = serve(batch_reads=False)

        _load(client)
        server.paths.clear()
        _load(client)

        assert not any(p.startswith("batch-peaks/") for p in server.paths)


class TestFailures:
    def test_a_sample_the_server_could_not_read_is_named(self, serve):
        client, server = serve()
        server.unreadable["s2"] = (404, "Sample file was not found")

        peaks = _load(client)

        assert set(peaks["sample_item_id"]) == {"s1", "s3"}
        assert peaks.attrs["complete"] is False
        assert peaks.attrs["failures"] == [
            {
                "sample_batch_name": "Batch one",
                "sample_item_id": "s2",
                "sample_item_name": "Sample s2",
                "error_type": "NotFoundError",
                # The request that carried the answer is the batch's
                "error": "[HTTP 404] Sample file was not found "
                "(URL: http://testserver/api/batch-peaks/batch/b1/peaks)",
            }
        ]

    def test_it_is_named_as_the_per_sample_read_names_it(self, serve):
        client, server = serve()
        server.unreadable["s2"] = (404, "Sample file was not found")
        bulk = _load(client, matches=False)

        client, server = serve(batch_reads=False)
        server.unreadable["s2"] = (404, "Sample file was not found")
        fan_out = _load(client, matches=False)

        # The same record but for the URL of the request that answered
        def unplaced(failures):
            return [
                {**f, "error": re.sub(r" \(URL: .*\)$", "", f["error"])}
                for f in failures
            ]

        assert unplaced(bulk.attrs["failures"]) == unplaced(fan_out.attrs["failures"])

    def test_a_sample_not_asked_for_is_no_failure_of_the_load(self, serve):
        client, server = serve(sample_ids=("s1",))
        server.unreadable["s2"] = (404, "Sample file was not found")

        peaks = _load(client)

        assert peaks.attrs["complete"] is True
        assert peaks.attrs["failures"] == []

    def test_a_sample_the_read_left_out_unnamed_is_a_failure(self, serve):
        # Gone between the listing and the read: the per-sample read of it
        # would have answered 404
        client, server = serve()
        server.vanished.add("s2")

        peaks = _load(client)

        assert set(peaks["sample_item_id"]) == {"s1", "s3"}
        assert peaks.attrs["complete"] is False
        assert [
            (f["sample_item_id"], f["error_type"]) for f in peaks.attrs["failures"]
        ] == [("s2", "NotFoundError")]

    def test_a_batch_whose_read_failed_fails_each_of_its_samples(self, serve):
        client, server = serve()
        server.down.add("b1")

        peaks = _load(client)

        assert set(peaks["sample_item_id"]) == {"s3"}
        assert [
            (f["sample_item_id"], f["error_type"]) for f in peaks.attrs["failures"]
        ] == [("s1", "ServerError"), ("s2", "ServerError")]
        assert client._server_has_batch_peak_lists is True

    def test_when_every_sample_failed_the_frame_is_empty_and_says_so(self, serve):
        client, server = serve(sample_ids=("s3",))
        server.unreadable["s3"] = (500, "Unexpected error")

        peaks = _load(client)

        assert peaks is not None and peaks.empty
        assert peaks.attrs["complete"] is False
        assert peaks.attrs["failures"][0]["error_type"] == "ServerError"


class TestThePeakListsRead:
    def test_list_columns_come_as_lists_and_the_metadata_as_attrs(self, serve):
        client, _ = serve()
        server_table = client.batch_peaks.peak_lists("b1", matches=True)

        # As get_peaks gives them: lists, not numpy arrays
        assert type(server_table["target_collection_ids"].iloc[0]) is list
        assert server_table["target_collection_ids"].iloc[0] == ["tc-1"]
        assert server_table["target_collection_ids"].iloc[2] is None
        assert server_table.attrs["samples"] == [
            {"sample_item_id": "s1", "rows": 3},
            {"sample_item_id": "s2", "rows": 1},
        ]
        assert server_table.attrs["complete"] is True

    def test_a_sample_the_server_could_not_read_makes_it_incomplete(self, serve):
        client, server = serve()
        server.unreadable["s2"] = (404, "Sample file was not found")

        server_table = client.batch_peaks.peak_lists("b1")

        assert server_table.attrs["complete"] is False
        assert server_table.attrs["failures"] == [
            {
                "sample_item_id": "s2",
                "sample_item_name": "Sample s2",
                "status_code": 404,
                "error": "Sample file was not found",
            }
        ]

    def test_it_asks_for_what_it_is_given(self, serve, monkeypatch):
        client, server = serve()
        asked = []
        real = server.http_get

        def spy(url, path, access_token, params=None, **kwargs):
            asked.append(params)
            return real(url, path, access_token, params=params, **kwargs)

        monkeypatch.setattr("mascope_sdk.resources._base.http_get", spy)
        client.batch_peaks.peak_lists("b1", satellites=True, average=False)
        client.batch_peaks.peak_lists("b1", samples=("s2",))

        assert asked == [
            {"matches": "false", "satellites": "true", "average": "false"},
            {
                "matches": "false",
                "satellites": "false",
                "average": "true",
                "sample_item_id": ["s2"],
            },
        ]
        assert server.opened[-1] == ["s2"]

    def test_an_empty_sample_list_is_refused_not_read_as_every_sample(self, serve):
        client, server = serve()

        with pytest.raises(ValueError, match="samples is empty"):
            client.batch_peaks.peak_lists("b1", samples=[])
        assert server.paths == []


class TestScoreMatches:
    def test_an_ion_is_scored_within_its_own_sample(self):
        # The same ion in two samples: each sample's score is its own
        rows = pd.DataFrame(
            {
                "sample_item_id": ["s1", "s1", "s2"],
                "match_score_isotope": [0.9, 0.5, 0.4],
                "relative_abundance": [1.0, 0.1, 1.0],
                "target_ion_id": ["ion-1", "ion-1", "ion-1"],
                "target_compound_id": ["cmp-1", "cmp-1", "cmp-1"],
                "ionization_mechanism_id": ["mech-1"] * 3,
            }
        )

        class Mechanisms:
            def list(self):
                return pd.DataFrame(
                    {
                        "ionization_mechanism_id": ["mech-1"],
                        "ionization_mechanism": ["X"],
                    }
                )

        scored = score_matches(rows, type("C", (), {"ionization": Mechanisms()})())

        assert scored["match_score_ion"].tolist() == pytest.approx([0.95, 0.95, 0.4])
        assert scored["match_score_compound"].tolist() == pytest.approx(
            [0.95, 0.95, 0.4]
        )
        assert scored["ionization_mechanism"].tolist() == ["X"] * 3


class TestTheSnapshot:
    def test_save_path_writes_a_parquet_that_reads_back_equal(
        self, serve, monkeypatch, tmp_path
    ):
        from mascope_sdk import MascopeClient

        client, server = serve()
        server.unreadable["s3"] = (404, "Sample file was not found")
        provenance = {"deployment_id": "d-1", "produced_with": {"mascope_version": "1"}}
        monkeypatch.setattr(
            StubClient, "_with_provenance", _stamped(provenance), raising=False
        )
        path = tmp_path / "batch.parquet"

        peaks = MascopeClient.load_peaks(
            client, "ds-1", confirm_above=None, save_path=path
        )

        again = pd.read_parquet(path)
        pd.testing.assert_frame_equal(again, peaks)
        assert again.attrs == peaks.attrs
        assert again.attrs["provenance"] == provenance
        assert again.attrs["failures"][0]["sample_item_id"] == "s3"

    def test_nothing_is_written_for_a_load_that_found_nothing(
        self, serve, monkeypatch, tmp_path
    ):
        from mascope_sdk import MascopeClient

        client, _ = serve()
        monkeypatch.setattr(
            _loaders, "_collect_sample_tasks", lambda *a, **k: ([], "d")
        )
        monkeypatch.setattr(StubClient, "_with_provenance", _stamped({}), raising=False)
        path = tmp_path / "batch.parquet"

        assert MascopeClient.load_peaks(client, "ds-1", save_path=path) is None
        assert not path.exists()


def _na_as_none(frame: pd.DataFrame) -> pd.DataFrame:
    """The frame with every missing value of an object column as None."""
    frame = frame.copy()
    for name in frame.columns:
        if not pd.api.types.is_object_dtype(frame[name]):
            continue
        frame[name] = frame[name].map(
            lambda v: None if not isinstance(v, list) and pd.isna(v) else v
        )
    return frame


def _stamped(provenance: dict):
    def with_provenance(self, frame):
        if frame is not None:
            frame.attrs["provenance"] = provenance
        return frame

    return with_provenance
