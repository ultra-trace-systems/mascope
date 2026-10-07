"""The acquisition record's schema, as both ends of an upload read it.

Hermetic. What is pinned is what the two ends have to agree on: which
documents are a record and which are not, that what a reader does not know is
kept, and where a file's sidecar is.
"""

import base64
import json
import os
import uuid

import pytest
import requests

from mascope_sdk import _agents, acquisition
from mascope_sdk.acquisition import AcquisitionError, AcquisitionRecord


IDS = {
    "agent_id": "3f0e8f0c-5d0b-4c7e-9a43-0d8f6c1b2a10",
    "sequence_run_id": "0199b6a0-7c00-7000-8000-000000000001",
    "step_id": "0199b6a0-7c00-7000-8000-000000000002",
    "acquisition_id": "0199b6a0-7c00-7000-8000-000000000003",
}


def minimal(**changes) -> dict:
    """The least a record may hold, with ``changes`` laid over it."""
    return {
        "schema": "mascope-acquisition/1",
        "source_filename": "run_0042.raw",
        **IDS,
        **changes,
    }


def document(**changes) -> bytes:
    return json.dumps(minimal(**changes)).encode("utf-8")


FULL = minimal(
    machine={"name": "LAB-PC-1", "windows_machine_guid": str(uuid.UUID(int=7))},
    control_program={"name": "control", "version": "6.0.0", "brand": "A"},
    instrument="Orbi-Lab2",
    kecu={"fw_version": "2.4.1"},
    nodes=[
        {
            "name": "ion-source",
            "device_name": "IS-1",
            "hw_version": "C",
            "fw_version": "1.9.0",
        }
    ],
    sequence={
        "name": "day",
        "cycle": 3,
        "step_index": 0,
        "loop": True,
        "steps": [{"mode": "IS1-", "duration": 600}, {"mode": "Pause", "duration": 30}],
        "hash": "a" * 64,
    },
    configuration=[{"path": "configs/modes.yaml", "sha256": "b" * 64}],
    mode={
        "name": "IS1-",
        "definition": {"voltages": {"lens": -40.5}},
        "hash": "c" * 64,
    },
    ionization="NO3",
    triggered_at="2026-10-07T12:00:00.000Z",
    acknowledged_at="2026-10-07T12:00:01.250Z",
    step_started_at="2026-10-07T11:59:30.000Z",
    step_finished_at="2026-10-07T12:10:00.000Z",
    settle_time=30,
    setpoints={"ion-source.lens": -40.5, "ion-source.on": True, "flow.mode": "auto"},
    heaters={"desorber.target": 200},
    events=[{"at": "2026-10-07T12:03:00.000Z", "event": "paused", "reason": "link"}],
    channel_csv={"name": "day_0003_00.csv", "sha256": "d" * 64},
    clock={
        "source": "instrument-pc",
        "timezone": "Europe/Helsinki",
        "utc_offset": "+03:00",
    },
)


# ---------------------------------------------------------------------------
# What is a record
# ---------------------------------------------------------------------------


def test_the_least_a_record_may_hold():
    record = acquisition.parse(document())

    assert record.source_filename == "run_0042.raw"
    assert record.acquisition_id == uuid.UUID(IDS["acquisition_id"])
    assert record.ionization is None


def test_every_field_the_schema_names_is_read():
    record = acquisition.parse(json.dumps(FULL))

    assert record.ionization == "NO3"
    assert record.sequence.steps[1].mode == "Pause"
    assert record.mode.definition == {"voltages": {"lens": -40.5}}
    assert record.setpoints == {
        "ion-source.lens": -40.5,
        "ion-source.on": True,
        "flow.mode": "auto",
    }
    assert record.events[0].at.utcoffset().total_seconds() == 0
    assert record.settle_time == 30.0


@pytest.mark.parametrize(
    "missing",
    ["schema", "source_filename", *IDS],
)
def test_the_schema_the_name_and_the_four_ids_are_required(missing):
    content = minimal()
    del content[missing]

    with pytest.raises(AcquisitionError):
        acquisition.parse(json.dumps(content))


def test_a_field_this_version_does_not_name_is_kept_at_every_level():
    content = minimal(
        later="kept",
        mode={"name": "IS1-", "reagent": "nitrate"},
        nodes=[{"name": "ion-source", "serial": "S-17"}],
    )

    record = acquisition.parse(json.dumps(content))
    written = json.loads(acquisition.dump(record))

    assert written["later"] == "kept"
    assert written["mode"]["reagent"] == "nitrate"
    assert written["nodes"][0]["serial"] == "S-17"


def test_what_is_written_reads_back_as_the_same_record():
    record = acquisition.parse(json.dumps(FULL))

    assert acquisition.parse(acquisition.dump(record)) == record


def test_a_record_built_by_name_is_written_under_the_schemas_own_key():
    record = AcquisitionRecord(
        schema="mascope-acquisition/1", source_filename="x.raw", **IDS
    )

    written = json.loads(acquisition.dump(record))

    assert written["schema"] == "mascope-acquisition/1"
    # Left out, not written as null: a reader tells "not said" by absence.
    assert "ionization" not in written


# ---------------------------------------------------------------------------
# What is not
# ---------------------------------------------------------------------------


def test_a_record_of_a_later_schema_is_refused_by_name():
    with pytest.raises(AcquisitionError) as refused:
        acquisition.parse(document(schema="mascope-acquisition/2"))

    assert "'mascope-acquisition/2'" in str(refused.value)
    assert "'mascope-acquisition/1'" in str(refused.value)


@pytest.mark.parametrize(
    "not_a_record, reason",
    [
        (b"\xff\xfe{}", "not UTF-8"),
        (b"{not json", "not JSON"),
        (b"[]", "not a JSON object"),
        (b'"mascope-acquisition/1"', "not a JSON object"),
    ],
)
def test_a_document_that_is_not_a_json_object_is_refused(not_a_record, reason):
    with pytest.raises(AcquisitionError, match=reason):
        acquisition.parse(not_a_record)


def test_a_record_larger_than_the_limit_is_refused():
    padded = document(setpoints={"x": "p" * acquisition.MAX_BYTES})

    with pytest.raises(AcquisitionError, match="16384 at most"):
        acquisition.parse(padded)


def test_a_record_of_exactly_the_limit_is_read():
    spare = acquisition.MAX_BYTES - len(document(later=""))

    assert len(document(later="p" * spare)) == acquisition.MAX_BYTES
    acquisition.parse(document(later="p" * spare))


@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": "not-a-uuid"},
        {"acquisition_id": 12},
        # A time with no offset is no instant.
        {"triggered_at": "2026-10-07T12:00:00"},
        # Nor is a number one: seconds since when is not said.
        {"triggered_at": 1791374400},
        {"settle_time": -1},
        {"settle_time": "30"},
        {"source_filename": "folder/run_0042.raw"},
        {"source_filename": "folder\\run_0042.raw"},
        {"source_filename": ""},
        {"ionization": ""},
        {"sequence": {"hash": "A" * 64}},
        {"configuration": [{"path": "configs/modes.yaml"}]},
        {"events": [{"event": "paused"}]},
        {"clock": {"utc_offset": "3h"}},
        # Flat: a setting is a value, not a structure.
        {"setpoints": {"ion-source": {"lens": -40.5}}},
    ],
)
def test_a_field_that_is_not_what_the_schema_says_is_refused(changes):
    with pytest.raises(AcquisitionError):
        acquisition.parse(document(**changes))


def test_the_reason_names_the_field():
    with pytest.raises(AcquisitionError) as refused:
        acquisition.parse(document(sequence={"cycle": -1}, step_id="x"))

    assert "sequence.cycle" in str(refused.value)
    assert "step_id" in str(refused.value)


def test_a_record_too_large_to_write_is_not_written():
    record = AcquisitionRecord(
        schema="mascope-acquisition/1",
        source_filename="x.raw",
        setpoints={"x": "p" * acquisition.MAX_BYTES},
        **IDS,
    )

    with pytest.raises(AcquisitionError, match="at most"):
        acquisition.dump(record)


# ---------------------------------------------------------------------------
# The sidecar
# ---------------------------------------------------------------------------


@pytest.fixture
def data_file(tmp_path):
    path = tmp_path / "run_0042.raw"
    path.write_bytes(b"raw-bytes")
    return path


def test_a_sidecar_is_beside_its_file_under_its_whole_name(data_file):
    assert acquisition.sidecar_path(data_file) == f"{data_file}.mascope.json"


def test_a_file_with_no_sidecar_has_none(data_file):
    assert acquisition.read_sidecar(data_file) is None


def test_a_sidecar_is_read_with_the_document_it_was_read_from(data_file):
    written = json.dumps(FULL, indent=2).encode("utf-8")
    (data_file.parent / "run_0042.raw.mascope.json").write_bytes(written)

    sidecar = acquisition.read_sidecar(str(data_file))

    assert sidecar.record.ionization == "NO3"
    # As it was written: the same document goes with every attempt.
    assert sidecar.document == written


def test_a_byte_order_mark_is_not_part_of_the_document(data_file):
    (data_file.parent / "run_0042.raw.mascope.json").write_bytes(
        b"\xef\xbb\xbf" + document()
    )

    assert acquisition.read_sidecar(data_file).document == document()


def test_the_record_of_another_file_is_not_this_files(data_file):
    (data_file.parent / "run_0042.raw.mascope.json").write_bytes(
        document(source_filename="run_0041.raw")
    )

    with pytest.raises(AcquisitionError, match="'run_0041.raw'"):
        acquisition.read_sidecar(data_file)


def test_a_sidecar_that_is_too_large_is_refused_as_too_large(data_file):
    (data_file.parent / "run_0042.raw.mascope.json").write_bytes(
        document(later="p" * (2 * acquisition.MAX_BYTES))
    )

    with pytest.raises(AcquisitionError, match="at most"):
        acquisition.read_sidecar(data_file)


def test_a_sidecar_that_cannot_be_read_is_refused(data_file):
    # A folder under the sidecar's name: there, and not a document.
    os.mkdir(f"{data_file}.mascope.json")

    with pytest.raises(AcquisitionError, match="could not be read"):
        acquisition.read_sidecar(data_file)


# ---------------------------------------------------------------------------
# With the upload
# ---------------------------------------------------------------------------


def _created(monkeypatch) -> dict:
    """Stand in for the server; returns the creation request's metadata."""
    seen: dict = {}

    def fake_post(url, headers, verify, timeout):
        for item in headers["Upload-Metadata"].split(","):
            key, value = item.split(" ", 1)
            seen[key] = base64.b64decode(value)
        response = requests.Response()
        response.status_code = 201
        response.headers["Location"] = f"{url}abc123"
        return response

    def fake_patch(url, data, headers, verify, timeout):
        response = requests.Response()
        response.status_code = 204
        return response

    monkeypatch.setattr(_agents.requests, "post", fake_post)
    monkeypatch.setattr(_agents.requests, "patch", fake_patch)
    return seen


def test_the_record_and_the_hash_go_with_the_request_that_creates_the_upload(
    monkeypatch, data_file
):
    seen = _created(monkeypatch)
    record = json.dumps(FULL, indent=2).encode("utf-8")

    _agents.api_post_file_tus(
        "http://testserver", "tok", str(data_file), acquisition=record, sha256="e" * 64
    )

    # Byte for byte: the server reads the document the program wrote.
    assert seen[acquisition.UPLOAD_METADATA_KEY] == record
    assert seen[acquisition.SHA256_METADATA_KEY] == b"e" * 64


def test_an_upload_given_neither_sends_neither(monkeypatch, data_file):
    seen = _created(monkeypatch)

    _agents.api_post_file_tus("http://testserver", "tok", str(data_file))

    assert "acquisition" not in seen
    assert "sha256" not in seen
