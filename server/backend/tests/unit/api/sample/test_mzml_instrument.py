"""
Tests: the instrument an mzML export writes, by the model a raw file names.

The terms are the vocabulary's, the batch spreadsheet's, not the reader's own
model table; a model the vocabulary does not know is the ontology's Thermo
parent term, since mzML asks for a model term. What the file then holds is
covered by ``tests/integration/api/workspace/test_file_mzml_export.py``.
"""

import pytest

from mascope_backend.api.new.file import service
from mascope_thermo.mzml import CvParam


@pytest.fixture
def model(monkeypatch):
    """Make every raw file name the model a test sets."""
    named = {}

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def instrument_details(self):
            return {"Model": named["model"]} if "model" in named else {}

    monkeypatch.setattr(service, "open_backend", lambda _path: Reader())

    def set_model(value):
        named["model"] = value

    return set_model


@pytest.mark.parametrize(
    ("name", "accession", "term_name"),
    [
        ("Orbitrap Exploris 120", "MS:1003095", "Orbitrap Exploris 120"),
        # The reader's spelling of a model is its PSI-MS name in the file
        ("Orbitrap XL", "MS:1000556", "LTQ Orbitrap XL"),
    ],
)
def test_a_known_model_is_the_vocabularys_term(model, name, accession, term_name):
    model(name)
    instrument = service._instrument_configuration("a.raw")

    assert instrument.model == CvParam(accession, term_name)
    assert instrument.source == CvParam("MS:1000071", "chemical ionization")
    assert instrument.analyser == CvParam("MS:1000484", "orbitrap")
    assert instrument.detector == CvParam("MS:1000624", "inductive detector")


@pytest.mark.parametrize("name", ["Orbitrap Exploris", "", None])
def test_an_unknown_model_is_the_thermo_parent_term(model, name):
    if name is not None:
        model(name)
    instrument = service._instrument_configuration("a.raw")

    assert instrument.model == CvParam(
        "MS:1000483", "Thermo Fisher Scientific instrument model"
    )
    assert instrument.detector == CvParam("MS:1000624", "inductive detector")
