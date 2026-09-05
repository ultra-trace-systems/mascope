"""Tests: the stored name of an h5 file carries its acquisition time.

The filestore files a sample under the date in its name, so the name has to
carry the acquisition time. The Thermo processor always injects it; the h5
processor injects it only when the on-disk name does not already carry one,
so the names TOF acquisition software stamps itself stay exactly as they are.
"""

from queue import Queue
from threading import Event

from mascope_backend.file_converter.base_processor import INSTRUMENT_TYPE_BY_EXTENSION
from mascope_tofwerk.processor import H5Processor


class _StampedH5Processor(H5Processor):
    """An h5 processor whose acquisition time is known without a file."""

    @property
    def timestamp(self) -> str:
        return "2026-09-05T10:12:01"


def _processor(path: str) -> H5Processor:
    processor = _StampedH5Processor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = path
    return processor


def test_a_name_that_carries_a_time_is_kept():
    assert (
        _processor("/data/tof3_20260905101201_ambient.h5").filename
        == "tof3_20260905101201_ambient"
    )


def test_a_name_without_a_time_gets_it_after_the_instrument_segment():
    assert (
        _processor("/data/Test_ambient.h5").filename
        == "Test_2026.09.05-10h12m01s_ambient"
    )


def test_a_name_with_no_underscore_gets_it_appended():
    assert _processor("/data/ambient.h5").filename == "ambient_2026.09.05-10h12m01s"


def test_the_reader_names_the_instrument_class():
    processor = _processor("/data/Test_ambient.h5")
    assert processor.instrument_type == "tof"
    assert INSTRUMENT_TYPE_BY_EXTENSION[processor.file_extension] == "tof"
