"""
Hermetic unit tests for ``SamplesResource.get_peaks`` match flattening, in
particular the ``target_collection_names`` the peaks payload carries next to
``target_collection_ids``. These mock the HTTP layer (like
``test_peak_assignments.py``) and do not need a running stack.
"""

import re
from typing import Any

import pandas as pd
import pytest

from mascope_sdk import _loaders
from mascope_sdk.resources.ionization import IonizationResource
from mascope_sdk.resources.samples import SamplesResource


SAMPLE_ID = "sample-1"


def _match(
    collection_ids: list[str],
    collection_names: list[str] | None,
    isotope: str = "iso-1",
) -> dict[str, Any]:
    """A match dict carrying every key ``get_peaks`` flattens.

    ``collection_names`` of None models a server older than the release that
    added the names: the key is simply absent from the payload.
    """
    match = {
        "match_score_isotope": 0.9,
        "relative_abundance": 1.0,
        "target_isotope_id": isotope,
        "target_isotope_formula": "C6H12O6",
        "target_ion_id": "ion-1",
        "target_ion_formula": "C6H13O6+",
        "target_compound_id": "cmp-1",
        "target_compound_name": "Glucose",
        "target_compound_formula": "C6H12O6",
        "ionization_mechanism_id": "mech-1",
        "target_collection_ids": collection_ids,
    }
    if collection_names is not None:
        match["target_collection_names"] = collection_names
    return match


#: p1 matches two collections, p2 one, p3 is unmatched.
PEAK_MATCHES = [
    [_match(["tc-1", "tc-2"], ["PFAS screen", "Reagent ions"])],
    [_match(["tc-2"], ["Reagent ions"], isotope="iso-2")],
    [],
]

#: The same peaks as served by a server that predates the names.
PEAK_MATCHES_WITHOUT_NAMES = [
    [_match(["tc-1", "tc-2"], None)],
    [_match(["tc-2"], None, isotope="iso-2")],
    [],
]


#: A stitched sample's segments as the listing describes them, one per scan
#: stream - more of them than the peaks below, and fewer than a real sample's
#: peaks, so a frame built with them in raises.
SEGMENTS = [
    {"index": i, "key": f"stream {i}", "label": f"m/z {lo}-{lo + 50}", "from": 0}
    for i, lo in enumerate((50, 100, 150, 200))
]

#: The key left out of the listing altogether, as a server older than the
#: segments sends it.
OMITTED = object()


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeServer:
    """A canned peaks API behind the ``http_get`` seam.

    Serves the peaks payload (honouring ``matches=false``) and the ionization
    mechanism listing - the two calls ``get_peaks`` makes. Every other path is
    recorded and refused, so a regression that reaches for a third endpoint
    fails loudly here.
    """

    def __init__(
        self,
        peak_matches: list[list[dict]] | None = None,
        *,
        signal_to_noise: bool = True,
        segments: Any = OMITTED,
        extra: dict[str, Any] | None = None,
    ):
        self.peak_matches = PEAK_MATCHES if peak_matches is None else peak_matches
        #: A list is a stitched sample, None one that is not, and OMITTED a
        #: server that predates the segments.
        self.segments = segments
        #: Keys a newer server might add to ``data``.
        self.extra = extra or {}
        #: Samples served as not stitched, whatever ``segments`` says.
        self.unstitched: set[str] = set()
        #: Warn each peak read differently, as reads short by different
        #: numbers of peaks do - which is when ``pd.concat`` drops ``attrs``.
        self.warn_each = False
        self.reads = 0
        #: False models a file that stores no noise estimate: the endpoint sends
        #: the key with a null rather than omitting it.
        self.signal_to_noise = signal_to_noise
        self.paths: list[str] = []

    def http_get(self, url, path, access_token, params=None, **kwargs):
        self.paths.append(path)
        if path == "ionization_mechanisms":
            return _FakeResponse(
                {
                    "status": "success",
                    "message": "Retrieved 1 mechanism",
                    "results": 1,
                    "data": [
                        {
                            "ionization_mechanism_id": "mech-1",
                            "ionization_mechanism": "[M+H]+",
                        }
                    ],
                }
            )
        sample = re.fullmatch(r"samples/([^/]+)/peaks", path)
        if sample is None:
            raise AssertionError(f"unexpected request to {path!r}")
        stitched = sample.group(1) not in self.unstitched

        params = dict(params or {})
        n = len(self.peak_matches)
        data = {
            "peak_id": [f"p{i + 1}" for i in range(n)],
            "mz": [100.0 + i for i in range(n)],
            "area": [1000.0 * (i + 1) for i in range(n)],
            "height": [500.0 * (i + 1) for i in range(n)],
            "sparsity": [0.0] * n,
            "signal_to_noise": (
                [10.0 * (i + 1) for i in range(n)] if self.signal_to_noise else None
            ),
            "match": self.peak_matches,
        }
        if self.segments is not OMITTED:
            segments = self.segments if stitched else None
            data["segment"] = (
                [i % len(segments) for i in range(n)] if segments else None
            )
            data["segments"] = segments
        data.update(self.extra)
        if params.get("matches") == "false":
            data.pop("match")
        body = {
            "status": "success",
            "message": f"Retrieved {n} peaks",
            "results": n,
            "data": data,
        }
        if self.warn_each:
            self.reads += 1
            body["warnings"] = [f"read {self.reads}"]
        return _FakeResponse(body)


class _StubClient:
    """The attributes ``BaseResource`` and ``get_peaks`` read off the client."""

    url = "http://testserver"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}
        self.samples = SamplesResource(self)

    @property
    def ionization(self) -> IonizationResource:
        return IonizationResource(self)


def _serve(
    monkeypatch, peak_matches=None, **kwargs
) -> tuple[SamplesResource, FakeServer]:
    fake = FakeServer(peak_matches=peak_matches, **kwargs)
    monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
    return _StubClient().samples, fake


@pytest.fixture()
def served(monkeypatch):
    return _serve(monkeypatch)


@pytest.fixture()
def resource(served):
    return served[0]


def _row(peaks: pd.DataFrame, peak_id: str) -> pd.Series:
    return peaks[peaks["peak_id"] == peak_id].iloc[0]


class TestSignalToNoise:
    """The per-peak noise estimate, for an engine scoring off this read."""

    def test_the_noise_estimate_rides_along_per_peak(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        assert _row(peaks, "p1")["signal_to_noise"] == 10.0
        assert _row(peaks, "p2")["signal_to_noise"] == 20.0

    def test_a_file_that_stores_none_says_so_rather_than_zero(self, monkeypatch):
        # An absent estimate and a peak at the noise floor are different facts,
        # and a scorer told the second one charges an isotopologue for being
        # invisible in a file that never measured whether it was.
        resource, _ = _serve(monkeypatch, signal_to_noise=False)

        peaks = resource.get_peaks(SAMPLE_ID)

        assert peaks["signal_to_noise"].isna().all()

    def test_a_multi_matched_peak_carries_it_on_every_row(self, resource):
        # The frame is exploded to one row per match; a per-peak column has to
        # survive the explode or a scorer reading it per row sees a NaN.
        peaks = resource.get_peaks(SAMPLE_ID)

        assert peaks.loc[peaks["peak_id"] == "p1", "signal_to_noise"].notna().all()


class TestTargetCollectionNames:
    def test_collection_names_arrive_alongside_ids(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        row = _row(peaks, "p1")
        assert row["target_collection_ids"] == ["tc-1", "tc-2"]
        assert row["target_collection_names"] == ["PFAS screen", "Reagent ions"]

        row = _row(peaks, "p2")
        assert row["target_collection_ids"] == ["tc-2"]
        assert row["target_collection_names"] == ["Reagent ions"]

    def test_names_column_sits_next_to_the_ids_column(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        cols = list(peaks.columns)
        assert (
            cols.index("target_collection_names")
            == cols.index("target_collection_ids") + 1
        )

    def test_unmatched_peak_has_no_collection_names(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        row = _row(peaks, "p3")
        assert row["target_collection_ids"] is None
        assert row["target_collection_names"] is None

    def test_matches_disabled_omits_the_column(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID, matches=False)

        assert "target_collection_names" not in peaks.columns

    def test_names_come_from_the_peaks_payload_only(self, resource, served):
        # The target collection listing is not reachable with a bearer token
        # (it is not declared token_access), so get_peaks must never call it -
        # resolving IDs client-side is what broke this feature before.
        resource.get_peaks(SAMPLE_ID)

        assert served[1].paths == [
            f"samples/{SAMPLE_ID}/peaks",
            "ionization_mechanisms",
        ]

    def test_older_server_leaves_the_column_none(self, monkeypatch):
        # A server that predates the names sends only the IDs. The column must
        # still exist, so the frame's schema does not depend on the server.
        resource, _ = _serve(monkeypatch, peak_matches=PEAK_MATCHES_WITHOUT_NAMES)

        peaks = resource.get_peaks(SAMPLE_ID)

        assert "target_collection_names" in peaks.columns
        assert peaks["target_collection_names"].isna().all()
        # The rest of the match flattening is untouched.
        assert _row(peaks, "p1")["target_collection_ids"] == ["tc-1", "tc-2"]
        assert _row(peaks, "p1")["target_compound_name"] == "Glucose"
        assert _row(peaks, "p1")["ionization_mechanism"] == "[M+H]+"

    def test_nothing_matched_keeps_the_column(self, monkeypatch):
        resource, _ = _serve(monkeypatch, peak_matches=[[], [], []])

        peaks = resource.get_peaks(SAMPLE_ID)

        assert "target_collection_names" in peaks.columns
        assert peaks["target_collection_names"].isna().all()


class TestSegments:
    """A stitched sample's listing names its segments beside the peaks: a
    list as long as the scan streams, not the peaks, which the frame must not
    take for a column."""

    def test_a_stitched_sample_reads_one_row_per_match(self, monkeypatch):
        resource, _ = _serve(monkeypatch, segments=SEGMENTS)

        peaks = resource.get_peaks(SAMPLE_ID)

        # p1 and p2 matched once each, p3 not at all
        assert peaks["peak_id"].tolist() == ["p1", "p2", "p3"]
        assert peaks["segment"].tolist() == [0, 1, 2]
        assert "segments" not in peaks.columns
        assert peaks.attrs["segments"] == SEGMENTS
        assert peaks.attrs["warnings"] == []
        assert _row(peaks, "p1")["target_compound_name"] == "Glucose"

    def test_a_multi_matched_peak_keeps_its_segment_on_every_row(self, monkeypatch):
        two = [_match(["tc-1"], ["A"]), _match(["tc-2"], ["B"], isotope="iso-2")]
        resource, _ = _serve(monkeypatch, peak_matches=[two, []], segments=SEGMENTS)

        peaks = resource.get_peaks(SAMPLE_ID)

        assert peaks["peak_id"].tolist() == ["p1", "p1", "p2"]
        assert peaks["segment"].tolist() == [0, 0, 1]
        assert peaks.attrs["segments"] == SEGMENTS

    def test_a_stitched_sample_reads_without_matches(self, monkeypatch):
        resource, _ = _serve(monkeypatch, segments=SEGMENTS)

        peaks = resource.get_peaks(SAMPLE_ID, matches=False)

        assert peaks["peak_id"].tolist() == ["p1", "p2", "p3"]
        assert peaks["segment"].tolist() == [0, 1, 2]
        assert peaks.attrs["segments"] == SEGMENTS

    def test_as_many_segments_as_peaks_are_still_not_a_column(self, monkeypatch):
        # A listing whose length happens to be the peaks' is no per-peak list
        resource, _ = _serve(monkeypatch, segments=SEGMENTS[:3])

        peaks = resource.get_peaks(SAMPLE_ID, matches=False)

        assert "segments" not in peaks.columns
        assert peaks.attrs["segments"] == SEGMENTS[:3]

    def test_a_stitched_sample_with_no_peaks_is_an_empty_frame(self, monkeypatch):
        resource, _ = _serve(monkeypatch, peak_matches=[], segments=SEGMENTS)

        peaks = resource.get_peaks(SAMPLE_ID)

        assert peaks is not None and peaks.empty
        assert "segment" in peaks.columns
        assert peaks.attrs["segments"] == SEGMENTS

    def test_an_unstitched_sample_reads_as_before(self, monkeypatch):
        # Both serve through the one seam, so one after the other
        old = _serve(monkeypatch)[0].get_peaks(SAMPLE_ID)
        resource, _ = _serve(monkeypatch, segments=None)

        peaks = resource.get_peaks(SAMPLE_ID)

        assert peaks.attrs["segments"] is None
        assert peaks["segment"].isna().all()
        assert "segments" not in peaks.columns
        pd.testing.assert_frame_equal(peaks.drop(columns=["segment"]), old)

    def test_a_server_without_segments_reads_as_before(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        assert peaks.attrs["segments"] is None
        assert "segment" not in peaks.columns
        assert "segments" not in peaks.columns
        assert peaks["peak_id"].tolist() == ["p1", "p2", "p3"]


class TestTheLoaders:
    """The loaders read through ``get_peaks`` and stack what it returns, so a
    stitched sample broke them too. They carry each sample's segments forward
    the way they carry the warnings."""

    @pytest.fixture
    def two_samples(self, monkeypatch):
        rows = pd.DataFrame(
            {"sample_item_id": [SAMPLE_ID, "s2"], "sample_item_name": ["A", "B"]}
        )
        monkeypatch.setattr(
            _loaders,
            "_collect_sample_tasks",
            lambda *a, **k: ([(row, "Batch") for _, row in rows.iterrows()], "ds-1"),
        )

    def test_load_peaks_keeps_each_samples_segments(self, monkeypatch, two_samples):
        resource, server = _serve(monkeypatch, segments=SEGMENTS)
        server.unstitched.add("s2")

        peaks = _loaders.load_peaks(resource._client, "My Dataset", max_workers=1)

        assert len(peaks) == 6
        assert peaks.attrs["segments"] == {SAMPLE_ID: SEGMENTS, "s2": None}
        assert peaks.attrs["warnings"] == []

    def test_load_peaks_by_stage_keeps_the_samples_segments(self, monkeypatch):
        resource, _ = _serve(monkeypatch, segments=SEGMENTS)
        client = resource._client
        client._cache["samples:ds-1"] = pd.DataFrame(
            {"sample_item_id": [SAMPLE_ID], "sample_item_name": ["A"]}
        )

        peaks = _loaders.load_peaks_by_stage(
            client, SAMPLE_ID, [(0, 30, "blank"), (30, 60, "sample")]
        )

        assert peaks["stage_name"].value_counts().to_dict() == {
            "blank": 3,
            "sample": 3,
        }
        assert peaks.attrs["segments"] == SEGMENTS

    def test_stages_that_warn_differently_keep_the_samples_segments(self, monkeypatch):
        resource, server = _serve(monkeypatch, segments=SEGMENTS)
        server.warn_each = True
        client = resource._client
        client._cache["samples:ds-1"] = pd.DataFrame(
            {"sample_item_id": [SAMPLE_ID], "sample_item_name": ["A"]}
        )

        peaks = _loaders.load_peaks_by_stage(
            client, SAMPLE_ID, [(0, 30), (30, 60)], max_workers=1
        )

        assert sorted(peaks.attrs["warnings"]) == ["read 1", "read 2"]
        assert peaks.attrs["segments"] == SEGMENTS

    def test_load_peak_timeseries_reads_a_stitched_sample(
        self, monkeypatch, two_samples
    ):
        # p1 and p2 match Glucose. The series are canned: what is under test
        # is the peak read that finds them, which run_concurrent would swallow
        # into a None if it raised.
        resource, server = _serve(monkeypatch, segments=SEGMENTS)
        server.unstitched.add("s2")
        series: list[tuple[str, str]] = []

        def get_peak_timeseries(sample_id, peak_id=None, **kwargs):
            series.append((sample_id, peak_id))
            return pd.DataFrame(
                {"peak_id": peak_id, "mz": 100.0, "time": [0.0, 1.0], "height": 1.0}
            )

        monkeypatch.setattr(resource, "get_peak_timeseries", get_peak_timeseries)

        ts = _loaders.load_peak_timeseries(
            resource._client, "My Dataset", compound="Glucose", max_workers=1
        )

        assert sorted(series) == [
            ("s2", "p1"),
            ("s2", "p2"),
            (SAMPLE_ID, "p1"),
            (SAMPLE_ID, "p2"),
        ]
        assert len(ts) == 8
        assert set(ts["target_compound_name"]) == {"Glucose"}


class TestWhatIsNotPerPeak:
    """What a newer server adds beside the per-peak lists goes to ``attrs``,
    so the next addition cannot break the read the way the segments did."""

    def test_a_list_of_another_length_goes_to_attrs(self, monkeypatch):
        resource, _ = _serve(monkeypatch, extra={"streams": ["a", "b"]})

        peaks = resource.get_peaks(SAMPLE_ID)

        assert "streams" not in peaks.columns
        assert peaks.attrs["streams"] == ["a", "b"]
        assert len(peaks) == 3

    def test_a_scalar_or_a_dict_goes_to_attrs(self, monkeypatch):
        extra = {"unit": "counts/s", "summary": {"total": 3}}
        resource, _ = _serve(monkeypatch, extra=extra)

        peaks = resource.get_peaks(SAMPLE_ID)

        assert "unit" not in peaks.columns and "summary" not in peaks.columns
        assert peaks.attrs["unit"] == "counts/s"
        assert peaks.attrs["summary"] == {"total": 3}

    def test_a_per_peak_list_stays_a_column(self, monkeypatch):
        resource, _ = _serve(monkeypatch, extra={"fwhm": [0.1, 0.2, 0.3]})

        peaks = resource.get_peaks(SAMPLE_ID)

        assert _row(peaks, "p2")["fwhm"] == 0.2
        assert "fwhm" not in peaks.attrs

    def test_it_cannot_overwrite_the_warnings(self, monkeypatch):
        resource, _ = _serve(monkeypatch, extra={"warnings": "not a list"})

        peaks = resource.get_peaks(SAMPLE_ID)

        assert peaks.attrs["warnings"] == []

    def test_it_is_logged_at_debug(self, monkeypatch):
        from loguru import logger

        records: list[tuple[str, str]] = []
        handler = logger.add(
            lambda m: records.append((m.record["level"].name, m.record["message"])),
            level="DEBUG",
            filter="mascope_sdk",
        )
        try:
            resource, _ = _serve(monkeypatch, segments=SEGMENTS, extra={"x": 1})
            resource.get_peaks(SAMPLE_ID)
        finally:
            logger.remove(handler)

        assert ("DEBUG", "Peak listing: 'x' is not per peak, kept in attrs") in records
