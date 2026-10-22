"""
Tests: the Samples sheet's PSI-MS columns, and where the model comes from.

The instrument model is read from a sample's raw file, the one place it is
recorded; a file that is not there or cannot be read leaves its cells empty
and the export goes on. The columns end to end are covered by
``tests/integration/api/export/test_batch_spreadsheet_traceability.py``.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from mascope_backend.api.controllers.sample.batches.export import service
from mascope_thermo.thermo import RawFileMetadata


COLUMNS = [
    "Instrument model",
    "Instrument model accession",
    "Ion source",
    "Ion source accession",
    "Mass analyser",
    "Mass analyser accession",
    "Detector",
    "Detector accession",
    "Scan polarity",
    "Scan polarity accession",
]


def test_an_orbitrap_sample_gets_every_term():
    assert service._vocabulary_columns("Orbitrap Exploris 120", "orbi", "-") == {
        "Instrument model": "Orbitrap Exploris 120",
        "Instrument model accession": "MS:1003095",
        "Ion source": "chemical ionization",
        "Ion source accession": "MS:1000071",
        "Mass analyser": "orbitrap",
        "Mass analyser accession": "MS:1000484",
        "Detector": "inductive detector",
        "Detector accession": "MS:1000624",
        "Scan polarity": "negative scan",
        "Scan polarity accession": "MS:1000129",
    }


def test_a_tof_sample_gets_every_term_but_the_model():
    columns = service._vocabulary_columns(None, "tof", "+")
    assert list(columns) == COLUMNS
    assert columns["Instrument model"] is None
    assert columns["Instrument model accession"] is None
    assert columns["Mass analyser accession"] == "MS:1000084"
    assert columns["Detector accession"] == "MS:1000114"
    assert columns["Scan polarity accession"] == "MS:1000130"


def test_a_model_without_a_term_is_named_but_not_given_one():
    columns = service._vocabulary_columns("Orbitrap Exploris", "orbi", "+")
    assert columns["Instrument model"] == "Orbitrap Exploris"
    assert columns["Instrument model accession"] is None


def test_both_polarities_are_listed_positive_first():
    columns = service._vocabulary_columns(None, "orbi", "+-")
    assert columns["Scan polarity"] == "positive scan; negative scan"
    assert columns["Scan polarity accession"] == "MS:1000130; MS:1000129"


def test_nothing_known_leaves_every_cell_empty():
    columns = service._vocabulary_columns(None, None, None)
    assert list(columns) == COLUMNS
    assert all(value is None for value in columns.values())


@pytest.fixture
def reader(monkeypatch):
    """A raw file reader that names a model for every file it is asked about,
    and records which files those were."""
    opened = []

    def metadata_factory(filename):
        opened.append(filename)
        return SimpleNamespace(instrument_details={"Model": "Orbitrap Exploris 120"})

    monkeypatch.setattr(service, "get_sample_file_type", lambda filename: "orbi_raw")
    monkeypatch.setattr(service.m_metadata, "metadata_factory", metadata_factory)
    return opened


def test_only_orbitrap_files_are_opened_once_each(reader):
    samples = [
        {"sample_file_id": "f1", "filename": "a.raw", "instrument_type": "orbi"},
        # A second item of the same file
        {"sample_file_id": "f1", "filename": "a.raw", "instrument_type": "orbi"},
        {"sample_file_id": "f2", "filename": "b.h5", "instrument_type": "tof"},
    ]
    assert service._instrument_models(samples) == {"f1": "Orbitrap Exploris 120"}
    assert reader == ["a.raw"]


def test_a_file_without_its_raw_file_is_not_opened(reader, monkeypatch):
    monkeypatch.setattr(service, "get_sample_file_type", lambda filename: "orbi_zarr")
    assert service._instrument_model("a.raw") is None
    assert reader == []


def test_a_file_that_cannot_be_read_names_no_model(monkeypatch):
    def unreadable(filename):
        raise OSError("truncated")

    monkeypatch.setattr(service, "get_sample_file_type", lambda filename: "orbi_raw")
    monkeypatch.setattr(service.m_metadata, "metadata_factory", unreadable)
    assert service._instrument_model("a.raw") is None


def test_a_reader_that_names_no_model_gives_none(monkeypatch):
    monkeypatch.setattr(service, "get_sample_file_type", lambda filename: "orbi_raw")
    monkeypatch.setattr(
        service.m_metadata,
        "metadata_factory",
        lambda filename: SimpleNamespace(instrument_details={"Model": ""}),
    )
    assert service._instrument_model("a.raw") is None


#: An Orbitrap raw file of the thermo library's own tests.
_RAW_FILE = (
    Path(__file__).resolve().parents[6]
    / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)


def test_the_readers_own_name_for_a_model_has_a_term(monkeypatch):
    """The table is keyed by the names the reader really reports: read a
    real file's model the way the export does, and map it."""
    monkeypatch.setattr(service, "get_sample_file_type", lambda filename: "orbi_raw")
    monkeypatch.setattr(
        service.m_metadata,
        "metadata_factory",
        lambda filename: RawFileMetadata(str(_RAW_FILE)),
    )
    model = service._instrument_model(_RAW_FILE.name)
    columns = service._vocabulary_columns(model, "orbi", "+")
    assert columns["Instrument model"] == "Orbitrap Exploris 120"
    assert columns["Instrument model accession"] == "MS:1003095"
