"""Scan-event records that carry a name are read at their own size (#2391).

A version-66 scan-event record is a 136-byte preamble and a 136-byte body
whose last word counts the UTF-16 code units of a name that follows it. A
targeted MS2 scan run from an inclusion list stores the entry's name there,
so the records of such a file are not all one size. OpenTFRaw 2.0.0 read them
all at one size inferred from the stream's length, and from the first named
record on decoded every scan's event from the wrong bytes. The pinned reader
walks the records by their counts (OpenTFRaw 3.1.0, Sigilweaver/OpenTFRaw#86),
which is what lets Mascope read these files without a decoder of its own.

No file with named records can be committed, so the tests give one to a
committed sample file: they write a name into one of its records, and move
what follows the scan-event stream by the name's length. The file is then the
same acquisition with records of two sizes, and the backend must read it as
it reads the original.
"""

import struct

import numpy as np
import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.run_header import _address_table


# Where a version 64+ run header holds the scan-event count, and where its
# 64-bit stream addresses run, the scan trailer's and the scan parameters'
# among them.
_RH_NTRAILER = 7376
_RH_ADDRESSES = range(7400, 7464, 8)
_RH_SCAN_TRAILER_ADDR = 7448
_RH_SCAN_PARAMS_ADDR = 7456
_RECORD_SIZE = 272
# Where in a record its name's length sits: the body's last word.
_NAME_LENGTH_AT = _RECORD_SIZE - 4

# An inclusion-list entry's name. An odd number of code units, so the stream's
# length leaves no size inferred from it that lands on a record boundary.
NAME = "C10H16O4 NO3-"


def _with_name(source: str, target, record: int, name: str = NAME) -> None:
    """Write ``source`` to ``target`` with ``name`` in its ``record``-th
    scan-event record."""
    data = bytearray(open(source, "rb").read())
    with open(source, "rb") as f:
        version, _data_addr, run_headers = _address_table(f)
    assert version == 66
    run_header = run_headers[0]
    (events,) = struct.unpack_from("<I", data, run_header + _RH_NTRAILER)
    (trailer,) = struct.unpack_from("<Q", data, run_header + _RH_SCAN_TRAILER_ADDR)
    (params,) = struct.unpack_from("<Q", data, run_header + _RH_SCAN_PARAMS_ADDR)
    # The stream runs from four bytes past the trailer address to the scan
    # parameters, one unnamed record per scan event.
    start = trailer + 4
    assert params - start == events * _RECORD_SIZE
    assert 0 <= record < events

    at = start + record * _RECORD_SIZE
    assert struct.unpack_from("<I", data, at + _NAME_LENGTH_AT)[0] == 0
    encoded = name.encode("utf-16-le")
    struct.pack_into("<I", data, at + _NAME_LENGTH_AT, len(encoded) // 2)
    end = at + _RECORD_SIZE
    data[end:end] = encoded
    for offset in _RH_ADDRESSES:
        (address,) = struct.unpack_from("<Q", data, run_header + offset)
        if end <= address < len(data) - len(encoded):
            struct.pack_into("<Q", data, run_header + offset, address + len(encoded))
    with open(target, "wb") as f:
        f.write(data)


def _read(path: str) -> dict:
    """What the OpenTFRaw backend reads from a file's scan events."""
    with OpenTFRawBackend(path) as backend:
        filters = backend.scan_filters()
        scans = [row["scan"] for row in filters]
        mzs, _intensities, _times = backend.profile_per_scan(ms_type=None)
        return {
            "polarities": backend.polarities(),
            "filters": filters,
            "statistics": backend.scan_statistics(ms_type=None),
            "profile_mz": dict(zip(scans, mzs)),
        }


@pytest.mark.parametrize("record", [0, 1], ids=["first", "second"])
@pytest.mark.parametrize(
    "source", [NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH], ids=["neg", "pos"]
)
def test_a_named_record_reads_as_the_file_without_it(tmp_path, source, record):
    """Every scan's event reads as it does without the name.

    The polarity, the filter (analyzer, MS order, scan range) and each
    scan's m/z axis all come from the scan's event. A reader that read the
    records at one size would decode the scan after the named record from
    the wrong bytes, and the backend would refuse the file for the analyzer
    that reading gives it.
    """
    named = tmp_path / "named.raw"
    _with_name(source, named, record)

    original, read = _read(source), _read(str(named))

    assert read["polarities"] == original["polarities"]
    assert read["filters"] == original["filters"]
    assert read["statistics"] == original["statistics"]
    assert read["profile_mz"].keys() == original["profile_mz"].keys()
    for scan, mz in original["profile_mz"].items():
        np.testing.assert_array_equal(read["profile_mz"][scan], mz)


def test_the_built_file_holds_records_of_two_sizes(tmp_path):
    """The name is in the stream, so the file is one a reader must walk.

    Without it the tests above would compare a file with itself.
    """
    named = tmp_path / "named.raw"
    _with_name(NEG_ORBI_FILE_PATH, named, 0)

    with open(named, "rb") as f:
        data = f.read()
        f.seek(0)
        _version, _data_addr, run_headers = _address_table(f)
    run_header = run_headers[0]
    (events,) = struct.unpack_from("<I", data, run_header + _RH_NTRAILER)
    (trailer,) = struct.unpack_from("<Q", data, run_header + _RH_SCAN_TRAILER_ADDR)
    (params,) = struct.unpack_from("<Q", data, run_header + _RH_SCAN_PARAMS_ADDR)
    sizes, at = [], trailer + 4
    for _ in range(events):
        (count,) = struct.unpack_from("<I", data, at + _NAME_LENGTH_AT)
        sizes.append(_RECORD_SIZE + 2 * count)
        at += sizes[-1]

    assert at == params
    assert sizes == [_RECORD_SIZE + 2 * len(NAME), _RECORD_SIZE]
    name_at = trailer + 4 + _RECORD_SIZE
    assert data[name_at : name_at + 2 * len(NAME)].decode("utf-16-le") == NAME
