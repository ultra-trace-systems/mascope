"""Scan-event records that carry a name are read at their own size (#2391).

A version-66 scan-event record is a 136-byte preamble and a 136-byte body
whose last word counts the UTF-16 code units of a name that follows. A
targeted MS2 scan from an inclusion list stores the entry's name there, so
its record is longer than the others, and OpenTFRaw 2.0.0, which reads every
record of a file at one inferred size, decodes the rest of the file at an
offset that drifts. ``mascope_thermo.v66_scan_events`` reads such a file's
events itself, until the pinned reader does (MAS-170 deletes both).

No file with named records can be committed, so the files here are built by
the test: the front of a raw file, a run header, a scan index, one profile
packet per scan, and the scan-event stream between the run header's trailer
and parameters addresses. OpenTFRaw cannot open them, so the reader the
backend wraps is a stand-in that answers from the same scans, the way
OpenTFRaw answers from a misread stream: everything event-dependent wrong.
"""

import struct
import weakref
from dataclasses import dataclass, field

import numpy as np
import opentfraw
import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo import v66_scan_events
from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.run_header import _address_table
from mascope_thermo.runtime import runtime
from mascope_thermo.v66_scan_events import (
    UnreadableScanEventsError,
    _misread_layout,
    redecode_if_misread,
)


EXPLORIS = "Orbitrap Exploris 120"

# Where the parts of a built file go. The front ends well before the run
# header; the rest follow it in this order.
RUN_HEADER_AT = 0x4000
RUN_HEADER_SIZE = 7464
# Where a controller listed before the MS one has its run header.
OTHER_RUN_HEADER_AT = 0x1000
SCAN_INDEX_AT = RUN_HEADER_AT + RUN_HEADER_SIZE + 0x100

# An Orbitrap's frequency-to-m/z calibration, as a version-66 event stores
# it: two leading values, then A, B and C of m/z = A + B/f^2 + C/f^4.
COEFFICIENTS = (0.0, 0.0, 0.0, 1.0e8, 0.0)


@dataclass
class Scan:
    """One scan of a built file: its event, and what the trailer says."""

    ms_level: int = 1
    polarity: int = 1
    name: str = ""
    precursor_mz: float = 0.0
    trailer_event: int | None = 1
    trailer_segment: int | None = 1
    analyzer: int = 4
    low_mz: float = 40.0
    high_mz: float = 600.0
    hcd_energy: str = "3.00"
    coefficients: tuple = COEFFICIENTS
    profile: list = field(default_factory=lambda: [(1000, [1.0, 5.0, 2.0])])
    #: Each profile chunk's m/z correction. Real files carry one (packet
    #: layout 0x80, a three-word chunk header); None writes layout 0.
    fudge: float | None = None

    def record(self) -> bytes:
        preamble = bytearray(136)
        preamble[4] = self.polarity
        preamble[5] = 1  # profile
        preamble[6] = self.ms_level
        preamble[11] = 5  # NSI
        preamble[24] = 4 if self.ms_level >= 2 else 0  # beam-type CID: "hcd"
        preamble[40] = self.analyzer
        body = bytearray(136)
        if self.ms_level >= 2:
            # An Exploris dependent scan: its reaction from body offset 4.
            struct.pack_into("<d", body, 4, self.precursor_mz)
            struct.pack_into("<d", body, 20, 0.0)
        struct.pack_into("<2d", body, 64, self.low_mz, self.high_mz)
        struct.pack_into("<I", body, 80, len(self.coefficients))
        for i, value in enumerate(self.coefficients):
            struct.pack_into("<d", body, 84 + 8 * i, value)
        struct.pack_into("<I", body, 132, len(self.name))
        return bytes(preamble) + bytes(body) + self.name.encode("utf-16-le")

    def packet(self) -> bytes:
        words = struct.pack("<2d2I", 0.0, 1.0, len(self.profile), 0)
        for first_bin, signal in self.profile:
            words += struct.pack("<2I", first_bin, len(signal))
            if self.fudge is not None:
                words += struct.pack("<f", self.fudge)
            words += struct.pack(f"<{len(signal)}f", *signal)
        layout = 0 if self.fudge is None else 0x80
        header = struct.pack("<8I2f", 0, len(words) // 4, 0, layout, 0, 0, 0, 0, 0, 0)
        return header + words

    def params(self) -> dict:
        params = {"HCD Energy V:": self.hcd_energy}
        if self.trailer_event is not None:
            params["Scan Event:"] = self.trailer_event
        if self.trailer_segment is not None:
            params["Scan Segment:"] = self.trailer_segment
        return params


def _text(value: str = "") -> bytes:
    return struct.pack("<I", len(value)) + value.encode("utf-16-le")


def _front(data_addr: int, version: int, run_headers=(RUN_HEADER_AT, 0)) -> bytes:
    """The front of a raw file, up to the end of its address table."""
    file_header = bytearray(1356)
    struct.pack_into("<H", file_header, 0, 0xA101)
    file_header[2:20] = "Finnigan\0".encode("utf-16-le")
    struct.pack_into("<I", file_header, 36, version)
    seq_row = bytes(64) + _text() * 16 + bytes(4) + _text() * 15
    autosampler = bytes(24) + _text()
    controllers = sum(1 for address in run_headers if address)
    info = bytes(20) + struct.pack("<7I", 1, 0, controllers, 0, 0, 0, 0)
    table = struct.pack("<QII", data_addr, 0, 0) + b"".join(
        struct.pack("<QII", address, 0, 0) for address in run_headers
    )
    return bytes(file_header) + seq_row + autosampler + info + bytes(760) + table


def build(
    tmp_path,
    scans: list[Scan],
    version: int = 66,
    records: bytes | None = None,
    name: str = "built.raw",
    other_controller_first: bool = False,
) -> str:
    """Write a raw file holding ``scans``, and return its path.

    ``records`` replaces the scan-event stream the scans would make.
    ``other_controller_first`` lists a controller with no scan events and
    scan data of its own (a UV detector, say) before the MS controller.
    """
    stream = records if records is not None else b"".join(s.record() for s in scans)
    data_addr = SCAN_INDEX_AT + 88 * len(scans) + 0x100
    packets, offsets = b"", []
    for scan in scans:
        offsets.append(len(packets))
        packets += scan.packet()
    trailer_addr = data_addr + len(packets) + 0x100
    params_addr = trailer_addr + 4 + len(stream)

    run_header = bytearray(RUN_HEADER_SIZE)
    struct.pack_into("<2I", run_header, 8, 1, len(scans))
    struct.pack_into("<I", run_header, 7376, len(scans))
    struct.pack_into("<2Q", run_header, 7408, SCAN_INDEX_AT, data_addr)
    struct.pack_into("<2Q", run_header, 7448, trailer_addr, params_addr)
    index = b"".join(
        bytes(72) + struct.pack("<Q", offset) + bytes(8) for offset in offsets
    )

    image = bytearray(params_addr + 64)
    run_headers = (RUN_HEADER_AT, 0)
    if other_controller_first:
        other = bytearray(RUN_HEADER_SIZE)
        struct.pack_into("<2I", other, 8, 1, 3)
        struct.pack_into("<2Q", other, 7408, 0x100, data_addr + 8)
        image[OTHER_RUN_HEADER_AT : OTHER_RUN_HEADER_AT + RUN_HEADER_SIZE] = other
        run_headers = (OTHER_RUN_HEADER_AT, RUN_HEADER_AT)
    front = _front(data_addr, version, run_headers)
    image[: len(front)] = front
    image[RUN_HEADER_AT : RUN_HEADER_AT + RUN_HEADER_SIZE] = run_header
    image[SCAN_INDEX_AT : SCAN_INDEX_AT + len(index)] = index
    image[data_addr : data_addr + len(packets)] = packets
    image[trailer_addr + 4 : params_addr] = stream
    path = tmp_path / name
    path.write_bytes(bytes(image))
    return str(path)


class MisreadRawFile:
    """What OpenTFRaw 2.0.0 opens a built file as.

    The scan index and the trailer read right; every value it derives from a
    scan event comes out as noise, as it does from a misread stream.
    """

    def __init__(self, scans: list[Scan], model: str = EXPLORIS):
        self.scans = scans
        self.first_scan = 1
        self.num_scans = len(scans)
        self.instrument_model = model

    def scan_table(self) -> dict:
        rows = [self.scan(n) for n in range(1, self.num_scans + 1)]
        return {key: [row[key] for row in rows] for key in rows[0]}

    def scan(self, scan_number: int) -> dict:
        scan = self.scans[scan_number - 1]
        return {
            "scan_number": scan_number,
            "scan_event": 36,
            "scan_segment": 0,
            "data_size": 4096,
            "ms_level": 8,
            "is_dia": True,
            "is_wideband": False,
            "polarity": "-",
            "scan_mode": "centroid",
            "analyzer": "ITMS",
            "retention_time": scan_number / 60,
            "filter_string": "ITMS - c EI Full ms8",
            "total_ion_current": 1.0,
            "low_mz": scan.low_mz,
            "high_mz": scan.high_mz,
            "charge": None,
            "precursor_mz": None,
            "isolation_target_mz": None,
            "isolation_width": None,
            "collision_energy": None,
            "collision_energy_is_nce": False,
            "activation": None,
            "master_scan_number": None,
            "extra": {"opentfraw.is_dia": "true", "opentfraw.is_wideband": "false"},
            "mz": np.array([100.0]),
            "intensity": np.array([1.0], dtype=np.float32),
        }

    def scan_filter(self, scan_number: int) -> str | None:
        return "ITMS - c EI Full ms8" if 1 <= scan_number <= self.num_scans else None

    def scan_parameters(self, scan_number: int) -> dict:
        return self.scans[scan_number - 1].params()

    def profile(self, scan_number: int):
        if not 1 <= scan_number <= self.num_scans:
            raise IndexError(scan_number)
        return np.array([953.99]), np.array([1.0])


def _tms2(precursor_mz: float, name: str, **kw) -> Scan:
    return Scan(ms_level=2, name=name, precursor_mz=precursor_mz, trailer_event=2, **kw)


def _acquisition() -> list[Scan]:
    """Full MS scans, then targeted MS2 scans of names that make their
    records 286, 288 and 290 bytes."""
    return [Scan() for _ in range(5)] + [
        _tms2(484.2389, "C6H5NO+"),  # 7 units: 286 bytes
        _tms2(452.2490, "C7H7NO2+"),  # 8 units: 288 bytes
        _tms2(420.2592, "C8H9NO2H+"),  # 9 units: 290 bytes
        _tms2(484.2389, "C6H5NO+"),
        _tms2(452.2490, "C7H7NO2+"),
    ]


@pytest.fixture
def enter(monkeypatch):
    """Enter a backend whose OpenTFRaw reader is a :class:`MisreadRawFile`."""
    import opentfraw

    def enter_(scans, path, model=EXPLORIS):
        monkeypatch.setattr(
            opentfraw, "RawFile", lambda _path: MisreadRawFile(scans, model)
        )
        return OpenTFRawBackend(path).__enter__()

    return enter_


class TestWhichFilesItEngages:
    def test_records_of_mixed_sizes_that_walk_to_the_end(self, tmp_path):
        scans = _acquisition()
        layout = _misread_layout(build(tmp_path, scans))

        assert layout is not None
        assert [len(p) + len(b) for p, b in layout.records] == [272] * 5 + [
            286,
            288,
            290,
            286,
            288,
        ]

    def test_records_of_one_size_are_read_right_already(self, tmp_path):
        # The stream divides evenly, so OpenTFRaw reads every record at its
        # size: unnamed (the manual MS2 files), or all named alike.
        assert _misread_layout(build(tmp_path, [Scan()] * 6)) is None
        same_name = [_tms2(484.2389, "C6H5NO+") for _ in range(6)]
        assert _misread_layout(build(tmp_path, same_name)) is None

    def test_a_walk_that_does_not_end_at_the_stream_end(self, tmp_path):
        scans = _acquisition()
        stream = b"".join(s.record() for s in scans)
        assert (
            _misread_layout(build(tmp_path, scans, records=stream + bytes(2))) is None
        )
        assert _misread_layout(build(tmp_path, scans, records=stream[:-2])) is None

    def test_a_name_that_is_not_text(self, tmp_path):
        scans = _acquisition()
        scans[6] = _tms2(452.2490, "C7H7\x00O2+")
        assert _misread_layout(build(tmp_path, scans)) is None

    def test_a_file_of_another_version(self, tmp_path):
        assert _misread_layout(build(tmp_path, _acquisition(), version=64)) is None

    def test_tribrid_records_are_left_to_opentfraw(self, tmp_path):
        # Primary and dependent records of 232 and 344 bytes, which OpenTFRaw
        # reads at those sizes. The walk cannot read them: a record is never
        # shorter than 272 bytes.
        records = b"".join(bytes(232) if i % 2 == 0 else bytes(344) for i in range(10))
        path = build(tmp_path, _acquisition(), records=records)
        assert _misread_layout(path) is None

    @pytest.mark.parametrize("path", [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH])
    def test_the_committed_files_are_opened_as_they_were(self, path):
        assert _misread_layout(path) is None
        with OpenTFRawBackend(path) as backend:
            assert type(backend._raw).__name__ == "RawFile"

    def test_a_file_that_cannot_be_followed(self, tmp_path):
        path = tmp_path / "short.raw"
        path.write_bytes(b"\x01\xa1" + bytes(100))
        assert _misread_layout(str(path)) is None
        assert _misread_layout(str(tmp_path / "absent.raw")) is None

    def test_a_named_record_after_many_unnamed_ones(self, tmp_path):
        # One named record in 41: the stream's floor average is 272, so
        # OpenTFRaw reads the first 40 records right and drifts from the 41st.
        scans = [Scan() for _ in range(40)] + [_tms2(484.2389, "C6H5NO+")]
        assert _misread_layout(build(tmp_path, scans)) is not None

    def test_a_controller_listed_before_the_ms_one_is_passed_over(self, tmp_path):
        # OpenTFRaw reads the first run header whose trailer counts scan
        # events, or whose scan data address is the file's; this one has
        # neither.
        path = build(tmp_path, _acquisition(), other_controller_first=True)
        layout = _misread_layout(path)

        assert layout is not None
        assert layout.scan_index_addr == SCAN_INDEX_AT


class TestWhatKeepsAFileOnTheReader:
    """An engaged layout the reader does not agree with is left to it."""

    def test_a_reader_that_counts_other_scans(self, tmp_path):
        scans = _acquisition()
        raw = MisreadRawFile(scans)
        raw.num_scans = len(scans) + 1
        assert redecode_if_misread(raw, build(tmp_path, scans)) is raw

    def test_a_scan_table_with_a_gap(self, tmp_path):
        scans = _acquisition()
        raw = MisreadRawFile(scans)
        table = raw.scan_table()
        table["scan_number"][4] = 99
        raw.scan_table = lambda: table
        assert redecode_if_misread(raw, build(tmp_path, scans)) is raw

    def test_a_file_the_reader_reads_as_srm(self, tmp_path):
        # A first scan this small is OpenTFRaw's mark of a flat-peak file,
        # whose filters it builds without the scan events.
        scans = _acquisition()
        raw = MisreadRawFile(scans)
        table = raw.scan_table()
        table["data_size"][0] = 50
        raw.scan_table = lambda: table
        assert redecode_if_misread(raw, build(tmp_path, scans)) is raw


class TestWhatItReads:
    def test_each_scan_reads_from_its_own_event(self, tmp_path, enter):
        scans = _acquisition()
        backend = enter(scans, build(tmp_path, scans))

        assert backend.polarities() == {"+"}
        assert backend.scan_indices(ms_type="Ms") == [1, 2, 3, 4, 5]
        assert backend.scan_indices(ms_type="Ms2") == [6, 7, 8, 9, 10]
        filters = [row["filter"] for row in backend.scan_filters()]
        assert filters[0] == "FTMS + p NSI Full ms [40.0000-600.0000]"
        assert filters[5] == "FTMS + p NSI Full ms2 484.2389@hcd3.00 [40.0000-600.0000]"
        assert filters[7] == "FTMS + p NSI Full ms2 420.2592@hcd3.00 [40.0000-600.0000]"

    def test_the_precursor_keys_of_a_fragment_scan(self, tmp_path, enter):
        scans = _acquisition()
        backend = enter(scans, build(tmp_path, scans))
        row = backend._all_scans()[6]

        assert row["ms_level"] == 2
        assert row["analyzer"] == "FTMS"
        assert row["scan_mode"] == "profile"
        assert row["precursor_mz"] == 452.2490
        assert row["isolation_target_mz"] == 452.2490
        assert row["collision_energy"] == 3.0
        assert row["collision_energy_is_nce"] is True
        assert row["activation"] == "hcd"
        assert row["is_dia"] is True  # an MS2 scan flagged as no dependent scan
        assert row["extra"]["opentfraw.is_dia"] == "true"
        assert backend.ms2_events_by_scan()[7] == (452.249, "hcd3.00")

        record = backend._raw.scan(7)
        assert record["ms_level"] == 2
        assert record["precursor_mz"] == 452.2490
        assert record["extra"]["opentfraw.is_dia"] == "true"
        np.testing.assert_array_equal(record["mz"], [100.0])  # the reader's peaks

    def test_a_survey_scan_has_no_precursor(self, tmp_path, enter):
        scans = _acquisition()
        row = enter(scans, build(tmp_path, scans))._all_scans()[0]

        assert row["ms_level"] == 1
        assert row["precursor_mz"] is None
        assert row["activation"] is None
        assert row["is_dia"] is False
        assert row["extra"]["opentfraw.is_dia"] == "false"

    def test_what_does_not_depend_on_the_event_is_the_readers(self, tmp_path, enter):
        scans = _acquisition()
        row = enter(scans, build(tmp_path, scans))._all_scans()[3]

        assert row["scan_event"] == 36
        assert row["retention_time"] == 4 / 60
        assert row["total_ion_current"] == 1.0

    @pytest.mark.parametrize("fudge", [None, 0.25], ids=["layout-0", "layout-0x80"])
    def test_the_profile_is_put_on_its_own_events_calibration(
        self, tmp_path, enter, fudge
    ):
        signal = [1.0, 5.0, 2.0]
        scans = _acquisition()
        scans[2] = Scan(
            coefficients=(0.0, 0.0, 0.5, 2.0e8, 1.0e12),
            profile=[(1000, signal), (2000, [3.0])],
            fudge=fudge,
        )
        backend = enter(scans, build(tmp_path, scans))
        mz, intensity = backend._raw.profile(3)

        freq = np.array([1000.0, 1001.0, 1002.0, 2000.0])
        expected = 0.5 + 2.0e8 / freq**2 + 1.0e12 / freq**4 + (fudge or 0.0)
        np.testing.assert_allclose(mz, expected, rtol=1e-15)
        np.testing.assert_array_equal(intensity, signal + [3.0])
        assert intensity.dtype == np.float64

    def test_a_scan_out_of_range_is_left_to_the_reader(self, tmp_path, enter):
        scans = _acquisition()
        raw = enter(scans, build(tmp_path, scans))._raw

        assert raw.scan_filter(99) is None
        with pytest.raises(IndexError):
            raw.profile(99)

    def test_what_it_does_not_reread_is_not_answered(self, tmp_path, enter):
        scans = _acquisition()
        raw = enter(scans, build(tmp_path, scans))._raw

        with pytest.raises(NotImplementedError, match="#2391"):
            raw.iter_scans()


class TestTheBackstop:
    def test_polarity_switching_within_one_event_is_no_refusal(self, tmp_path, enter):
        # A polarity-switching full scan alternates + and - under one event.
        scans = [Scan(polarity=i % 2, trailer_event=1) for i in range(6)] + [
            _tms2(484.2389, "C6H5NO+"),
            _tms2(452.2490, "C7H7NO2+"),
        ]
        backend = enter(scans, build(tmp_path, scans))

        assert backend.polarities() == {"+", "-"}

    def test_scan_event_0_on_every_scan_is_no_refusal(self, tmp_path, enter):
        # A manual acquisition records event 0 on every scan, of any order.
        scans = _acquisition()
        for scan in scans:
            scan.trailer_event = 0
        backend = enter(scans, build(tmp_path, scans))

        assert len(backend.scan_indices(ms_type="Ms2")) == 5

    def test_one_event_number_in_two_segments_is_no_refusal(self, tmp_path, enter):
        # A method numbers its events within each segment: event 1 is the
        # survey scan of the first and a targeted scan of the second.
        scans = [Scan(trailer_segment=1, trailer_event=1) for _ in range(4)]
        scans.append(_tms2(484.2389, "C6H5NO+", trailer_segment=1))
        scans += [_tms2(452.2490, "C7H7NO2+", trailer_segment=2) for _ in range(3)]
        for scan in scans[5:]:
            scan.trailer_event = 1
        backend = enter(scans, build(tmp_path, scans))

        assert backend.scan_indices(ms_type="Ms2") == [5, 6, 7, 8]

    def test_an_analyzer_an_orbitrap_only_instrument_has_not(self, tmp_path, enter):
        scans = _acquisition()
        scans[3].analyzer = 0  # ITMS
        with pytest.raises(UnreadableScanEventsError) as refused:
            enter(scans, build(tmp_path, scans))

        message = str(refused.value)
        assert (
            "1 of its 10 scans read as an analyzer other than the Orbitrap" in message
        )
        assert EXPLORIS in message
        assert "#2391" in message

    def test_the_analyzer_is_not_judged_on_an_instrument_with_others(
        self, tmp_path, enter
    ):
        scans = _acquisition()
        scans[3].analyzer = 0
        backend = enter(scans, build(tmp_path, scans), model="Orbitrap Fusion Lumos")

        assert backend._all_scans()[3]["analyzer"] == "ITMS"

    def test_one_event_read_as_two_ms_orders(self, tmp_path, enter):
        scans = _acquisition()
        scans[7].trailer_event = 1  # the survey scans' event
        with pytest.raises(UnreadableScanEventsError, match="more than one MS order"):
            enter(scans, build(tmp_path, scans))

    def test_a_refused_file_is_let_go(self, tmp_path, monkeypatch):
        # A refusal outlives the open: the converter handles it, and moves
        # the file aside, while holding it. Its traceback must not keep the
        # reader, and with it the file, open meanwhile.
        import opentfraw

        scans = _acquisition()
        scans[3].analyzer = 0
        opened = []

        def raw_file(_path):
            raw = MisreadRawFile(scans)
            opened.append(weakref.ref(raw))
            return raw

        monkeypatch.setattr(opentfraw, "RawFile", raw_file)
        with pytest.raises(UnreadableScanEventsError) as refused:
            with OpenTFRawBackend(build(tmp_path, scans)):
                pass

        # Asked while the refusal is held, as the converter holds it while it
        # moves the file aside.
        assert refused.value.__traceback__ is not None
        assert opened[0]() is None

    def test_a_refusal_is_a_value_error_and_no_empty_file(self, tmp_path, enter):
        from mascope_thermo.thermo import NoScansFoundError

        scans = _acquisition()
        scans[0].analyzer = 1
        with pytest.raises(ValueError) as refused:
            enter(scans, build(tmp_path, scans))
        assert not isinstance(refused.value, NoScansFoundError)


class TestTheWarning:
    def test_once_per_file(self, tmp_path, enter, monkeypatch):
        monkeypatch.setattr(v66_scan_events, "_warned", set())
        messages: list[str] = []
        sink = runtime.logger.add(
            lambda message: messages.append(message.record["message"]),
            level="WARNING",
        )
        try:
            scans = _acquisition()
            path = build(tmp_path, scans)
            enter(scans, path)
            enter(scans, path)
        finally:
            runtime.logger.remove(sink)

        assert len(messages) == 1
        assert "5 of its records carry inclusion-list names" in messages[0]
        assert "#2391" in messages[0]

    def test_none_for_a_file_it_does_not_engage(self, tmp_path):
        raw = object()
        assert redecode_if_misread(raw, build(tmp_path, [Scan()] * 4)) is raw


def _event(ms_power, *, scan_mode=0, analyzer=4, activation=0, reactions=()):
    preamble = bytearray(136)
    preamble[4], preamble[5], preamble[6] = 1, scan_mode, ms_power
    preamble[10] = 1 if ms_power >= 2 else 0  # dependent
    preamble[11], preamble[24], preamble[40] = 5, activation, analyzer
    return v66_scan_events._Event(bytes(preamble), None, (), tuple(reactions))


@pytest.mark.parametrize(
    ("event", "window", "params", "expected"),
    [
        (
            _event(1, scan_mode=1),
            (350.0, 1500.0),
            {},
            "FTMS + p NSI Full ms [350.0000-1500.0000]",
        ),
        (
            _event(2, activation=1),
            (150.0, 2000.0),
            {"Monoisotopic M/Z:": 645.8311, "HCD Energy:": "28.0"},
            "FTMS + c NSI d Full ms2 645.8311@hcd28.00 [150.0000-2000.0000]",
        ),
        (
            _event(2, analyzer=0, activation=4, reactions=[(440.254, 35.0)]),
            (116.0, 892.0),
            {"Monoisotopic M/Z:": 440.254, "HCD Energy:": "35.0"},
            "ITMS + c NSI d Full ms2 440.2540@cid35.00 [116.0000-892.0000]",
        ),
        (
            _event(
                3, analyzer=0, activation=4, reactions=[(810.5, 35.0), (265.27, 35.0)]
            ),
            (100.0, 1000.0),
            {"Monoisotopic M/Z:": 265.27, "HCD Energy:": "35.0"},
            "ITMS + c NSI d Full ms3 810.5000@cid35.00 265.2700@cid35.00"
            " [100.0000-1000.0000]",
        ),
        (
            _event(2, activation=12),
            (150.0, 2000.0),
            {
                "Monoisotopic M/Z:": 649.12,
                "HCD Energy:": "35.0",
                "Supplemental Activation CE:": 28.0,
            },
            "FTMS + c NSI d Full ms2 649.1200@etd35.00@hcd28.00 [150.0000-2000.0000]",
        ),
    ],
    ids=["ms1", "ms2-from-trailer", "ms2-cid-reaction", "ms3-chain", "ethcd"],
)
def test_the_filter_reads_as_opentfraw_renders_it(event, window, params, expected):
    """OpenTFRaw 2.0.0's own ``build_filter`` cases (``scan_filter.rs``)."""
    assert v66_scan_events._scan_filter(event, *window, params) == expected


@pytest.mark.parametrize("path", [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH])
def test_read_by_its_records_a_committed_file_reads_as_opentfraw_reads_it(path):
    """The committed files' records are unnamed, so OpenTFRaw reads them
    right: the stand-in forced onto them must answer exactly as it does, on
    the packet layout and the trailers real files have."""
    with open(path, "rb") as f:
        _, data_addr, addresses = _address_table(f)
        first, last, n_events, scan_index_addr, start, end = (
            v66_scan_events._ms_run_header(f, addresses, data_addr)
        )
        f.seek(start)
        records = v66_scan_events._walk_named_records(f, end - start, n_events)
    layout = v66_scan_events._Layout(
        data_addr, scan_index_addr, last - first + 1, tuple(records)
    )
    raw = opentfraw.RawFile(path)
    stand_in = v66_scan_events._RedecodedRawFile(raw, path, layout, raw.scan_table())

    assert stand_in.scan_table() == raw.scan_table()
    for n in raw.scan_table()["scan_number"]:
        assert stand_in.scan_filter(n) == raw.scan_filter(n)
        ours, theirs = stand_in.scan(n), raw.scan(n)
        for key in ("mz", "intensity"):
            np.testing.assert_array_equal(ours.pop(key), theirs.pop(key))
        assert ours == theirs
        for mine, its in zip(stand_in.profile(n), raw.profile(n)):
            np.testing.assert_array_equal(mine, its)
