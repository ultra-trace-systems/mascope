"""
Startup report of the files in ``failed_files`` that no record was made of.

The file converter moves a file it could not store to ``failed_files`` beside
the streams folder, where nothing retries it. Most such files had their
uploader told why. One the converter found with no upload registered for it -
left in the streams folder across a converter restart - had nobody told: the
converter reports it to the admins as it sets it aside, but that report is
lost when the backend is not there to take it (#2535). So at every start the
backend looks through ``failed_files`` itself and names each file that has no
record to the deployment's admins and owners
(``api/new/notifications/lost_uploads.py``), each file once.

A stored file's name is not the file's own: the Thermo reader puts the
acquisition time after the instrument segment, and the TOF reader does when
the name carries none. So a file has a record when a stored name is its stem
(spaces read as underscores, as the Thermo reader stores them), with or
without one such timestamp segment.
"""

import os
import re

from sqlalchemy import or_, select

from mascope_backend.api.new.notifications.lost_uploads import (
    FOUND_DETAIL,
    LostUpload,
    notify_lost_uploads,
)
from mascope_backend.db import SampleFile, async_session
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_name


#: The folder the converter moves a file it could not store to.
FAILED_FILES = "failed_files"

#: The files the converter reads, as its watchers glob them.
RAW_EXTENSIONS = (".raw", ".h5")

#: The acquisition time a reader puts into a stored name, with the underscore
#: before it ("%Y.%m.%d-%Hh%Mm%Ss"), where it ends the name or a segment.
_STAMP = re.compile(r"_\d{4}\.\d{2}\.\d{2}-\d{2}h\d{2}m\d{2}s(?=_|$)")


def failed_files_folder() -> str:
    """The converter's ``failed_files``, beside the streams folder uploads go to."""
    return os.path.join(runtime.config.filestreams, FAILED_FILES)


def list_failed_files(folder: str) -> list[str]:
    """The raw files in ``folder``, by name, in order; none if it is missing."""
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []
    return sorted(
        name
        for name in names
        if name.lower().endswith(RAW_EXTENSIONS)
        and os.path.isfile(os.path.join(folder, name))
    )


def _stems(name: str) -> set[str]:
    """The names a file's record could be stored under, timestamp removed."""
    stem = os.path.splitext(name)[0]
    return {stem, stem.replace(" ", "_")}


def record_keys(stored: set[str]) -> set[str]:
    """The stems stored names answer to: each as it is, and without its
    first timestamp segment. Built once for a scan, so each file is one set
    lookup however many names its instrument has stored.
    """
    return stored | {_STAMP.sub("", candidate, count=1) for candidate in stored}


def has_record(name: str, keys: set[str]) -> bool:
    """Whether a file in ``failed_files`` has a stored record.

    :param name: The file's name, with its extension.
    :param keys: :func:`record_keys` of the stored names that may be the file's.
    :return: True when one of them is the file's stem, with or without the
        timestamp segment a reader puts in.
    """
    return bool(_stems(name) & keys)


async def _stored_names(session, names: list[str]) -> set[str]:
    """The stored names that begin as one of ``names`` does.

    Every stored name of a file begins with its stem up to the first
    underscore, which is where a reader puts the timestamp in. A stem that
    starts with one has an empty such prefix, which would select every stored
    name; the reader stores it as ``_<timestamp>_<rest>``, so it is looked up
    by the underscore alone.
    """
    prefixes = {
        stem.split("_", 1)[0] or "_" for name in names for stem in _stems(name) if stem
    }
    if not prefixes:
        return set()
    return set(
        await session.scalars(
            select(SampleFile.filename).where(
                or_(
                    *(
                        SampleFile.filename.startswith(prefix, autoescape=True)
                        for prefix in sorted(prefixes)
                    )
                )
            )
        )
    )


async def report_unstored_failed_files(folder: str | None = None) -> dict:
    """Name the files in ``failed_files`` with no record to the admins.

    Runs at startup, in the main process: no browser is connected yet, so
    nothing is sent, and the notifications wait for their readers. Never
    raises: a report is not worth refusing to boot over.

    :param folder: The folder to look through; the converter's by default.
    :return: Operation results, with the files reported under ``data.files``.
    :rtype: dict
    """
    folder = folder or failed_files_folder()
    try:
        names = list_failed_files(folder)
        if not names:
            files: list[LostUpload] = []
        else:
            async with async_session() as session:
                keys = record_keys(await _stored_names(session, names))
            files = [
                (name, get_instrument_name(name), FOUND_DETAIL)
                for name in names
                if not has_record(name, keys)
            ]
        if files:
            await notify_lost_uploads(files, emit=False, once=True)
    except Exception as error:  # noqa: BLE001 - a report must not stop a start
        message = f"Could not look through the failed_files folder: {error}"
        runtime.logger.warning(message)
        return {"status": "skipped", "message": message, "data": {"files": []}}

    if files:
        message = (
            f"Found {len(files)} file(s) in failed_files with no record; "
            "named to the admins and owners not told of them yet"
        )
        # INFO: each is an upload someone has to make again
        runtime.logger.info(message)
    else:
        message = "No file without a record in failed_files"
        runtime.logger.debug(message)
    return {
        "status": "success",
        "message": message,
        "data": {"files": [name for name, _, _ in files]},
    }
