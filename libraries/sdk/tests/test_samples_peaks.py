"""
Hermetic unit tests for ``SamplesResource.get_peaks`` match flattening, in
particular the ``target_collection_names`` the peaks payload carries next to
``target_collection_ids``. These mock the HTTP layer (like
``test_peak_assignments.py``) and do not need a running stack.
"""

from typing import Any

import pandas as pd
import pytest

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

    def __init__(self, peak_matches: list[list[dict]] | None = None):
        self.peak_matches = PEAK_MATCHES if peak_matches is None else peak_matches
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
                            "ionization_mechanism": "+H+",
                        }
                    ],
                }
            )
        if path != f"samples/{SAMPLE_ID}/peaks":
            raise AssertionError(f"unexpected request to {path!r}")

        params = dict(params or {})
        n = len(self.peak_matches)
        data = {
            "peak_id": [f"p{i + 1}" for i in range(n)],
            "mz": [100.0 + i for i in range(n)],
            "area": [1000.0 * (i + 1) for i in range(n)],
            "height": [500.0 * (i + 1) for i in range(n)],
            "sparsity": [0.0] * n,
            "match": self.peak_matches,
        }
        if params.get("matches") == "false":
            data.pop("match")
        return _FakeResponse(
            {
                "status": "success",
                "message": f"Retrieved {n} peaks",
                "results": n,
                "data": data,
            }
        )


class _StubClient:
    """The attributes ``BaseResource`` and ``get_peaks`` read off the client."""

    url = "http://testserver"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}

    @property
    def ionization(self) -> IonizationResource:
        return IonizationResource(self)


def _serve(monkeypatch, peak_matches=None) -> tuple[SamplesResource, FakeServer]:
    fake = FakeServer(peak_matches=peak_matches)
    monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
    return SamplesResource(_StubClient()), fake


@pytest.fixture()
def served(monkeypatch):
    return _serve(monkeypatch)


@pytest.fixture()
def resource(served):
    return served[0]


def _row(peaks: pd.DataFrame, peak_id: str) -> pd.Series:
    return peaks[peaks["peak_id"] == peak_id].iloc[0]


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
        assert _row(peaks, "p1")["ionization_mechanism"] == "+H+"

    def test_nothing_matched_keeps_the_column(self, monkeypatch):
        resource, _ = _serve(monkeypatch, peak_matches=[[], [], []])

        peaks = resource.get_peaks(SAMPLE_ID)

        assert "target_collection_names" in peaks.columns
        assert peaks["target_collection_names"].isna().all()
