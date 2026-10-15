"""Whether a Thermo raw file records a run header.

A raw file opens with a fixed file header, the sequence row the acquisition
was started from, the autosampler block and the raw file info. The raw file
info holds one run header address per controller, and the run header is the
index to everything a controller recorded: its scan index, its scan events,
its trailers. Nothing in the file can be read without one.

An acquisition that ends before it records anything leaves a file that holds
that front and the instrument method and nothing else: the controller count
and the acquisition date are zero, the scan data address is the end of the
file, and every run header address is zero.

OpenTFRaw has no word for that. With no address to follow it reads a run
header at offset zero, which puts the stream addresses it then follows past
the raw file info, among the bytes of the instrument method. What lies there
decides how the open fails. An address larger than the filesystem lets a file
be has the seek refused: ``Invalid argument (os error 22)``. A smaller one is
sought to, and the read finds nothing there: ``unexpected end of file at
offset 0x2700000026``, which carries the address and so reads differently for
every method. :func:`records_no_run_header` is how either failure is told
from a file that is damaged.

The layout walked here is the one OpenTFRaw's reader follows for file format
versions 64 and 66 (``header.rs``, ``seq_row.rs`` and ``raw_file_info.rs`` in
its ``opentfraw`` crate), which are the versions an Orbitrap writes.
"""

from __future__ import annotations

import os
import struct
from typing import BinaryIO


_MAGIC = 0xA101
_SIGNATURE = "Finnigan\0".encode("utf-16-le")
_VERSION_OFFSET = 36
_FILE_HEADER_SIZE = 1356
# The versions whose raw file info carries the 64-bit address table read below.
_VERSIONS = frozenset({64, 66})

# Sequence row: the injection data, then two runs of length-prefixed text
# fields with one integer between them.
_INJECTION_DATA_SIZE = 64
_SEQ_ROW_TEXT_FIELDS = (16, 15)
_AUTOSAMPLER_PREAMBLE_SIZE = 24
# Raw file info, up to the address table: the method flag and the acquisition
# date, seven integers of which the third is the controller count, and a
# reserved area.
_DATE_BLOCK_SIZE = 20
_RESERVED_AREA_SIZE = 760
# One table entry is an address and two integers. The first entry is the scan
# data, the others the controllers' run headers; the reader takes two of those
# when the count says fewer.
_TABLE_ENTRY_SIZE = 16
_MIN_RUN_HEADER_ENTRIES = 2
_MAX_RUN_HEADER_ENTRIES = 64


class _NotFollowed(Exception):
    """The front of the file does not follow the layout."""


def _read(f: BinaryIO, n: int) -> bytes:
    data = f.read(n)
    if len(data) != n:
        raise _NotFollowed
    return data


def _u32(f: BinaryIO) -> int:
    return struct.unpack("<I", _read(f, 4))[0]


def _skip_text_field(f: BinaryIO) -> None:
    """Step over a text field: a count of UTF-16 code units, then the units.

    A count that runs past the end of the file is not checked for here: the
    next read comes back short, and that is what reports it.
    """
    f.seek(2 * _u32(f), os.SEEK_CUR)


def _run_header_addresses(f: BinaryIO) -> list[int]:
    """The run header addresses the raw file info records, one per controller.

    :param f: The raw file, opened for reading in binary mode.
    :return: The addresses, in the order the file lists them.
    :rtype: list[int]
    :raises _NotFollowed: When the file is not a raw file of a version walked
        here, or ends before the address table does.
    """
    front = _read(f, _VERSION_OFFSET + 4)
    (magic,) = struct.unpack_from("<H", front, 0)
    (version,) = struct.unpack_from("<I", front, _VERSION_OFFSET)
    if (
        magic != _MAGIC
        or front[2 : 2 + len(_SIGNATURE)] != _SIGNATURE
        or version not in _VERSIONS
    ):
        raise _NotFollowed

    f.seek(_FILE_HEADER_SIZE + _INJECTION_DATA_SIZE)
    first_run, second_run = _SEQ_ROW_TEXT_FIELDS
    for _ in range(first_run):
        _skip_text_field(f)
    _u32(f)
    for _ in range(second_run):
        _skip_text_field(f)

    f.seek(_AUTOSAMPLER_PREAMBLE_SIZE, os.SEEK_CUR)
    _skip_text_field(f)

    f.seek(_DATE_BLOCK_SIZE, os.SEEK_CUR)
    controller_count = struct.unpack("<7I", _read(f, 28))[2]
    n_entries = max(controller_count, _MIN_RUN_HEADER_ENTRIES)
    if n_entries > _MAX_RUN_HEADER_ENTRIES:
        # More entries than the table has room for: the count is not one, and
        # must not size a read.
        raise _NotFollowed

    f.seek(_RESERVED_AREA_SIZE + _TABLE_ENTRY_SIZE, os.SEEK_CUR)
    table = _read(f, n_entries * _TABLE_ENTRY_SIZE)
    return [
        struct.unpack_from("<Q", table, i * _TABLE_ENTRY_SIZE)[0]
        for i in range(n_entries)
    ]


def records_no_run_header(datafile_path: str) -> bool:
    """Whether a raw file was written without a run header.

    True only when the front of the file can be followed to its address table
    and every run header address there is zero. Anything else is False: a file
    that records an address, a file of a version not walked here, a file that
    is cut short or is not a raw file at all, and a file that cannot be read.
    So the answer can name a failure to open, and can never be the reason for
    one.

    :param datafile_path: Path to the raw file.
    :return: True when the file records no run header.
    :rtype: bool
    """
    try:
        with open(datafile_path, "rb") as f:
            return not any(_run_header_addresses(f))
    except (OSError, _NotFollowed):
        return False
