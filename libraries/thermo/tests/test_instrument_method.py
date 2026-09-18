"""The instrument method name, read from the committed KORBI files.

``ReaderBackend.method_file`` feeds ``sample_file.method_file`` and the
instrument config posted beside it, so the value is pinned exactly, under each
reader backend: the Xcalibur path of the ``.meth`` file, verbatim.
"""

import pytest
from conftest import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

from mascope_thermo.backend import open_backend


_METHOD_DIR = "C:\\Xcalibur\\methods\\5.1 Methods\\"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param(
            POS_ORBI_FILE_PATH,
            _METHOD_DIR + "ambient_pos_massrange40-500.meth",
            id="pos",
        ),
        pytest.param(
            NEG_ORBI_FILE_PATH,
            _METHOD_DIR + "ambient_neg_massrange40-500.meth",
            id="neg",
        ),
    ],
)
def test_method_file_is_the_recorded_meth_path(backend, path, expected):  # noqa: ARG001
    with open_backend(path) as reader:
        assert reader.method_file() == expected


class _NoSampleInfo:
    """An ``opentfraw.RawFile`` whose header carries no sample information."""

    sample_info = None


class _NoMethodRecorded:
    """An ``opentfraw.RawFile`` whose sample information names no method."""

    sample_info = {"inst_method": "", "proc_method": ""}


@pytest.mark.parametrize("raw", [_NoSampleInfo(), _NoMethodRecorded()])
def test_opentfraw_reports_a_missing_method_as_empty(raw):
    from mascope_thermo.backend import OpenTFRawBackend

    reader = OpenTFRawBackend.__new__(OpenTFRawBackend)
    reader._raw = raw
    assert reader.method_file() == ""
