"""A file the converter fails on is reported with its size and age.

Raw files have failed to open with nothing but "Invalid argument (os error 22)"
to go on. The watcher only holds a file until its size is stable across one
poll, so a file picked up mid-write looks, by size alone, like a damaged one;
the time since its last write is what tells the two apart.
"""

import os
import time

from mascope_backend.file_converter.base_processor import describe_file_state


def test_a_file_is_described_by_its_size_and_age(tmp_path):
    path = tmp_path / "ORBI-1_run.raw"
    path.write_bytes(b"\x01\xa1" * 512)
    written = time.time() - 90
    os.utime(path, (written, written))

    size, age = describe_file_state(path).split(", ")

    assert size == "1024 bytes"
    seconds = float(age.removeprefix("last written ").removesuffix(" s ago"))
    assert 90 <= seconds < 120


def test_an_empty_file_says_so(tmp_path):
    path = tmp_path / "ORBI-1_run.raw"
    path.touch()

    assert describe_file_state(path).startswith("0 bytes, last written ")


def test_a_file_already_gone_does_not_fail_the_report(tmp_path):
    assert describe_file_state(tmp_path / "missing.raw") == "size unknown"
