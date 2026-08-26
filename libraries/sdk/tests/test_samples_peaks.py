"""
Hermetic unit tests for ``SamplesResource.get_peaks`` match flattening,
in particular the resolution of ``target_collection_ids`` to names. These
mock the HTTP layer (like ``test_peak_assignments.py``) and do not need a
running stack.
"""

from typing import Any

import pandas as pd
import pytest

from mascope_sdk import AuthenticationError
from mascope_sdk.resources.ionization import IonizationResource
from mascope_sdk.resources.samples import SamplesResource


SAMPLE_ID = "sample-1"

COLLECTIONS = [
    {
        "target_collection_id": "tc-1",
        "target_collection_name": "PFAS screen",
        "target_collection_type": "user",
    },
    {
        "target_collection_id": "tc-2",
        "target_collection_name": "Reagent ions",
        "target_collection_type": "global",
    },
]


def _match(collection_ids: list[str], isotope: str = "iso-1") -> dict[str, Any]:
    """A match dict carrying every key ``get_peaks`` flattens."""
    return {
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


#: p1 matches two known collections, p2 one that is not in the listing,
#: p3 is unmatched.
PEAK_MATCHES = [
    [_match(["tc-1", "tc-2"])],
    [_match(["tc-missing"], isotope="iso-2")],
    [],
]


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeServer:
    """A canned peaks API behind the ``http_get`` seam.

    Serves the peaks payload (honouring ``matches=false``), the ionization
    mechanism listing and the target collection listing - the three calls
    ``get_peaks`` makes.
    """

    def __init__(
        self,
        peak_matches: list[list[dict]] | None = None,
        collections: list[dict] | None = None,
        collections_error: Exception | None = None,
    ):
        self.peak_matches = PEAK_MATCHES if peak_matches is None else peak_matches
        self.collections = COLLECTIONS if collections is None else collections
        self.collections_error = collections_error
        self.collection_calls = 0

    def http_get(self, url, path, access_token, params=None, **kwargs):
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
        if path == "target/collections":
            self.collection_calls += 1
            if self.collections_error is not None:
                raise self.collections_error
            return _FakeResponse(
                {
                    "status": "success",
                    "message": "Target collections retrieved successfully.",
                    "results": len(self.collections),
                    "data": self.collections,
                }
            )

        # The peaks endpoint
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


@pytest.fixture()
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
    return fake


@pytest.fixture()
def resource(server):
    return SamplesResource(_StubClient())


def _row(peaks: pd.DataFrame, peak_id: str) -> pd.Series:
    return peaks[peaks["peak_id"] == peak_id].iloc[0]


class TestTargetCollectionNames:
    def test_collection_names_resolve_alongside_ids(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        row = _row(peaks, "p1")
        assert row["target_collection_ids"] == ["tc-1", "tc-2"]
        assert row["target_collection_names"] == ["PFAS screen", "Reagent ions"]

    def test_names_column_sits_next_to_the_ids_column(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        cols = list(peaks.columns)
        assert (
            cols.index("target_collection_names")
            == cols.index("target_collection_ids") + 1
        )

    def test_unknown_collection_id_resolves_to_none(self, resource):
        # A collection that is gone, or invisible to the token, must not
        # leak its raw ID into a name column.
        peaks = resource.get_peaks(SAMPLE_ID)

        assert _row(peaks, "p2")["target_collection_names"] == [None]

    def test_unmatched_peak_has_no_collection_names(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID)

        row = _row(peaks, "p3")
        assert row["target_collection_ids"] is None
        assert row["target_collection_names"] is None

    def test_collection_listing_is_fetched_once_per_client(self, resource, server):
        resource.get_peaks(SAMPLE_ID)
        resource.get_peaks(SAMPLE_ID)

        assert server.collection_calls == 1

    def test_no_collection_request_when_nothing_matched(self, monkeypatch):
        fake = FakeServer(peak_matches=[[], [], []])
        monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
        resource = SamplesResource(_StubClient())

        peaks = resource.get_peaks(SAMPLE_ID)

        assert fake.collection_calls == 0
        # The schema must not depend on whether the sample matched anything.
        assert "target_collection_names" in peaks.columns
        assert peaks["target_collection_names"].isna().all()

    def test_matches_disabled_omits_the_column(self, resource):
        peaks = resource.get_peaks(SAMPLE_ID, matches=False)

        assert "target_collection_names" not in peaks.columns

    def test_collection_listing_failure_does_not_break_get_peaks(self, monkeypatch):
        fake = FakeServer(
            collections_error=AuthenticationError(
                message="Not authorized", status_code=403
            )
        )
        monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
        resource = SamplesResource(_StubClient())

        peaks = resource.get_peaks(SAMPLE_ID)

        assert _row(peaks, "p1")["target_collection_names"] == [None, None]
        # The rest of the match flattening is untouched.
        assert _row(peaks, "p1")["target_compound_name"] == "Glucose"
        assert _row(peaks, "p1")["ionization_mechanism"] == "+H+"
