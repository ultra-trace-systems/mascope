"""A file the converter fails on is reported with its size.

Raw files have failed to open with nothing but "Invalid argument (os error 22)"
to go on. The size at the moment of failure is what tells a file picked up too
early - empty, or still being written - from one that is damaged.
"""

from mascope_backend.file_converter.base_processor import describe_file_size


def test_a_file_is_described_by_its_size(tmp_path):
    path = tmp_path / "ORBI-1_run.raw"
    path.write_bytes(b"\x01\xa1" * 512)

    assert describe_file_size(path) == "1024 bytes"


def test_an_empty_file_says_so(tmp_path):
    path = tmp_path / "ORBI-1_run.raw"
    path.touch()

    assert describe_file_size(path) == "0 bytes"


def test_a_file_already_gone_does_not_fail_the_report(tmp_path):
    assert describe_file_size(tmp_path / "missing.raw") == "size unknown"
