"""The acquisition record's schema, as both ends of an upload read it.

Hermetic. What is pinned is what the two ends have to agree on: which
documents are a record and which are not, that what a reader does not know is
kept, and where a file's sidecar is.
"""

import base64
import json
import os
import re
import uuid

import pytest
import requests

from mascope_sdk import _agents, acquisition
from mascope_sdk.acquisition import AcquisitionError, AcquisitionRecord
from mascope_sdk.exceptions import MascopeAPIError, ValidationError


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
# What a lenient reader lets through
# ---------------------------------------------------------------------------
#
# A document this version takes is a record for good, so each of these is
# refused now: none could be refused later without being a new schema.


def _with(member: str) -> str:
    """The least record, with one more member written out by hand."""
    return json.dumps(minimal())[:-1] + ", " + member + "}"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    "member",
    [
        '"setpoints": {"a.b": %s}',
        '"heaters": {"desorber.target": %s}',
        '"mode": {"definition": {"voltages": [1.5, %s]}}',
        '"settle_time": %s',
        '"sequence": {"steps": [{"duration": %s}]}',
        '"later": {"reading": %s}',
    ],
)
def test_a_number_json_does_not_have_is_refused_wherever_it_stands(constant, member):
    """In a field of the schema, in one it does not name, at any depth: a
    database that stores JSON takes none of them."""
    with pytest.raises(AcquisitionError, match=re.escape(f"it holds {constant}")):
        acquisition.parse(_with(member % constant))


@pytest.mark.parametrize("written", ["1e999", "-1e999", "1e400"])
@pytest.mark.parametrize(
    "member",
    [
        '"setpoints": {"a.b": %s}',
        '"mode": {"definition": {"voltages": [1.5, %s]}}',
        '"settle_time": %s',
        '"later": {"reading": %s}',
    ],
)
def test_a_number_too_large_to_be_one_is_refused(member, written):
    """It is JSON, and it is read as infinity, which nothing after this can
    write down again: not the next program to encode the record, and not a
    database that stores JSON."""
    with pytest.raises(AcquisitionError, match="too large to be read as anything"):
        acquisition.parse(_with(member % written))


def test_a_large_number_that_is_one_is_read():
    record = acquisition.parse(_with('"setpoints": {"a.b": 1e308, "c.d": -1e308}'))

    assert record.setpoints == {"a.b": 1e308, "c.d": -1e308}


def test_python_writes_such_a_number_without_being_asked():
    """Which is how one gets into a sidecar: a failed reading, and json.dumps."""
    written = json.dumps(minimal(setpoints={"a.b": float("nan")}))
    assert "NaN" in written

    with pytest.raises(AcquisitionError, match="NaN"):
        acquisition.parse(written)


@pytest.mark.parametrize(
    "written",
    [
        "1791374400",  # read as seconds since 1970 by the parser underneath
        "0",
        "1791374400.5",
        "2026-10-07 12:00:00Z",
        "2026-10-07T12:00Z",
        "2026-10-07t12:00:00z",
        "2026-10-07T12:00:00+0300",
        "20261007T120000Z",
        "2026-10-07",
    ],
)
@pytest.mark.parametrize("field", acquisition.TIME_FIELDS)
def test_a_time_is_written_one_way(field, written):
    with pytest.raises(AcquisitionError) as refused:
        acquisition.parse(document(**{field: written}))

    assert f"{field}: " in str(refused.value)


def test_a_time_inside_an_event_is_written_the_same_way():
    with pytest.raises(AcquisitionError, match=r"events\.1\.at: a time is written as"):
        acquisition.parse(
            document(
                events=[
                    {"at": "2026-10-07T12:03:00.000Z", "event": "paused"},
                    {"at": "1791374400", "event": "resumed"},
                ]
            )
        )


@pytest.mark.parametrize(
    "written",
    [
        "2026-10-07T12:00:00Z",
        "2026-10-07T12:00:00.000Z",
        "2026-10-07T15:00:00.123456+03:00",
        "2026-10-07T07:00:00-05:00",
    ],
)
def test_a_date_time_with_its_offset_is_a_time(written):
    record = acquisition.parse(document(triggered_at=written, step_started_at=written))

    assert record.triggered_at.isoformat().startswith("2026-10-07T")
    assert record.triggered_at.utcoffset() is not None


@pytest.mark.parametrize(
    "spelling",
    [
        "0199B6A0-7C00-7000-8000-000000000003",
        "0199b6a07c0070008000000000000003",
        "{0199b6a0-7c00-7000-8000-000000000003}",
        "urn:uuid:0199b6a0-7c00-7000-8000-000000000003",
    ],
)
@pytest.mark.parametrize("field", acquisition.ID_FIELDS)
def test_an_identifier_has_one_spelling(field, spelling):
    """Each of these is the same UUID to the parser underneath. Kept as
    written, it would be a second spelling beside the column made from it."""
    with pytest.raises(AcquisitionError) as refused:
        acquisition.parse(document(**{field: spelling}))

    assert f"{field}: a UUID is written in lowercase with its hyphens" in str(
        refused.value
    )


def _of_format(schema: dict, wanted: str) -> set[str]:
    """The properties of a JSON schema object that are of one string format."""
    return {
        name
        for name, member in schema.get("properties", {}).items()
        if any(
            option.get("format") == wanted
            for option in (member, *member.get("anyOf", []))
        )
    }


def test_every_identifier_and_time_of_the_model_is_held_to_its_spelling():
    """The spellings are kept by two lists beside the model, since the model
    cannot keep them itself. A field added to the one and not to the other
    would be read as loosely as the parser underneath reads."""
    schema = AcquisitionRecord.model_json_schema()
    parts = schema["$defs"]

    assert _of_format(schema, "uuid") == set(acquisition.ID_FIELDS)
    assert _of_format(schema, "date-time") == set(acquisition.TIME_FIELDS)
    assert {
        name: _of_format(part, "date-time")
        for name, part in parts.items()
        if _of_format(part, "date-time")
    } == {"Event": {"at"}}
    assert not any(_of_format(part, "uuid") for part in parts.values())


@pytest.mark.parametrize(
    "member, twice",
    [
        ('"ionization": "NO3", "ionization": "BR"', "ionization"),
        ('"mode": {"name": "IS1-", "name": "IS1+"}', "name"),
        ('"later": [{"a": 1, "a": 1}]', "a"),
    ],
)
def test_a_key_is_there_once(member, twice):
    """Two readers of such a document need not pick the same one."""
    with pytest.raises(AcquisitionError, match=f"it has the key '{twice}' twice"):
        acquisition.parse(_with(member))


def _nested(levels: int) -> str:
    """A member whose value nests ``levels`` deep below the record itself."""
    return '"later": ' + "[" * levels + "]" * levels


def test_a_record_nests_so_deep_and_no_deeper():
    acquisition.parse(_with(_nested(acquisition.MAX_DEPTH - 1)))

    with pytest.raises(AcquisitionError, match="it nests more than 32 levels deep"):
        acquisition.parse(_with(_nested(acquisition.MAX_DEPTH)))


def test_brackets_alone_do_not_get_past_the_reader():
    """2 KB of them. Whether the JSON reader survives that many depends on
    the Python, down to its patch release; the answer does not."""
    with pytest.raises(AcquisitionError, match="it nests more than 32 levels deep"):
        acquisition.parse(_with(_nested(2000)))


def test_a_reader_that_gives_out_is_a_record_that_is_too_deep(monkeypatch):
    """Where the JSON reader does not survive the brackets it says so with a
    RecursionError, which is no ValueError and would get past a handler for
    one. Raised here by hand, since which Pythons raise it is not ours to
    say."""

    def gives_out(text, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(acquisition.json, "loads", gives_out)

    with pytest.raises(AcquisitionError, match="it nests more than 32 levels deep"):
        acquisition.parse(document())


def test_nesting_is_counted_through_objects_and_lists_alike():
    deep = {"a": [{"b": [{"c": {}}]}]}
    for _ in range(9):
        deep = {"a": [{"b": deep}]}

    with pytest.raises(AcquisitionError, match="it nests more than 32 levels deep"):
        acquisition.parse(document(mode={"definition": deep}))


# ---------------------------------------------------------------------------
# What is written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize(
    "held",
    [
        lambda number: {"setpoints": {"a.b": number}},
        lambda number: {"mode": {"definition": {"voltages": [number]}}},
        lambda number: {"later": {"reading": number}},
    ],
)
def test_a_record_that_holds_a_number_json_cannot_write_is_not_written(held, number):
    """Whatever the serializer underneath would have made of it: one version
    writes NaN, another null, and neither is what the record held."""
    record = AcquisitionRecord(
        schema="mascope-acquisition/1", source_filename="x.raw", **IDS, **held(number)
    )

    with pytest.raises(AcquisitionError, match="not finite"):
        acquisition.dump(record)


def test_nothing_is_written_that_would_not_be_read_back():
    deep: dict = {}
    for _ in range(acquisition.MAX_DEPTH):
        deep = {"deeper": deep}
    record = AcquisitionRecord(
        schema="mascope-acquisition/1",
        source_filename="x.raw",
        mode={"definition": deep},
        **IDS,
    )

    with pytest.raises(AcquisitionError, match="it nests more than 32 levels deep"):
        acquisition.dump(record)


def test_a_null_that_was_read_is_written():
    """Kept as it came has no exception for a field whose value is null."""
    content = minimal(
        later=None,
        ionization=None,
        mode={"name": "IS1-", "reagent": None, "definition": {"gas": None}},
    )

    written = json.loads(acquisition.dump(acquisition.parse(json.dumps(content))))

    assert written == content


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


def test_a_sidecar_of_exactly_the_limit_is_read_whole_behind_its_mark(data_file):
    """The mark is not part of the document, so it is not part of its size."""
    spare = acquisition.MAX_BYTES - len(document(later=""))
    full = document(later="p" * spare)
    (data_file.parent / "run_0042.raw.mascope.json").write_bytes(b"\xef\xbb\xbf" + full)

    assert acquisition.read_sidecar(data_file).document == full


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


def test_a_conflict_when_the_upload_is_created_is_not_a_rejected_request(
    monkeypatch, data_file
):
    """An agent sends a file again without its record when the request was
    rejected for what it is. A 409 is not that, which leaves a server one way
    to refuse an upload outright and not be sent the file recordless."""
    conflict = requests.Response()
    conflict.status_code = 409
    conflict._content = b'{"detail": "already here"}'
    monkeypatch.setattr(_agents.requests, "post", lambda *a, **k: conflict)

    with pytest.raises(MascopeAPIError) as refused:
        _agents.api_post_file_tus(
            "http://testserver", "tok", str(data_file), acquisition=document()
        )

    assert not isinstance(refused.value, ValidationError)
    assert refused.value.status_code == 409


def test_an_upload_given_neither_sends_neither(monkeypatch, data_file):
    seen = _created(monkeypatch)

    _agents.api_post_file_tus("http://testserver", "tok", str(data_file))

    assert "acquisition" not in seen
    assert "sha256" not in seen
