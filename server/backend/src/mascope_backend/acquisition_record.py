"""
An upload's acquisition record and hash, as the server takes them.

The program that ran an acquisition can say what its file is - which step of
which run acquired it, under which chemistry - in a JSON document the
uploading agent sends with the file, and the agent sends the file's SHA-256
beside it (``docs/dev/acquisition_sidecar.md``). ``mascope_sdk.acquisition``
is the document's schema, shared with whatever wrote it. This module is what
the server does with the two at its door.

**Neither may cost a file its place on the server.** A record is refused
where the uploader can still act on the refusal: when the upload is created,
before any bytes move. From then on nothing here raises for a record's sake.
A record that turns out unusable later is dropped with a line in the log and
the file is kept.

Pure, and its own module, because three layers read it: the upload routes,
the controller that registers a file, and the pipeline that binds one.
"""

import hashlib
import json
import os
import re

from mascope_sdk import acquisition


#: The record's identifiers that are columns of their own on a sample file.
ID_COLUMNS: tuple[str, ...] = (
    "acquisition_id",
    "step_id",
    "sequence_run_id",
    "agent_id",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def record_from_upload(metadata: dict) -> dict | None:
    """The acquisition record an upload carries, as the document to keep.

    Kept as it was sent, fields this version does not know included, rather
    than as the model read it back: the document is the control program's
    statement, and a later reader may know more of it than this one does.

    :param metadata: The upload's metadata, as the tus router decoded it.
    :type metadata: dict
    :raises ValueError: The upload carries a record that cannot be kept. The
        message is a clause that reads after "is not kept, as".
    :return: The record, or None when the upload carries none.
    :rtype: dict | None
    """
    document = metadata.get(acquisition.UPLOAD_METADATA_KEY)
    if not document:
        return None
    record = acquisition.parse(document)
    uploaded = os.path.basename(
        metadata.get("source_filename") or metadata.get("filename") or ""
    )
    # Folded: the agent compares the two as its own file system does, and a
    # Windows one does not tell cases apart.
    if record.source_filename.casefold() != uploaded.casefold():
        raise acquisition.AcquisitionError(
            f"it is the record of {record.source_filename!r}, and the upload "
            f"is {uploaded!r}"
        )
    return json.loads(document)


def record_ids(document: dict) -> dict[str, str]:
    """A record's identifiers, each in the one spelling a column compares.

    :param document: The record.
    :type document: dict
    :raises ValueError: The document is not a record.
    :return: The value of each of :data:`ID_COLUMNS`, as a canonical UUID.
    :rtype: dict[str, str]
    """
    record = acquisition.AcquisitionRecord.model_validate_json(
        json.dumps(document), strict=True
    )
    return {name: str(getattr(record, name)) for name in ID_COLUMNS}


def declared_ionization(document: dict | None) -> str | None:
    """The chemistry a record names, or None when it names none.

    :param document: The record, or None for a file that has none.
    :type document: dict | None
    :rtype: str | None
    """
    declared = (document or {}).get("ionization")
    return declared if isinstance(declared, str) and declared else None


def reported_sha256(metadata: dict) -> str | None:
    """The hash an uploader says its file has, or None when it says none.

    A value that is not a SHA-256 is no hash at all, and is passed over: a
    file is not refused over a claim about it that cannot even be checked.

    :param metadata: The upload's metadata, as the tus router decoded it.
    :type metadata: dict
    :rtype: str | None
    """
    reported = (metadata.get(acquisition.SHA256_METADATA_KEY) or "").strip().lower()
    return reported if _SHA256.match(reported) else None


def file_sha256(path: str) -> str:
    """The SHA-256 of a file's content, as lowercase hex.

    :param path: The file.
    :type path: str
    :rtype: str
    """
    with open(path, "rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()
