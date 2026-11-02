"""Per-user scoping for ephemeral temp downloads.

Temp files (spreadsheet exports, peak CSVs, download bundles) are written under
a per-user subdirectory of the runtime temp dir. The ``/api/temp`` download
route only ever reads from the caller's own subdirectory, so a user can only
download files they generated - never another tenant's, even when the filename
is known or guessable. The basename reduction plus the containment check also
close off path traversal.

A download stays for :data:`DOWNLOAD_RETENTION_S` after it was last written.
Each write starts a sweep, in a thread beside the event loop, of every user's
directory for the downloads older than that, so
the space a download takes is given back by the next one, with no timer to
run; the startup reset still empties ``temp/`` as a whole.
"""

import os
import re
import shutil
import threading
import time

from mascope_backend.runtime import runtime


#: How long a temp download is kept after it was last written. Long enough to
#: come back for a deposit package the next day; short enough that the raw
#: files the bundle and the package copy do not pile up on a long-running
#: server. The age is the entry's mtime: a copy, a zip and a moved archive all
#: carry the time their writing finished.
DOWNLOAD_RETENTION_S = 24 * 60 * 60

#: Shortest time between two sweeps in one process. An export that writes
#: many files calls :func:`user_temp_dir` for each; one scan is enough.
_SWEEP_INTERVAL_S = 60

_last_sweep: float | None = None

#: The thread of the latest sweep. Exports run on the API worker's event loop,
#: and removing a day of raw-file bundles and deposit packages takes seconds,
#: so the sweep runs beside the loop rather than on it.
_sweeper: threading.Thread | None = None


def user_temp_dir(user_id: int, *, create: bool = True) -> str:
    """
    Return the temp directory reserved for ``user_id``.

    :param user_id: The owning user's id.
    :param create: Create the directory if missing. Writers keep the default;
        the download route passes ``False`` so a mere GET probe does not mint
        empty per-user directories.
    :return: Absolute path to the user's temp directory.
    """
    path = runtime.env.path("temp", str(int(user_id)))
    if create:
        _sweep_now_and_then()
        os.makedirs(path, exist_ok=True)
    return path


def _sweep_now_and_then() -> None:
    """Start a sweep of expired downloads in a thread of its own.

    At most once per :data:`_SWEEP_INTERVAL_S`, and never while the previous
    sweep is still running. The caller does not wait for it.
    """
    global _last_sweep, _sweeper
    now = time.monotonic()
    if _last_sweep is not None and now - _last_sweep < _SWEEP_INTERVAL_S:
        return
    if _sweeper is not None and _sweeper.is_alive():
        return
    _last_sweep = now
    _sweeper = threading.Thread(
        target=sweep_expired_downloads, name="temp-download-sweep", daemon=True
    )
    _sweeper.start()


def sweep_expired_downloads(max_age_s: float = DOWNLOAD_RETENTION_S) -> int:
    """
    Remove every user's temp downloads last written more than ``max_age_s`` ago.

    Only the per-user directories (``temp/<user id>/``) are swept: ``tus/``
    holds uploads in flight, with a sweep of its own, and ``packages/`` holds
    builds in progress. A folder an export is still filling is fresh, since
    each file added to it touches its mtime. A file a download is still
    reading may be removed: the open handle keeps reading it.

    :param max_age_s: The age, in seconds of mtime, past which an entry goes.
    :return: How many entries were removed.
    """
    temp_dir = runtime.env.path("temp")
    cutoff = time.time() - max_age_s
    removed = 0
    try:
        users = [e for e in os.scandir(temp_dir) if e.name.isdigit()]
    except OSError as error:
        runtime.logger.warning(f"Could not sweep {temp_dir}: {error}")
        return 0
    for user in users:
        try:
            if not user.is_dir(follow_symlinks=False):
                continue
            entries = list(os.scandir(user.path))
        except OSError as error:
            runtime.logger.warning(f"Could not sweep {user.path}: {error}")
            continue
        for entry in entries:
            try:
                if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                    continue
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.remove(entry.path)
                removed += 1
            except FileNotFoundError:
                continue  # another worker swept it first
            except OSError as error:
                runtime.logger.warning(f"Could not remove {entry.path}: {error}")
    if removed:
        runtime.logger.info(f"Removed {removed} expired temp download(s)")
    return removed


def user_temp_path(user_id: int, filename: str, *, create: bool = True) -> str:
    """
    Resolve ``filename`` inside the user's temp dir, rejecting traversal.

    The filename is reduced to its basename so no path separators or parent
    references can escape the per-user directory, and the resolved path is
    verified to stay within it.

    :param user_id: The owning user's id.
    :param filename: Untrusted leaf filename (e.g. from a download link).
    :param create: Create the user's directory if missing (see
        :func:`user_temp_dir`).
    :raises ValueError: If ``filename`` does not resolve to a file directly
        inside the user's temp directory.
    :return: Absolute path to the file inside the user's temp directory.
    """
    safe = os.path.basename(filename)
    if not safe or safe in (os.curdir, os.pardir):
        raise ValueError(f"Unsafe temp filename: {filename!r}")
    base = os.path.normpath(user_temp_dir(user_id, create=create))
    # The normalized path must stay within the user's dir. (normpath +
    # prefix check is the containment guard static analysis recognizes.)
    path = os.path.normpath(os.path.join(base, safe))
    if not path.startswith(base + os.sep):
        raise ValueError(f"Unsafe temp filename: {filename!r}")
    # Defense in depth: a symlink inside the dir must not escape it either.
    if not os.path.realpath(path).startswith(os.path.realpath(base) + os.sep):
        raise ValueError(f"Unsafe temp filename: {filename!r}")
    return path


#: What no file system or browser accepts in a file name - the path separators
#: first among them - and the control characters.
_UNSAFE_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

#: Longest stem a download name is given: well inside every file system's
#: 255-byte limit once the timestamp and the extension are added.
MAX_DOWNLOAD_STEM = 120


def download_name(*parts: str, extension: str) -> str:
    """A temp download's file name, built from parts a user may have typed.

    A batch or a sample is named by its owner, and a name with a ``/`` in it
    is a directory to the file system and a path to the download route -
    :func:`user_temp_path` reduces it to its basename, so the file is written
    under one name and requested under another, and the download fails. Every
    export names its file through this, so a name is a name: unsafe characters
    become ``_``, runs of whitespace one ``_``, and the stem is capped.

    :param parts: The stem's pieces (a timestamp, a kind, a user-typed name),
        joined with ``_``; empty pieces are dropped.
    :param extension: The extension, without the dot.
    :return: A single path segment ending in ``.<extension>``.
    """
    pieces = []
    for part in parts:
        text = "_".join(str(part).split())
        text = _UNSAFE_NAME_CHARS.sub("_", text)
        text = re.sub(r"_+", "_", text).strip("._ ")
        if text:
            pieces.append(text)
    stem = "_".join(pieces)[:MAX_DOWNLOAD_STEM].rstrip("._ ") or "download"
    return f"{stem}.{extension.lstrip('.')}"
