"""A raw file written without a run header is named, not reported as a fault.

An acquisition that ends before it records anything leaves the front of a raw
file and the instrument method behind it: no run header, so no index to any
scan. Neither reader says so. OpenTFRaw reads a run header at offset zero
instead, which puts the addresses it then follows inside the method, and the
Thermo library fails selecting an MS controller the file does not have. Both
reached error monitoring as a fault in Mascope, for a file that every aborted
acquisition produces.

What OpenTFRaw reports depends on the bytes it took for an address. One
larger than the filesystem lets a file be has the seek refused: ``Invalid
argument (os error 22)``. A smaller one is sought to, and the read finds
nothing there: ``unexpected end of file at offset 0x2700000026``, with the
address in the text, so a different one for every method.

The files here are built by the test, at the layout
:mod:`mascope_thermo.run_header` walks, with a pattern where the method would
be that stands for each of the two.
"""

import os
import struct

import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import open_backend
from mascope_thermo.run_header import records_no_run_header
from mascope_thermo.thermo import NoScansFoundError, UnfinishedAcquisitionError


def _text(value: str = "") -> bytes:
    return struct.pack("<I", len(value)) + value.encode("utf-16-le")


def _front(
    run_header_addresses=(0, 0),
    controller_count: int = 0,
    version: int = 66,
    magic: int = 0xA101,
    first_text: bytes | None = None,
) -> bytes:
    """The front of a raw file, up to the end of its address table."""
    file_header = bytearray(1356)
    struct.pack_into("<H", file_header, 0, magic)
    file_header[2:20] = "Finnigan\0".encode("utf-16-le")
    struct.pack_into("<I", file_header, 36, version)

    texts = [_text("7"), _text("C:\\methods\\full_scan.meth")] + [_text()] * 14
    if first_text is not None:
        texts[0] = first_text
    seq_row = bytes(64) + b"".join(texts) + bytes(4) + _text() * 15
    autosampler = bytes(24) + _text()
    info = bytes(20) + struct.pack("<7I", 1, 0, controller_count, 0, 0, 0, 0)
    table = bytes(16) + b"".join(
        struct.pack("<QII", address, 0, 0) for address in run_header_addresses
    )
    return bytes(file_header) + seq_row + autosampler + info + bytes(760) + table


# What stands where the instrument method would be, eight bytes that repeat.
# OpenTFRaw takes its addresses from there, at offsets that are multiples of
# eight, so the pattern is the address: one no seek accepts, and one a seek
# accepts and a read finds nothing at. The second is the pair of consecutive
# sector numbers a method's own sector chain put there in a real file.
SEEK_REFUSED = b"\xff" * 8
NOTHING_THERE = struct.pack("<II", 0x26, 0x27)


def _unfinished(tmp_path, method: bytes = SEEK_REFUSED, **front) -> str:
    """Write a file that ends where an unfinished acquisition's does.

    After the address table come the zeros that pad its region, the raw file
    info's five label headings and the computer name, all empty here, and
    then the pattern standing for the instrument method, kept in step with
    the file's offsets.
    """
    n_entries = 1 + len(front.get("run_header_addresses", (0, 0)))
    rest_of_info = bytes(1048 - 16 * n_entries) + _text() * 6
    before = _front(**front) + rest_of_info
    path = tmp_path / "ORBI-1_unfinished.raw"
    path.write_bytes(before + (method * 800)[len(before) % 8 :][:6000])
    return str(path)


# The reader's own words for each pattern. A refused seek is reported in the
# operating system's.
READER_ERRORS = {
    SEEK_REFUSED: "os error 131" if os.name == "nt" else "os error 22",
    NOTHING_THERE: "unexpected end of file at offset 0x2700000026",
}


class TestRecordsNoRunHeader:
    def test_a_file_with_every_address_zero_records_none(self, tmp_path):
        assert records_no_run_header(_unfinished(tmp_path)) is True

    @pytest.mark.parametrize("addresses", [(0x5000, 0), (0, 0x5000)])
    def test_one_recorded_address_is_enough(self, tmp_path, addresses):
        # The second slot counts as much as the first: the reader skips a zero
        # and reads the run header the other one points at.
        path = _unfinished(tmp_path, run_header_addresses=addresses)
        assert records_no_run_header(path) is False

    def test_every_controller_the_file_counts_is_looked_at(self, tmp_path):
        path = _unfinished(
            tmp_path, run_header_addresses=(0, 0, 0x5000), controller_count=3
        )
        assert records_no_run_header(path) is False

    @pytest.mark.parametrize("path", [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH])
    def test_a_finished_acquisition_records_one(self, path):
        assert records_no_run_header(path) is False

    @pytest.mark.parametrize(
        "front",
        [
            {"magic": 0x1234},
            {"version": 63},
            # A text field whose length runs past the end of the file.
            {"first_text": struct.pack("<I", 1 << 20)},
        ],
        ids=["magic", "version", "text-length"],
    )
    def test_a_front_that_cannot_be_followed_is_not_called_unfinished(
        self, tmp_path, front
    ):
        assert records_no_run_header(_unfinished(tmp_path, **front)) is False

    @pytest.mark.parametrize("controller_count", [65, 0xFFFFFFFF])
    def test_a_controller_count_the_table_cannot_hold_is_not_believed(
        self, tmp_path, controller_count
    ):
        # Zeros pad the table's region, so entries read on past the two it
        # holds would all be zero addresses and the file would pass for an
        # unfinished one.
        path = _unfinished(tmp_path, controller_count=controller_count)
        assert records_no_run_header(path) is False

    @pytest.mark.parametrize("kept", [0, 30, 1356, 1500, -60, -40, -8])
    def test_a_file_cut_short_is_not_called_unfinished(self, tmp_path, kept):
        # Cut inside the file header, the sequence row, the raw file info and,
        # counted from its end, the address table: an upload that stopped is
        # not an acquisition that did, and stays the fault it is.
        path = tmp_path / "ORBI-1_cut.raw"
        path.write_bytes(_front()[:kept])
        assert records_no_run_header(str(path)) is False

    def test_a_missing_file_is_not_called_unfinished(self, tmp_path):
        assert records_no_run_header(str(tmp_path / "ORBI-1_absent.raw")) is False


@pytest.fixture(params=[SEEK_REFUSED, NOTHING_THERE], ids=["refused", "nothing"])
def method(request):
    """Run a test once for each way OpenTFRaw fails on an unfinished file."""
    return request.param


class TestOpeningAnUnfinishedAcquisition:
    def test_the_reader_alone_does_not_say_what_is_wrong(self, tmp_path, method):
        # What the naming exists for: two errors that share no word, neither
        # of them about the acquisition. Should a reader release start
        # reporting the condition itself, this is the test that says so.
        import opentfraw

        with pytest.raises(OSError, match=READER_ERRORS[method]):
            opentfraw.RawFile(_unfinished(tmp_path, method))

    def test_both_backends_name_it(self, backend, tmp_path, method):  # noqa: ARG002
        with pytest.raises(UnfinishedAcquisitionError, match="no run header") as e:
            with open_backend(_unfinished(tmp_path, method)):
                pass
        # The reader's own error stays reachable for whoever debugs the file.
        assert e.value.__cause__ is not None

    def test_it_is_a_file_with_no_scans_to_its_callers(self, tmp_path):
        with pytest.raises(NoScansFoundError):
            with open_backend(_unfinished(tmp_path)):
                pass

    def test_the_file_is_left_free_to_be_moved_aside(self, backend, tmp_path):  # noqa: ARG002
        # The file converter moves a file it could not process out of the
        # streams folder, which a reader still holding it open would block.
        path = _unfinished(tmp_path)
        with pytest.raises(UnfinishedAcquisitionError):
            with open_backend(path):
                pass
        (tmp_path / "ORBI-1_unfinished.raw").rename(tmp_path / "moved.raw")

    def test_another_failure_to_open_is_left_as_the_reader_reported_it(
        self,
        backend,
        tmp_path,  # noqa: ARG002
    ):
        # A file cut short inside its front records no address either, because
        # it records nothing: it must keep failing as the fault it may be.
        path = tmp_path / "ORBI-1_cut.raw"
        path.write_bytes(_front()[:1500])
        with pytest.raises(Exception) as e:  # noqa: PT011
            with open_backend(str(path)):
                pass
        assert not isinstance(e.value, NoScansFoundError)
