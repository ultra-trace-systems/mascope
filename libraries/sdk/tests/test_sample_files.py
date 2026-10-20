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

import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from loguru import logger
from requests.exceptions import ChunkedEncodingError

from mascope_sdk.exceptions import MascopeConnectionError, NotFoundError
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


# ============= download_raw =============


RAW = bytes(range(256)) * 10


class _Response:
    """A streamed response as ``requests`` hands it over: headers, the body
    in pieces, and a close when the ``with`` ends."""

    url = "http://test/api/sample/files/file-1/raw"

    def __init__(self, chunks, headers):
        self.chunks = chunks
        self.headers = headers
        self.closed = False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


def _raw_response(
    body: bytes = RAW,
    disposition: str | None = 'attachment; filename="instr_2026.01.01.raw"',
    length: int | None = None,
    chunks: list | None = None,
) -> _Response:
    headers = {"Content-Length": str(len(body) if length is None else length)}
    if disposition is not None:
        headers["Content-Disposition"] = disposition
    return _Response(chunks or [body[:1000], body[1000:]], headers)


@pytest.fixture
def downloads(monkeypatch) -> SampleFilesResource:
    """A resource whose GET answers ``answer`` and records what it asked."""
    resource = SampleFilesResource(client=None)
    resource.requests = []
    resource.answer = _raw_response()

    def fake_get(path: str, params: dict[str, Any] | None = None, stream=False):
        resource.requests.append((path, stream))
        return resource.answer

    monkeypatch.setattr(resource, "_get", fake_get)
    return resource


def test_download_raw_streams_the_files_route(downloads, tmp_path):
    downloads.download_raw(FILE_ID, tmp_path / "out.raw")

    assert downloads.requests == [(f"sample/files/{FILE_ID}/raw", True)]
    assert downloads.answer.closed


def test_download_raw_writes_the_bytes_to_the_path_given(downloads, tmp_path):
    target = tmp_path / "out.raw"

    written = downloads.download_raw(FILE_ID, str(target))

    assert written == target
    assert target.read_bytes() == RAW
    assert list(tmp_path.iterdir()) == [target]


def test_download_raw_replaces_a_file_at_the_path(downloads, tmp_path):
    target = tmp_path / "out.raw"
    target.write_bytes(b"an older download")

    downloads.download_raw(FILE_ID, target)

    assert target.read_bytes() == RAW


def test_download_raw_into_a_directory_takes_the_upload_name(downloads, tmp_path):
    written = downloads.download_raw(FILE_ID, tmp_path)

    assert written == tmp_path / "instr_2026.01.01.raw"
    assert written.read_bytes() == RAW


@pytest.mark.parametrize("separator", sorted({os.sep, "/"}))
def test_download_raw_into_a_directory_not_made_yet_makes_it(
    downloads, tmp_path, separator
):
    directory = tmp_path / "downloads" / "today"

    written = downloads.download_raw(FILE_ID, f"{directory}{separator}")

    assert written == directory / "instr_2026.01.01.raw"
    assert written.read_bytes() == RAW
    assert list(directory.iterdir()) == [written]


def test_download_raw_as_the_readme_shows_it(downloads, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    written = downloads.download_raw(FILE_ID, "downloads/")

    assert written == Path("downloads") / "instr_2026.01.01.raw"
    assert (tmp_path / written).read_bytes() == RAW


def test_download_raw_refused_makes_no_directory(downloads, tmp_path, monkeypatch):
    def refuse(path, params=None, stream=False):
        raise NotFoundError("no source data file")

    monkeypatch.setattr(downloads, "_get", refuse)

    with pytest.raises(NotFoundError):
        downloads.download_raw(FILE_ID, f"{tmp_path / 'downloads'}/")

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("disposition", "name"),
    [
        pytest.param(
            "attachment; filename*=utf-8''m%C3%A4%C3%A4r%C3%A4.h5",
            "määrä.h5",
            id="rfc5987",
        ),
        # The name is the server's; it never chooses the directory.
        pytest.param('attachment; filename="../../escape.raw"', "escape.raw", id="up"),
        pytest.param('attachment; filename="/etc/escape.raw"', "escape.raw", id="abs"),
        pytest.param('attachment; filename="..\\\\escape.raw"', "escape.raw", id="win"),
        pytest.param('attachment; filename=".."', FILE_ID, id="dots"),
        pytest.param(None, FILE_ID, id="no-header"),
    ],
)
def test_download_raw_names_a_file_in_the_directory_and_nowhere_else(
    downloads, tmp_path, disposition, name
):
    downloads.answer = _raw_response(disposition=disposition)

    written = downloads.download_raw(FILE_ID, tmp_path)

    assert written == tmp_path / name
    assert [p.name for p in tmp_path.iterdir()] == [name]


def test_download_raw_cut_off_part_way_raises_and_leaves_nothing(downloads, tmp_path):
    downloads.answer = _raw_response(
        chunks=[RAW[:1000], ChunkedEncodingError("connection reset")]
    )
    target = tmp_path / "out.raw"

    with pytest.raises(MascopeConnectionError, match="after 1000 bytes"):
        downloads.download_raw(FILE_ID, target)

    assert list(tmp_path.iterdir()) == []


def test_download_raw_shorter_than_announced_raises_and_leaves_nothing(
    downloads, tmp_path
):
    downloads.answer = _raw_response(length=len(RAW) + 1)
    target = tmp_path / "out.raw"
    target.write_bytes(b"an older download")

    with pytest.raises(MascopeConnectionError, match=f"of {len(RAW) + 1} bytes"):
        downloads.download_raw(FILE_ID, target)

    # What was there is left as it was, and no partial file beside it.
    assert target.read_bytes() == b"an older download"
    assert list(tmp_path.iterdir()) == [target]


def test_download_raw_of_an_unknown_length_keeps_what_arrived(downloads, tmp_path):
    downloads.answer = _raw_response()
    del downloads.answer.headers["Content-Length"]

    written = downloads.download_raw(FILE_ID, tmp_path / "out.raw")

    assert written.read_bytes() == RAW
