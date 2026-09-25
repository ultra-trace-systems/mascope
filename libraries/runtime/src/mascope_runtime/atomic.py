"""Writing a JSON file so that a reader never sees a partial one.

Two files Mascope rewrites in place are read far more often than they are
written - the runtime state and a sample's ``.props`` - and both hold
something that costs more than a reread to lose: which env is active, and a
sample's m/z calibration fit. Overwriting in place truncates the old content
before the new content is known to be complete, so a process that dies part
way leaves an empty file.

Writing beside the file and renaming over it fixes that, but only with the
three details below. They are here once rather than in each writer, because
each of them is a thing a writer gets wrong by leaving it out:

- **The temporary name must be unique per writer, not per process.** Two
  threads of one process writing ``<path>.<pid>.tmp`` share one temporary,
  and one thread's rename publishes a file the other is still filling - the
  torn file the rename was meant to prevent. :func:`tempfile.mkstemp` is
  unique per call.
- **The content must reach the disk before the rename.** Without the fsync,
  the rename can land before the bytes do, and a power loss leaves the new
  name pointing at an empty file on some filesystems - which is the failure
  being avoided, arrived at the other way.
- **On Windows the rename fails while any other handle has the destination
  open**, with ``PermissionError``, where an in-place write would have
  succeeded. Mascope reads ``.props`` often enough for that to be a real
  collision on a dev stack, so the rename is retried with a short backoff
  rather than allowed to lose the write.
"""

import contextlib
import json
import os
import random
import tempfile
import time


#: How long a rename keeps retrying a Windows sharing violation before giving
#: up. Generous: the reader that holds the file open is reading a small JSON
#: document, so the collision lasts microseconds and only a stuck reader
#: reaches the end of this.
REPLACE_TIMEOUT = 5.0


def replace_with_retry(temp_path: str, path: str, timeout: float = REPLACE_TIMEOUT):
    """Rename ``temp_path`` over ``path``, waiting out a Windows sharing lock.

    :param temp_path: The file to move.
    :param path: What it replaces.
    :param timeout: How long to keep retrying a ``PermissionError`` [s].
    :raises PermissionError: The destination stayed open for the whole budget.
    """
    deadline = time.monotonic() + timeout
    delay = 0.001
    while True:
        try:
            os.replace(temp_path, path)
            return
        except PermissionError:
            # Windows only: a reader currently holds the destination open.
            # Back off (with jitter, so concurrent writers do not keep
            # colliding) and try again until the budget runs out.
            if time.monotonic() >= deadline:
                raise
        time.sleep(delay * (1 + random.random()))
        delay = min(delay * 2, 0.1)


def write_json(
    path: str,
    data,
    *,
    indent: int | None = None,
    prefix: str = ".tmp.",
    timeout: float = REPLACE_TIMEOUT,
):
    """Write ``data`` to ``path`` as JSON, leaving the old file on any failure.

    :param path: The file to write.
    :param data: What to serialise.
    :param indent: Passed to :func:`json.dump`.
    :param prefix: Prefix for the temporary file, which is made in the same
        directory so the rename stays on one filesystem.
    :param timeout: How long the rename retries a Windows sharing lock [s].
    :raises OSError: The write or the rename failed; ``path`` is unchanged.
    """
    directory = os.path.dirname(path) or "."
    fd, temp_path = tempfile.mkstemp(
        dir=directory, prefix=f"{prefix}{os.path.basename(path)}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(temp_path, path, timeout=timeout)
    finally:
        # A successful replace moved the temporary away; anything still there
        # is from a failed write and must not be left behind.
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
