"""A file whose scans read as an analyzer its instrument has not is refused.

OpenTFRaw derives a scan's analyzer, polarity, MS order and filter, and the
calibration of its profile, from the scan's event, and a file whose events it
reads from the wrong bytes gives noise in all of them (GitHub issue #2394). On
an instrument whose only analyzer is an Orbitrap, a scan read as any other is
the one part of that noise that cannot be data, so the OpenTFRaw backend
refuses the file, whatever made it misread.

The reader is a stand-in that answers ``scan_table()`` with the analyzers a
test gives it, and opens nothing: the backend's path names no file, which the
check for named scan-event records passes over.
"""

import weakref

import opentfraw
import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.thermo import MisreadScanEventsError, NoScansFoundError


# Models as OpenTFRaw names them.
ORBITRAP_ONLY = (
    "Q Exactive HF-X",
    "Q Exactive Plus",
    "Q Exactive GC",
    "Orbitrap Exploris 120",
    "Orbitrap Exploris 480",
    "Orbitrap Exploris GC 240",
)
WITH_OTHER_ANALYZERS = (
    "Orbitrap Fusion Lumos",
    "Orbitrap Eclipse",
    "Orbitrap Astral",
    "LTQ Orbitrap Velos",
)

# A survey scan and its fragment scans, one of them read as the ion trap.
ONE_ITMS = ["FTMS", "FTMS", "ITMS", "FTMS", "FTMS"]


class Reader:
    """An OpenTFRaw ``RawFile`` reduced to what a scan selection reads."""

    def __init__(self, analyzers: list, model: str | None):
        self.analyzers = list(analyzers)
        self.instrument_model = model
        self.first_scan = 1
        self.num_scans = len(analyzers)
        self.tables_read = 0

    def scan_table(self) -> dict:
        self.tables_read += 1
        count = len(self.analyzers)
        return {
            "scan_number": list(range(1, count + 1)),
            "retention_time": [n / 60 for n in range(1, count + 1)],
            "polarity": ["+"] * count,
            "ms_level": [1] + [2] * (count - 1),
            "analyzer": list(self.analyzers),
            "total_ion_current": [1.0] * count,
            "low_mz": [40.0] * count,
            "high_mz": [600.0] * count,
        }


@pytest.fixture
def opened(monkeypatch, tmp_path):
    """Open a backend on a :class:`Reader` of these analyzers and model."""
    readers = []

    def open_(analyzers, model):
        def raw_file(_path):
            readers.append(Reader(analyzers, model))
            return readers[-1]

        monkeypatch.setattr(opentfraw, "RawFile", raw_file)
        return OpenTFRawBackend(str(tmp_path / "absent.raw")).__enter__()

    open_.readers = readers
    return open_


@pytest.mark.parametrize("model", ORBITRAP_ONLY)
def test_another_analyzer_on_an_orbitrap_only_instrument(opened, model):
    backend = opened(ONE_ITMS, model)

    with pytest.raises(MisreadScanEventsError) as refused:
        backend.scan_indices(ms_type=None)

    message = str(refused.value)
    assert "1 of its 5 scans read as an analyzer other than the Orbitrap" in message
    assert model in message
    assert "#2394" in message


@pytest.mark.parametrize("model", WITH_OTHER_ANALYZERS)
def test_an_instrument_with_other_analyzers_is_not_judged(opened, model):
    backend = opened(ONE_ITMS, model)

    assert backend.scan_indices(ms_type=None) == [1, 2, 3, 4, 5]


def test_an_instrument_it_cannot_name_is_not_judged(opened):
    backend = opened(ONE_ITMS, None)

    assert backend.scan_indices(ms_type=None) == [1, 2, 3, 4, 5]


def test_every_scan_read_as_the_orbitrap(opened):
    backend = opened(["FTMS"] * 5, "Q Exactive HF-X")

    assert backend.scan_indices(ms_type="Ms2") == [2, 3, 4, 5]


def test_a_scan_read_as_no_analyzer(opened):
    # An event read from the wrong bytes can hold an analyzer byte OpenTFRaw
    # has no name for.
    backend = opened(["FTMS", None, "FTMS"], "Orbitrap Exploris 480")

    with pytest.raises(MisreadScanEventsError, match="1 of its 3 scans"):
        backend.polarities()


def test_every_foreign_scan_is_counted(opened):
    backend = opened(["FTMS", "ITMS", "SQMS", "TQMS", "Sector", None], "Q Exactive")

    with pytest.raises(MisreadScanEventsError, match="5 of its 6 scans"):
        backend.polarities()


def test_opening_reads_no_scan_table(opened):
    # Opening costs what it did: what needs no scan event is still answered,
    # and the table is read once, for the first selection, and judged there.
    backend = opened(ONE_ITMS, "Q Exactive HF-X")
    reader = opened.readers[0]

    assert backend.instrument_details()["Model"] == "Q Exactive HF-X"
    assert reader.tables_read == 0
    with pytest.raises(MisreadScanEventsError):
        backend.polarities()
    assert reader.tables_read == 1


def test_every_selection_is_refused(opened):
    backend = opened(ONE_ITMS, "Q Exactive HF-X")

    for select in (
        backend.polarities,
        backend.mass_range,
        backend.scan_times,
        backend.scan_filters,
    ):
        with pytest.raises(MisreadScanEventsError):
            select()


def test_a_refusal_is_a_value_error_and_no_empty_file(opened):
    backend = opened(ONE_ITMS, "Q Exactive HF-X")

    with pytest.raises(ValueError) as refused:
        backend.polarities()
    assert not isinstance(refused.value, NoScansFoundError)


def test_a_refused_file_is_let_go(monkeypatch, tmp_path):
    # The converter handles a refusal, and moves the file aside, while it
    # holds it. The refusal is raised inside the backend's context, so its
    # traceback holds the backend, whose reader, and with it the file, must
    # be let go by then.
    readers = []

    def raw_file(_path):
        readers.append(Reader(ONE_ITMS, "Q Exactive HF-X"))
        return readers[-1]

    monkeypatch.setattr(opentfraw, "RawFile", raw_file)
    with pytest.raises(MisreadScanEventsError) as refused:
        with OpenTFRawBackend(str(tmp_path / "absent.raw")) as backend:
            opened_reader = weakref.ref(readers.pop())
            backend.polarities()

    assert refused.value.__traceback__ is not None
    assert opened_reader() is None


@pytest.mark.parametrize("path", [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH])
def test_the_committed_files_are_judged_and_read(path):
    # Both are from an Orbitrap-only instrument under the name OpenTFRaw
    # reads for it, so the check runs on real files and passes them.
    with OpenTFRawBackend(path) as backend:
        model = backend.instrument_details()["Model"]
        analyzers = {s["analyzer"] for s in backend._all_scans()}

    assert "Exploris" in model
    assert analyzers == {"FTMS"}
