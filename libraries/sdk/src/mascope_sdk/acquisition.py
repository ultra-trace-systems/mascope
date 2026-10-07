"""The acquisition record: what the program that ran an acquisition says of it.

A raw file says what the instrument measured. It does not say which step of
which sequence asked for it, in which mode, with which settings, on which
installation - the program that controls the instrument knows that, and only
while the acquisition runs. It writes it down as a small JSON document beside
the raw file, a *sidecar*::

    2026.10.07-12h00m00s_sample.raw
    2026.10.07-12h00m00s_sample.raw.mascope.json

A File Agent sends the document with the file's upload, and the server keeps
it on the file. This module is the document's schema, ``mascope-acquisition/1``,
for whichever side reads or writes one::

    from mascope_sdk import acquisition

    record = acquisition.AcquisitionRecord(
        schema="mascope-acquisition/1",
        source_filename="2026.10.07-12h00m00s_sample.raw",
        agent_id=agent_id, sequence_run_id=run_id, step_id=step_id,
        acquisition_id=acquisition_id, ionization="NO3",
    )
    document = acquisition.dump(record)      # the bytes of the sidecar
    acquisition.parse(document)              # what a reader does with them

**Four identifiers are the record.** ``agent_id`` names the installation that
wrote it, ``sequence_run_id`` one run of a sequence, ``step_id`` one step of
one cycle of that run, and ``acquisition_id`` the acquisition of this one
file. All are UUIDs, so they are unique without a registry and read the same
on every server. Everything else is optional: a program writes what it knows.

**Unknown fields are kept.** A field this version does not name is carried
through as it came, at every level, so a program can write more than a reader
knows of and nothing is lost between them. A change that is not an addition
gets a new schema name, ``mascope-acquisition/2``, which this version refuses.

**Every time is an instant.** ISO 8601 with an offset, UTC by convention
(``2026-10-07T12:00:00.000Z``), read off the clock ``clock`` describes. A time
without an offset is refused: nothing downstream could place it.

**``ionization`` names the chemistry to the server.** It is the token of an
ionization mode as that server has it configured - the string that would
otherwise have to appear in the file's name for the file to be bound to the
mode. A server that knows no mode by it falls back on the file's name, as it
does for a file with no record.
"""

import codecs
import json
import os
from typing import Annotated, Any, Literal, NamedTuple
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

# The upload metadata keys a record and the file's SHA-256 travel under.
from ._agents import ACQUISITION_METADATA_KEY as UPLOAD_METADATA_KEY
from ._agents import SHA256_METADATA_KEY


__all__ = [
    "CAPABILITY",
    "MAX_BYTES",
    "SCHEMA",
    "SHA256_METADATA_KEY",
    "SIDECAR_SUFFIX",
    "UPLOAD_METADATA_KEY",
    "AcquisitionError",
    "AcquisitionRecord",
    "Sidecar",
    "dump",
    "parse",
    "read_sidecar",
    "sidecar_path",
]


#: The schema this module reads and writes.
SCHEMA = "mascope-acquisition/1"

#: What a sidecar's name adds to its data file's.
SIDECAR_SUFFIX = ".mascope.json"

#: The largest a record may be, in bytes of UTF-8. It travels in the headers
#: of the request that creates an upload, and those are bounded.
MAX_BYTES = 16 * 1024

#: What a server announces when it keeps a record, and a file's hash, sent
#: with an upload. One that does not announce it is sent neither.
CAPABILITY = "files_accept_acquisition_metadata"


class AcquisitionError(ValueError):
    """A record that cannot be used. The message says why, as a clause that
    reads after "the record is not used, as"."""


#: A SHA-256 as lowercase hex.
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

#: A file's own name, with no folder in front of it.
FileName = Annotated[
    str, StringConstraints(min_length=1, max_length=256, pattern=r"^[^/\\]+$")
]

#: An offset from UTC: ``+03:00``.
UtcOffset = Annotated[str, StringConstraints(pattern=r"^[+-]\d{2}:\d{2}$")]

#: A setting as a mode applied it.
Scalar = bool | int | float | str | None


class Part(BaseModel):
    """A part of a record. Fields it does not name are kept as they came."""

    model_config = ConfigDict(extra="allow")


class Machine(Part):
    """The computer the acquisition was controlled from."""

    #: Its host name, as the agent reports it when it pairs.
    name: str | None = None
    #: Windows' ``MachineGuid``: stable for one installation of Windows,
    #: where a host name can change or be used again.
    windows_machine_guid: str | None = None


class ControlProgram(Part):
    """The program that ran the acquisition and wrote the record."""

    name: str | None = None
    version: str | None = None
    #: The name the program is sold under, where one program has several.
    brand: str | None = None


class ControlUnit(Part):
    """The unit between the control program and the instrument's hardware."""

    fw_version: str | None = None


class Node(Part):
    """One device on the control unit's bus, as it reported itself."""

    #: What the configuration calls it.
    name: str | None = None
    device_name: str | None = None
    hw_version: str | None = None
    fw_version: str | None = None


class SequenceStep(Part):
    """One step of a sequence as it was run."""

    mode: str | None = None
    #: Seconds.
    duration: float | None = Field(None, ge=0)


class Sequence(Part):
    """The sequence the step belongs to, and where in it the step is."""

    name: str | None = None
    #: Which pass through the steps this was, from 0.
    cycle: int | None = Field(None, ge=0)
    #: The step's place among ``steps``, from 0.
    step_index: int | None = Field(None, ge=0)
    #: Whether the sequence starts over when its last step ends.
    loop: bool | None = None
    steps: list[SequenceStep] | None = None
    #: Of the definition as run: its name, ``loop`` and ``steps``.
    hash: Sha256 | None = None


class ConfigurationFile(Part):
    """A configuration file as it was loaded when the run started."""

    #: Relative to the program's configuration folder.
    path: str
    sha256: Sha256


class Mode(Part):
    """The mode the step put the instrument in."""

    name: str | None = None
    #: The mode in full, as the program's configuration holds it.
    definition: dict[str, Any] | None = None
    #: Of ``definition``.
    hash: Sha256 | None = None


class Event(Part):
    """Something that happened to the step while it ran: a pause, a resume,
    a device set up again. ``event`` is the program's own name for it."""

    at: AwareDatetime
    event: str


class ChannelCsv(Part):
    """The file of channel readings the program wrote for the step."""

    name: str | None = None
    sha256: Sha256 | None = None


class Clock(Part):
    """The clock the record's times were read off."""

    #: Whose clock it is, in the program's words.
    source: str | None = None
    #: The IANA name of the zone the computer was set to.
    timezone: str | None = None
    #: That zone's offset from UTC as the step started: ``+03:00``.
    utc_offset: UtcOffset | None = None


class AcquisitionRecord(Part):
    """One raw file's acquisition, as the program that ran it describes it.

    The module's docstring says what the record is for; the fields are named
    here. Only the schema, the file's name and the four identifiers are
    required.
    """

    schema_name: Literal["mascope-acquisition/1"] = Field(alias="schema")
    #: The raw file's name where it was written, which is the sidecar's own
    #: name less :data:`SIDECAR_SUFFIX`.
    source_filename: FileName

    #: The installation that wrote the record, across upgrades and pairings.
    agent_id: UUID
    #: One run of a sequence, with all its cycles.
    sequence_run_id: UUID
    #: One step of one cycle. A step that goes on after a pause keeps it; one
    #: triggered again is another step.
    step_id: UUID
    #: This file's acquisition. Normally one to a step.
    acquisition_id: UUID

    machine: Machine | None = None
    control_program: ControlProgram | None = None
    #: The instrument, by the name the server files its data under.
    instrument: Annotated[str, StringConstraints(max_length=64)] | None = None
    kecu: ControlUnit | None = None
    #: The configured devices that were online.
    nodes: list[Node] | None = None

    sequence: Sequence | None = None
    configuration: list[ConfigurationFile] | None = None

    mode: Mode | None = None
    #: The chemistry, as the token of an ionization mode on the server.
    ionization: (
        Annotated[str, StringConstraints(min_length=1, max_length=256)] | None
    ) = None
    #: When the program asked the instrument to acquire.
    triggered_at: AwareDatetime | None = None
    #: When the instrument answered that it had started.
    acknowledged_at: AwareDatetime | None = None
    step_started_at: AwareDatetime | None = None
    step_finished_at: AwareDatetime | None = None
    #: Seconds the step waited for the mode to settle before acquiring.
    settle_time: float | None = Field(None, ge=0)
    #: What the mode set, as ``device.setting`` to value.
    setpoints: dict[str, Scalar] | None = None
    heaters: dict[str, Scalar] | None = None
    events: list[Event] | None = None

    channel_csv: ChannelCsv | None = None
    clock: Clock | None = None


def _reasons(error: ValidationError) -> str:
    """What is wrong with a document, as a clause: the first few faults."""
    faults = error.errors(include_url=False, include_input=False)
    said = [
        f"{'.'.join(str(part) for part in fault['loc']) or 'it'}: {fault['msg']}"
        for fault in faults[:3]
    ]
    more = len(faults) - len(said)
    return "; ".join(said) + (f"; and {more} more" if more else "")


def parse(document: bytes | str) -> AcquisitionRecord:
    """Read a record from the document that carries it.

    What every reader does with one, so that a program writing a sidecar, the
    agent sending it and the server keeping it agree on what a record is.

    :param document: The JSON document, as UTF-8 bytes or as text
    :type document: bytes | str
    :raises AcquisitionError: When it is too large, is not a JSON object of
        this schema, or a field is not what the schema says
    :return: The record
    :rtype: AcquisitionRecord
    """
    raw = document.encode("utf-8") if isinstance(document, str) else bytes(document)
    if len(raw) > MAX_BYTES:
        raise AcquisitionError(
            f"it is {len(raw)} bytes, and a record may be {MAX_BYTES} at most"
        )
    try:
        text = raw.decode("utf-8")
        content = json.loads(text)
    except UnicodeDecodeError as e:
        raise AcquisitionError("it is not UTF-8 text") from e
    except ValueError as e:
        raise AcquisitionError(f"it is not JSON ({e})") from e
    if not isinstance(content, dict):
        raise AcquisitionError("it is not a JSON object")
    if content.get("schema") != SCHEMA:
        # Said on its own: a record of a later schema is the one fault a
        # reader will meet without anybody having made a mistake.
        raise AcquisitionError(
            f"its schema is {content.get('schema')!r}, and this reads {SCHEMA!r}"
        )
    try:
        # Strict, as JSON: a UUID and a time are strings and nothing else is,
        # and a number is not read out of a string.
        return AcquisitionRecord.model_validate_json(text, strict=True)
    except ValidationError as e:
        raise AcquisitionError(_reasons(e)) from e


def dump(record: AcquisitionRecord) -> bytes:
    """Write a record as the document that carries it.

    Compact, with the fields the record does not have left out and the ones
    this version does not name kept.

    :param record: The record
    :type record: AcquisitionRecord
    :raises AcquisitionError: When the document would be larger than a record
        may be
    :return: The JSON document, as UTF-8 bytes
    :rtype: bytes
    """
    document = record.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    if len(document) > MAX_BYTES:
        raise AcquisitionError(
            f"it is {len(document)} bytes, and a record may be {MAX_BYTES} at most"
        )
    return document


class Sidecar(NamedTuple):
    """A sidecar that was read: the record, and the document it was read from."""

    record: AcquisitionRecord
    #: The document as it goes with the upload: UTF-8, with no byte order mark.
    document: bytes


def sidecar_path(data_file: str | os.PathLike) -> str:
    """Where the sidecar of a data file is: beside it, under its name and
    :data:`SIDECAR_SUFFIX`.

    :param data_file: Path of the data file
    :type data_file: str | os.PathLike
    :return: Path of its sidecar, whether or not there is one
    :rtype: str
    """
    return os.fspath(data_file) + SIDECAR_SUFFIX


def read_sidecar(data_file: str | os.PathLike) -> Sidecar | None:
    """Read the sidecar of a data file, if it has one.

    :param data_file: Path of the data file
    :type data_file: str | os.PathLike
    :raises AcquisitionError: When there is a sidecar and it cannot be used:
        it cannot be read, is not a record, or is the record of another file
    :return: The record and its document, or None when the file has no sidecar
    :rtype: Sidecar | None
    """
    try:
        with open(sidecar_path(data_file), "rb") as file:
            # One byte more than a record may be, so that parse() sees a
            # sidecar that is too large as too large.
            raw = file.read(MAX_BYTES + len(codecs.BOM_UTF8) + 1)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise AcquisitionError(f"it could not be read ({e})") from e
    # Windows tools write the mark as a matter of course; JSON has none.
    raw = raw.removeprefix(codecs.BOM_UTF8)
    record = parse(raw)
    name = os.path.basename(os.fspath(data_file))
    if os.path.normcase(record.source_filename) != os.path.normcase(name):
        raise AcquisitionError(
            f"it is the record of {record.source_filename!r}, not of this file"
        )
    return Sidecar(record, raw)
