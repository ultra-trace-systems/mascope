"""Unit tests: an upload's acquisition record and hash, as the server takes them.

Pure functions over the upload's metadata, as the tus router hands it over:
every value a string it decoded. What is pinned is what is kept, what is
refused and with which words, and what is passed over without a refusal.
"""

import hashlib
import json

import pytest

from mascope_backend.acquisition_record import (
    ID_COLUMNS,
    declared_ionization,
    file_sha256,
    record_from_upload,
    record_ids,
    reported_sha256,
)
from mascope_sdk import acquisition


RECORD = {
    "schema": "mascope-acquisition/1",
    "source_filename": "run_0042.raw",
    "agent_id": "3f0e8f0c-5d0b-4c7e-9a43-0d8f6c1b2a10",
    "sequence_run_id": "0199b6a0-7c00-7000-8000-000000000001",
    "step_id": "0199b6a0-7c00-7000-8000-000000000002",
    "acquisition_id": "0199b6a0-7c00-7000-8000-000000000003",
    "ionization": "NO3",
}


def _upload(record=RECORD, **metadata) -> dict:
    """An upload's metadata, as an agent sends it with a record."""
    return {
        "filename": "Orbi-Lab2_run_0042.raw",
        "source_filename": "run_0042.raw",
        **({} if record is None else {"acquisition": json.dumps(record)}),
        **metadata,
    }


def test_an_upload_with_no_record_has_none():
    assert record_from_upload(_upload(record=None)) is None
    assert record_from_upload(_upload(record=None, acquisition="")) is None


def test_the_record_is_kept_as_it_was_sent():
    """Fields this version does not know included: the document is the
    control program's statement, not the server's reading of it."""
    sent = {**RECORD, "later": {"kept": True}, "mode": {"name": "IS1-", "reagent": "x"}}

    assert record_from_upload(_upload(sent)) == sent


def test_the_record_of_another_file_is_refused():
    with pytest.raises(ValueError) as refused:
        record_from_upload(_upload({**RECORD, "source_filename": "run_0041.raw"}))

    assert "'run_0041.raw'" in str(refused.value)
    assert "'run_0042.raw'" in str(refused.value)


def test_the_file_is_known_by_the_name_it_had_where_it_was_written():
    """Not by the name it is uploaded under, which carries what the agent's
    configuration adds to it."""
    assert record_from_upload(_upload()) == RECORD
    # An upload that reports no other name is its own.
    assert record_from_upload(
        {"filename": "run_0042.raw", "acquisition": json.dumps(RECORD)}
    )


def test_a_name_that_differs_only_in_case_is_the_same_file():
    """The agent compares them as a Windows file system does."""
    assert record_from_upload(_upload(source_filename="RUN_0042.RAW")) == RECORD


@pytest.mark.parametrize(
    "document",
    [
        "{not json",
        "[]",
        json.dumps({**RECORD, "schema": "mascope-acquisition/2"}),
        json.dumps({**RECORD, "acquisition_id": "not-a-uuid"}),
        json.dumps({**RECORD, "later": "p" * acquisition.MAX_BYTES}),
    ],
)
def test_a_document_that_is_not_a_record_is_refused(document):
    with pytest.raises(ValueError):
        record_from_upload(_upload(record=None, acquisition=document))


def test_the_identifiers_are_spelled_one_way_for_the_columns():
    """A column compares what it stores, and a UUID has several spellings."""
    document = {**RECORD, "acquisition_id": "0199B6A07C0070008000000000000003"}

    ids = record_ids(document)

    assert tuple(ids) == ID_COLUMNS
    assert ids["acquisition_id"] == "0199b6a0-7c00-7000-8000-000000000003"
    assert ids["agent_id"] == RECORD["agent_id"]


def test_a_document_that_is_no_record_has_no_identifiers():
    with pytest.raises(ValueError):
        record_ids({**RECORD, "step_id": None})


@pytest.mark.parametrize(
    "document, declared",
    [
        (RECORD, "NO3"),
        ({**RECORD, "ionization": None}, None),
        ({k: v for k, v in RECORD.items() if k != "ionization"}, None),
        ({**RECORD, "ionization": ""}, None),
        ({**RECORD, "ionization": ["NO3"]}, None),
        (None, None),
    ],
)
def test_the_chemistry_a_record_names(document, declared):
    assert declared_ionization(document) == declared


@pytest.mark.parametrize(
    "sent, reported",
    [
        ("a" * 64, "a" * 64),
        (("AB" * 32), "ab" * 32),
        (f"  {'c' * 64}\n", "c" * 64),
        ("a" * 63, None),
        ("g" * 64, None),
        ("", None),
        (None, None),
    ],
)
def test_only_a_sha256_is_a_reported_hash(sent, reported):
    metadata = {} if sent is None else {"sha256": sent}

    assert reported_sha256(metadata) == reported


def test_the_hash_is_of_the_files_content(tmp_path):
    path = tmp_path / "run_0042.raw"
    content = bytes(range(256)) * 9_000
    path.write_bytes(content)

    assert file_sha256(str(path)) == hashlib.sha256(content).hexdigest()
