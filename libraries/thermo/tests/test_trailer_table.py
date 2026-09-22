"""The per-scan trailer table (``ReaderBackend.scan_acquisition_settings``).

The table is each scan's trailer under the instrument's own labels, with one
label list for every scan. The committed sample files pin that on real data,
under each reader backend available. A scripted opentfraw reader drives the
scans no committed file has: one with no trailer record, and ones whose labels
differ.
"""

import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import OpenTFRawBackend, open_backend


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(POS_ORBI_FILE_PATH, id="pos"),
        pytest.param(NEG_ORBI_FILE_PATH, id="neg"),
    ],
)
def test_the_table_is_each_scans_trailer(backend, path):  # noqa: ARG001
    # Whichever backend reads the file, a scan's row is its own trailer, in the
    # instrument's order, so a scan can be looked up by a trailer label such
    # as "FT Resolution:" under either.
    with open_backend(path) as reader:
        table = reader.scan_acquisition_settings(ms_type=None)
        scans = reader.scan_indices(ms_type=None)
        trailers = {scan: reader.scan_trailer(scan) for scan in scans}

    labels = table["header_labels"]
    assert len(set(labels)) == len(labels)
    assert {"FT Resolution:", "AGC Target:", "Ion Injection Time (ms):"} <= set(labels)
    assert list(table["settings"]) == scans
    for scan, values in table["settings"].items():
        assert list(trailers[scan]) == labels
        assert values == list(trailers[scan].values())


class _ScriptedRaw:
    """The slice of ``opentfraw.RawFile`` the trailer table reads: one MS1
    scan per trailer record, where ``None`` is a scan with no record."""

    def __init__(self, trailers: list[dict | None]):
        self._trailers = trailers

    def iter_scans(self):
        for scan_number in range(1, len(self._trailers) + 1):
            yield {
                "scan_number": scan_number,
                "ms_level": 1,
                "polarity": "+",
                "retention_time": scan_number / 60,
                "total_ion_current": 1e6,
            }

    def scan_parameters(self, scan_number):
        return self._trailers[scan_number - 1]


def _table(trailers: list[dict | None]) -> dict:
    reader = OpenTFRawBackend("unused.raw")
    reader._raw = _ScriptedRaw(trailers)
    return reader.scan_acquisition_settings()


_TRAILER = {
    "Scan Event:": 1,
    "FT Resolution:": 120000,
    "=== Mass Calibration: ===": None,
}


def test_a_scan_without_a_trailer_gets_a_row_of_none():
    assert _table([_TRAILER, None, _TRAILER]) == {
        "header_labels": list(_TRAILER),
        "settings": {
            1: [1, 120000, None],
            2: [None, None, None],
            3: [1, 120000, None],
        },
    }


def test_the_labels_come_from_whichever_scan_has_a_trailer():
    table = _table([None, _TRAILER])
    assert table["header_labels"] == list(_TRAILER)
    assert table["settings"] == {1: [None, None, None], 2: [1, 120000, None]}


def test_rows_follow_the_labels_when_scans_carry_different_ones():
    # One label list for the table: a label only some scans carry is kept, in
    # the order first met, and each value lands under its own label whatever
    # its position in the scan's trailer.
    table = _table(
        [
            {"Scan Event:": 1, "FT Resolution:": 120000},
            {"FT Resolution:": 60000, "FAIMS CV:": -40.0, "Scan Event:": 2},
        ]
    )
    assert table == {
        "header_labels": ["Scan Event:", "FT Resolution:", "FAIMS CV:"],
        "settings": {1: [1, 120000, None], 2: [2, 60000, -40.0]},
    }


def test_a_file_without_trailers_has_an_empty_label_list():
    assert _table([None, None]) == {
        "header_labels": [],
        "settings": {1: [], 2: []},
    }
