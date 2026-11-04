"""The instrument model a raw file names, as the converter records it.

``RawProcessor.instrument_model`` fills the props' ``instrument_model``, which
the registration carries to the instrument's own row. Pinned on a committed
Orbitrap file under each reader backend, and for the two ways a file can fail
to say: a model the reader does not recognise, and a reader that cannot
answer. A TofDaq file records its digitizer and its acquisition software, not
the instrument, so ``H5Processor`` takes the base processor's None. A props
file written before the field existed reads back with None too.

Here rather than in the Thermo library's suite: the processor imports the
backend, which the library tests run without.
"""

import contextlib
from pathlib import Path

import pytest

from mascope_backend.file_converter.schema import SampleFileProps
from mascope_thermo.lib import thermo_available
from mascope_thermo.processor import RawProcessor
from mascope_tofwerk.processor import H5Processor


_REPO_ROOT = Path(__file__).resolve().parents[5]
#: A committed positive-mode Orbitrap file.
POS_ORBI_FILE = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)


def _processor(path: str = str(POS_ORBI_FILE)) -> RawProcessor:
    processor = RawProcessor.__new__(RawProcessor)
    processor.file_to_process = path
    return processor


@pytest.mark.parametrize("reader", ["thermo", "opentfraw"])
def test_the_committed_file_names_its_model(monkeypatch, reader):
    if reader == "thermo" and not thermo_available():
        pytest.skip(
            "Thermo backend unavailable; set MASCOPE_THERMO_DLL_DIR to the "
            "RawFileReader DLLs to run it."
        )
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", reader)
    assert _processor().instrument_model == "Orbitrap Exploris 120"


class _Reports:
    def __init__(self, details):
        self._details = details

    def instrument_details(self):
        if isinstance(self._details, Exception):
            raise self._details
        return self._details


@pytest.mark.parametrize(
    "details",
    [{"Model": ""}, {"Model": "   "}, {"Model": None}, {}],
    ids=["unrecognised", "blank", "none", "absent"],
)
def test_a_file_that_names_no_model_records_none(monkeypatch, details):
    monkeypatch.setattr(
        RawProcessor,
        "_file_context_manager",
        staticmethod(lambda path: contextlib.nullcontext(_Reports(details))),
    )
    assert _processor().instrument_model is None


def test_a_reader_that_cannot_answer_records_none(monkeypatch, caplog):
    monkeypatch.setattr(
        RawProcessor,
        "_file_context_manager",
        staticmethod(
            lambda path: contextlib.nullcontext(_Reports(RuntimeError("no InstID")))
        ),
    )
    assert _processor().instrument_model is None
    assert "Could not read the instrument model" in caplog.text


def test_a_tofdaq_file_names_no_model():
    assert H5Processor.__new__(H5Processor).instrument_model is None


def test_props_written_before_the_field_read_none():
    assert "instrument_model" not in SampleFileProps.model_json_schema()["required"]
    assert SampleFileProps.model_fields["instrument_model"].default is None
