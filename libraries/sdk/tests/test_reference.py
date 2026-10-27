"""
Hermetic unit tests for ``ReferenceResource``: what it asks the server for,
and the frames it makes of the answers. They stub the resource's GET seams and
need no running stack; ``test_contract.py`` reads the same two routes on the
demo stack.
"""

from typing import Any

import pandas as pd
import pytest

from mascope_sdk.exceptions import NotFoundError
from mascope_sdk.resources import reference
from mascope_sdk.resources.reference import ReferenceResource


SOURCE = {
    "name": "nitroaromatics",
    "version": "2026.09",
    "label": "Nitroaromatics",
    "references": [
        {
            "citation": "A. Author, A paper. A Journal 1 (2020) 1-2.",
            "doi": "10.0000/paper",
            "isbn": None,
            "note": None,
        }
    ],
    "license": "CC-BY-4.0",
    "polarity": "negative",
    "allow_radicals": False,
    "known_window": {"elements": None, "max_carbon": None, "max_mass": None},
    "record_count": 5,
    "ingested_at": "2026-10-26T12:18:00.286519+00:00",
    "admitted": True,
}


def _compound(index: int) -> dict[str, Any]:
    return {
        "name": f"compound {index}",
        "formula": f"C{index}H4",
        "monoisotopic_mass": 100.0 + index,
        "inchikey": None,
        "source_native_id": str(index),
        "reference": "10.0000/example",
        "tags": ["tag"],
    }


@pytest.fixture
def resource(monkeypatch) -> ReferenceResource:
    """A resource whose GETs answer from ``sources`` and ``compounds``.

    The compounds route pages as the server does: ``page`` and ``limit``
    slice the version's rows, and ``total`` counts them all. Each request is
    recorded in ``requests``.
    """
    resource = ReferenceResource(client=None)
    resource.requests = []
    resource.sources_answer = [SOURCE]
    resource.compounds_answer = [_compound(i) for i in range(5)]

    def fake_get(path: str, params: dict[str, Any] | None = None, **kwargs):
        resource.requests.append((path, params or {}))
        return resource.sources_answer

    def fake_envelope(path: str, params: dict[str, Any] | None = None):
        params = params or {}
        resource.requests.append((path, params))
        if resource.compounds_answer is None:
            raise NotFoundError("Reference source x has no active version", 404)
        start = params["page"] * params["limit"]
        return {
            "name": "nitroaromatics",
            "version": params.get("version", "2026.09"),
            "data": resource.compounds_answer[start : start + params["limit"]],
            "total": len(resource.compounds_answer),
        }

    monkeypatch.setattr(resource, "_get", fake_get)
    monkeypatch.setattr(resource, "_get_envelope", fake_envelope)
    return resource


class TestSources:
    def test_reads_the_sources_route(self, resource):
        resource.sources()

        assert resource.requests == [("reference/sources", {})]

    def test_one_row_per_source_with_the_admitted_flag(self, resource):
        frame = resource.sources()

        assert list(frame.columns) == reference._SOURCE_COLUMNS
        assert len(frame) == 1
        assert bool(frame.iloc[0]["admitted"]) is True
        assert frame.iloc[0]["known_window"] == SOURCE["known_window"]

    def test_each_source_cites_its_publications(self, resource):
        frame = resource.sources()

        assert frame.iloc[0]["references"] == SOURCE["references"]

    def test_the_load_date_is_a_utc_datetime(self, resource):
        frame = resource.sources()

        assert isinstance(frame["ingested_at"].dtype, pd.DatetimeTZDtype)
        assert str(frame["ingested_at"].dt.tz) == "UTC"
        assert frame.iloc[0]["ingested_at"] == pd.Timestamp(SOURCE["ingested_at"])

    def test_load_dates_of_mixed_shapes_and_offsets(self, resource):
        # The server writes isoformat(), which leaves out the fraction when
        # the microseconds are 0; a later row shaped unlike the first must
        # parse too, and a non-UTC offset is converted to UTC.
        resource.sources_answer = [
            SOURCE,
            {**SOURCE, "name": "other", "ingested_at": "2026-10-26T15:18:00+03:00"},
        ]

        frame = resource.sources()

        assert str(frame["ingested_at"].dt.tz) == "UTC"
        assert frame.iloc[1]["ingested_at"] == pd.Timestamp("2026-10-26T12:18:00Z")
        assert frame.iloc[1]["ingested_at"].hour == 12

    def test_no_sources_is_an_empty_frame_with_the_columns(self, resource):
        resource.sources_answer = []

        frame = resource.sources()

        assert frame.empty
        assert list(frame.columns) == reference._SOURCE_COLUMNS
        assert "references" in frame.columns


class TestCompounds:
    def test_reads_every_page(self, resource, monkeypatch):
        monkeypatch.setattr(reference, "COMPOUNDS_PAGE_LIMIT", 2)

        frame = resource.compounds("nitroaromatics")

        assert list(frame["source_native_id"]) == ["0", "1", "2", "3", "4"]
        assert [params["page"] for _, params in resource.requests] == [0, 1, 2]
        assert {params["limit"] for _, params in resource.requests} == {2}

    def test_stops_at_the_total_without_an_empty_page(self, resource, monkeypatch):
        monkeypatch.setattr(reference, "COMPOUNDS_PAGE_LIMIT", 5)

        resource.compounds("nitroaromatics")

        assert len(resource.requests) == 1

    def test_an_empty_page_ends_the_read(self, resource, monkeypatch):
        # A version reloaded with fewer rows mid-read: total still counts
        # the old rows, and a page past the new ones comes back empty.
        monkeypatch.setattr(reference, "COMPOUNDS_PAGE_LIMIT", 2)
        fake_envelope = resource._get_envelope

        def overcounting(path, params=None):
            assert params["page"] < 10, "read on past an empty page"
            return {**fake_envelope(path, params), "total": 10}

        monkeypatch.setattr(resource, "_get_envelope", overcounting)

        frame = resource.compounds("nitroaromatics")

        assert len(frame) == 5
        assert [params["page"] for _, params in resource.requests] == [0, 1, 2, 3]

    def test_asks_for_the_largest_page(self, resource):
        resource.compounds("nitroaromatics")

        assert resource.requests[0][1]["limit"] == 1000

    def test_the_active_version_sends_no_version(self, resource):
        frame = resource.compounds("nitroaromatics")

        assert "version" not in resource.requests[0][1]
        assert frame.attrs == {"source": "nitroaromatics", "version": "2026.09"}

    def test_the_later_pages_ask_for_page_zeros_version(self, resource, monkeypatch):
        # A load that becomes active mid-read must not mix into the frame:
        # page 0 says which version is active, and the rest ask for it.
        monkeypatch.setattr(reference, "COMPOUNDS_PAGE_LIMIT", 2)
        fake_envelope = resource._get_envelope

        def reloading(path, params=None):
            # Unpinned, every page after the first would see the new load.
            if params["page"] > 0 and "version" not in params:
                params = {**params, "version": "2026.10"}
            return fake_envelope(path, params)

        monkeypatch.setattr(resource, "_get_envelope", reloading)

        frame = resource.compounds("nitroaromatics")

        assert "version" not in resource.requests[0][1]
        assert [params["version"] for _, params in resource.requests[1:]] == [
            "2026.09",
            "2026.09",
        ]
        assert frame.attrs["version"] == "2026.09"

    def test_a_named_version_is_asked_for(self, resource):
        frame = resource.compounds("nitroaromatics", version="2025.01")

        assert all(params["version"] == "2025.01" for _, params in resource.requests)
        assert frame.attrs["version"] == "2025.01"

    def test_the_source_name_is_quoted_into_the_path(self, resource):
        resource.compounds("my list/v2")

        assert resource.requests[0][0] == "reference/sources/my%20list%2Fv2/compounds"

    def test_the_columns_are_the_routes(self, resource):
        frame = resource.compounds("nitroaromatics")

        assert list(frame.columns) == reference._COMPOUND_COLUMNS

    def test_an_empty_version_is_an_empty_frame_with_the_columns(self, resource):
        resource.compounds_answer = []

        frame = resource.compounds("nitroaromatics")

        assert frame.empty
        assert list(frame.columns) == reference._COMPOUND_COLUMNS

    def test_an_unknown_source_raises_not_found(self, resource):
        resource.compounds_answer = None

        with pytest.raises(NotFoundError):
            resource.compounds("no-such-list")


def test_the_client_exposes_the_resource():
    # The contract tests probe for mascope.reference and skip without it, so
    # the client's property is pinned here. No constructor: it would ask a
    # server for the workspaces.
    from mascope_sdk import MascopeClient

    client = MascopeClient.__new__(MascopeClient)
    client._reference = None

    assert isinstance(client.reference, ReferenceResource)
    assert client.reference is client.reference
