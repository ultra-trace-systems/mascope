"""
Hermetic unit tests for ``SampleFilesResource.get_scans``: what it asks the
server for, and the frame it makes of the answer. They stub the resource's GET
seam and need no running stack.

The scan table is keyed by scan number on the wire and its columns are the
instrument's own trailer labels, so the tests pin the two things a script
leans on: a row per scan with its number in a column, and the filters reaching
the request exactly as given - a label carries a colon and spaces, and must
not be rewritten on the way.
"""

from typing import Any

import pandas as pd
import pytest
from loguru import logger

from mascope_sdk.resources.sample_files import SampleFilesResource


FILE_ID = "file-1"

#: The table as the route sends it: rows under their scan number, as text.
SCAN_TABLE = {
    "num_of_scans": 3,
    "stats_per_scan": {
        "1": {
            "ScanNumber": 1,
            "StartTime": 0.01,
            "MsType": "Ms",
            "FT Resolution:": 120000,
            "Conversion Parameter B:": 67848187.2,
        },
        "2": {
            "ScanNumber": 2,
            "StartTime": 0.05,
            "MsType": "Ms",
            "FT Resolution:": 120000,
            "Conversion Parameter B:": 67848188.4,
        },
        "3": {
            "ScanNumber": 3,
            "StartTime": 0.09,
            "MsType": "Ms2",
            "FT Resolution:": 15000,
            "Conversion Parameter B:": None,
        },
    },
    "stats_per_file": {"Value": {"Model": "Orbitrap", "Name": "Orbitrap"}},
}


@pytest.fixture
def sample_files(monkeypatch) -> SampleFilesResource:
    """A resource that records its GETs in ``requests`` and answers
    ``SCAN_TABLE``, or whatever a test puts in ``answer``."""
    resource = SampleFilesResource(client=None)
    resource.requests = []
    resource.answer = SCAN_TABLE

    def fake_get(path: str, params: dict[str, Any] | None = None, **kwargs):
        resource.requests.append((path, params or {}))
        return resource.answer

    monkeypatch.setattr(resource, "_get", fake_get)
    return resource


@pytest.fixture
def logged():
    """What the SDK logs at WARNING and above."""
    records: list[str] = []
    handler = logger.add(
        lambda message: records.append(message.record["message"]),
        level="WARNING",
        filter="mascope_sdk",
    )
    yield records
    logger.remove(handler)


def test_the_table_is_a_row_per_scan_in_scan_order(sample_files):
    scans = sample_files.get_scans(FILE_ID)

    assert isinstance(scans, pd.DataFrame)
    assert scans["ScanNumber"].tolist() == [1, 2, 3]
    assert list(scans.columns) == [
        "ScanNumber",
        "StartTime",
        "MsType",
        "FT Resolution:",
        "Conversion Parameter B:",
    ]
    assert scans["FT Resolution:"].tolist() == [120000, 120000, 15000]
    # A value the reader left unset is missing, not zero.
    assert pd.isna(scans["Conversion Parameter B:"].iloc[2])


def test_the_files_own_statistics_ride_on_the_frame(sample_files):
    scans = sample_files.get_scans(FILE_ID)

    assert scans.attrs["num_of_scans"] == 3
    assert scans.attrs["stats_per_file"] == {"Model": "Orbitrap", "Name": "Orbitrap"}


def test_nothing_is_asked_for_but_the_file_by_default(sample_files):
    sample_files.get_scans(FILE_ID)

    assert sample_files.requests == [("sample/files/file-1/scans", {})]


def test_every_filter_reaches_the_request_as_given(sample_files):
    sample_files.get_scans(
        FILE_ID,
        fields=["Conversion Parameter B:", "FT Resolution:"],
        first_scan=2,
        last_scan=3,
        t_min=0.0,
        t_max=12.5,
        polarity="+",
    )

    [(_, params)] = sample_files.requests
    assert params == {
        # A list, which the HTTP layer sends as a repeated parameter.
        "fields": ["Conversion Parameter B:", "FT Resolution:"],
        "first_scan": 2,
        "last_scan": 3,
        # 0.0 is a bound, not a filter left out.
        "t_min": 0.0,
        "t_max": 12.5,
        "polarity": "+",
    }


def test_one_field_given_as_text_is_one_field(sample_files):
    # A bare string is iterable, and would otherwise be asked for letter by
    # letter.
    sample_files.get_scans(FILE_ID, fields="FT Resolution:")

    [(_, params)] = sample_files.requests
    assert params == {"fields": ["FT Resolution:"]}


def test_fields_may_come_from_any_iterable(sample_files):
    sample_files.get_scans(FILE_ID, fields=(name for name in ["MsType", "TIC"]))

    [(_, params)] = sample_files.requests
    assert params == {"fields": ["MsType", "TIC"]}


def test_no_fields_asks_for_the_scan_numbers_alone(sample_files):
    # An empty list is a choice - "no column but the scan number" - but a
    # query string cannot carry an empty list: the parameter would simply be
    # absent, and the server would send every column.
    sample_files.get_scans(FILE_ID, fields=[])

    [(_, params)] = sample_files.requests
    assert params == {"fields": ["ScanNumber"]}


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {},
        {"num_of_scans": 3, "stats_per_scan": {}, "stats_per_file": {"Value": {}}},
    ],
    ids=["no-data", "empty", "no-scan-matches"],
)
def test_no_matching_scan_is_none(sample_files, answer):
    sample_files.answer = answer

    assert sample_files.get_scans(FILE_ID, first_scan=99) is None


def test_a_field_the_file_does_not_use_is_warned_about(sample_files, logged):
    # The server leaves such a name out: trailer labels differ between
    # instrument models. The usual cause is a label typed without its colon,
    # and a column that is silently absent would only surface as a KeyError
    # somewhere downstream.
    sample_files.answer = {
        **SCAN_TABLE,
        "stats_per_scan": {
            scan: {"ScanNumber": row["ScanNumber"], "MsType": row["MsType"]}
            for scan, row in SCAN_TABLE["stats_per_scan"].items()
        },
    }

    scans = sample_files.get_scans(FILE_ID, fields=["MsType", "FT Resolution"])

    assert list(scans.columns) == ["ScanNumber", "MsType"]
    [warning] = logged
    assert "'FT Resolution'" in warning
    assert "'MsType'" not in warning


def test_fields_the_file_has_raise_no_warning(sample_files, logged):
    sample_files.get_scans(FILE_ID, fields=["MsType", "FT Resolution:"])

    assert logged == []
