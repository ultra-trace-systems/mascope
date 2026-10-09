"""
Download and verify a published demo bundle into the local cache.

Pure download/extract/verify logic - no Typer concerns. The CLI command in
``main.py`` wraps these helpers with user-facing messaging.
"""

import http.client
import shutil
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from mascope_cli.cmd.demo import bundles
from mascope_cli.runtime import runtime


# Seconds to wait for the connection, and then for each further piece of the
# response, before an attempt is given up. It bounds a stall, not the download:
# a slow transfer that keeps delivering is left to finish.
_TIMEOUT_S = 60

# How many times an archive is requested before the download is given up, the
# first request included.
_MAX_ATTEMPTS = 5

# Seconds to pause before the second attempt. Each later pause is twice the one
# before it: 5, 10, 20, 40.
_RETRY_PAUSE_S = 5

# Statuses that say "not now" rather than "never", and are worth asking again.
_TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class _AttemptFailed(Exception):
    """One attempt at a download that did not deliver the file, and may be retried."""


def _extract_archive(archive: Path, dest: Path) -> None:
    """
    Extract a ``.zip`` or ``.tar.gz`` archive into ``dest``.

    The archive is expected to contain the bundle contents at its top level
    (``manifest.json``, ``raw/``, ``snapshot/`` ...). If it instead wraps them in
    a single top-level directory, that directory's contents are flattened into
    ``dest`` so the layout is consistent regardless of how it was packed.

    :param archive: Path to the downloaded archive.
    :param dest: Target bundle directory (created/overwritten by the caller).
    :raises ValueError: If the archive format is unsupported.
    """
    staging = dest.parent / f"{dest.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(staging)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            tf.extractall(staging)
    else:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f"Unsupported archive format: {archive.name}")

    # Flatten a single wrapping directory if present.
    entries = list(staging.iterdir())
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging

    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(root), str(dest))
    shutil.rmtree(staging, ignore_errors=True)


def _announced_length(response) -> int | None:
    """Return the body length a response's headers announce, if they announce one."""
    try:
        return int(response.headers.get("Content-Length", ""))
    except ValueError:
        return None


def _arrived(received: int, total: int | None) -> str:
    """Word how much of a file has arrived, for a log line or an error."""
    if total is None:
        return f"{received:,} bytes"
    return f"{received:,} of {total:,} bytes"


def _attempt(url: str, dest: Path) -> None:
    """
    Request a URL once and write what arrives to a local file.

    What ``dest`` already holds is taken for the start of the same file: only
    the rest is asked for, with an HTTP ``Range`` request, and it is appended
    when the server answers 206 with exactly that part. A server that sends
    the whole file instead has the file started over.

    A server that closes the connection early ends the stream without an
    error, so the bytes received are compared with the length the server
    announced, and falling short of it fails the attempt.

    :param url: Source URL.
    :param dest: Destination file path; its parent must exist.
    :raises _AttemptFailed: If the transfer failed, stalled or ended short in
                            a way another attempt may not.
    :raises OSError: On a failure that asking again will not change: a status
                     such as 404 (``urllib.error.HTTPError``), or an error
                     writing ``dest``.
    """
    offset = dest.stat().st_size if dest.exists() else 0
    request = urllib.request.Request(url)
    if offset:
        request.add_header("Range", f"bytes={offset}-")

    try:
        response = urllib.request.urlopen(  # noqa: S310 - trusted Zenodo URL
            request, timeout=_TIMEOUT_S
        )
    except urllib.error.HTTPError as e:
        if offset and e.code == 416:
            dest.unlink()
            raise _AttemptFailed(
                f"the server would not resume at byte {offset:,}"
            ) from e
        if e.code not in _TRANSIENT_STATUSES:
            raise
        raise _AttemptFailed(f"HTTP {e.code} {e.reason}") from e
    except (OSError, http.client.HTTPException) as e:
        raise _AttemptFailed(str(e) or type(e).__name__) from e

    with response:
        resumed = False
        if offset and response.status == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {offset}-"):
                dest.unlink()
                raise _AttemptFailed(
                    f"asked for byte {offset:,} onwards, "
                    f"the server sent '{content_range}'"
                )
            resumed = True

        received = offset if resumed else 0
        length = _announced_length(response)
        total = None if length is None else received + length
        if resumed:
            runtime.logger.info(f"  resuming after {_arrived(received, total)}")
        elif offset:
            runtime.logger.info("  the server sent the whole file again; starting over")
        pct = received * 100 // total if total else 0
        next_mark = pct - (pct % 10) + 10

        with dest.open("ab" if resumed else "wb") as out:
            while True:
                try:
                    chunk = response.read(1 << 20)
                except (OSError, http.client.HTTPException) as e:
                    raise _AttemptFailed(
                        f"{str(e) or type(e).__name__}, "
                        f"after {_arrived(received, total)}"
                    ) from e
                if not chunk:
                    break
                out.write(chunk)
                received += len(chunk)
                if total:
                    pct = received * 100 // total
                    if pct >= next_mark:
                        runtime.logger.info(f"  ...{pct}%")
                        next_mark = pct - (pct % 10) + 10

    if total is not None and received < total:
        raise _AttemptFailed(f"the connection closed after {_arrived(received, total)}")


def _download(url: str, dest: Path) -> None:
    """
    Download a URL to a local file, retrying a transfer that fails or is cut short.

    An attempt that fails, stalls or ends short of the announced length is
    made again after a pause, up to :data:`_MAX_ATTEMPTS` attempts in all. A
    later attempt resumes from the bytes the earlier ones wrote where the
    server allows it (see :func:`_attempt`).

    All this establishes is that the announced length arrived. Whether the
    file is the right one, a resumed file included, is for the caller's
    checksum to say.

    :param url: Source URL.
    :param dest: Destination file path (parent created automatically). A file
                 already there is replaced, not resumed.
    :raises RuntimeError: If no attempt delivered the whole file.
    :raises OSError: On a failure that is not retried (see :func:`_attempt`).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    runtime.logger.info(f"Downloading {url}")

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            _attempt(url, dest)
            return
        except _AttemptFailed as e:
            if attempt == _MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Download of {url} failed after {_MAX_ATTEMPTS} attempts: {e}"
                ) from e
            pause = _RETRY_PAUSE_S * 2 ** (attempt - 1)
            runtime.logger.warning(
                f"Download attempt {attempt} of {_MAX_ATTEMPTS} failed: {e}. "
                f"Trying again in {pause} s"
            )
            time.sleep(pause)


def _checksum_mismatch(bundle: bundles.Bundle, archive: Path) -> str | None:
    """
    Compare a downloaded archive with the checksum its bundle was published with.

    :param bundle: The bundle the archive belongs to.
    :param archive: Path to the downloaded archive.
    :return: A message naming both checksums if they differ, otherwise ``None``
             (also when the bundle records no checksum).
    """
    if not bundle.archive_md5:
        return None
    actual = bundles.md5_file(archive)
    # Case-insensitive: hashlib emits lowercase, but a checksum pasted from
    # Zenodo (or PowerShell's Get-FileHash) may be uppercase.
    if actual.lower() == bundle.archive_md5.lower():
        return None
    return (
        f"Archive checksum mismatch for '{bundle.version}': "
        f"expected {bundle.archive_md5[:12]}..., got {actual[:12]}..."
    )


def fetch(version: str | None = None, force: bool = False) -> Path:
    """
    Ensure a demo bundle is present and verified in the local cache.

    If already cached and intact, returns immediately unless ``force`` is set.
    Otherwise downloads the archive from the registered URL, extracts it, and
    verifies every file against the manifest checksums.

    :param version: Bundle version tag. Defaults to the registry default.
    :param force: Re-download even if a valid cached copy exists.
    :raises RuntimeError: If the bundle has no published URL, the download
                          fails, or checksum verification fails.
    :return: Path to the verified bundle directory.
    """
    bundle = bundles.get_bundle(version)
    dest = bundles.bundle_dir(version)

    if bundles.is_cached(version) and not force:
        problems = bundles.verify_manifest(version)
        if not problems:
            runtime.logger.success(f"Demo bundle '{bundle.version}' already cached")
            return dest
        runtime.logger.warning(
            f"Cached bundle '{bundle.version}' failed verification; re-fetching"
        )

    if not bundle.url:
        raise RuntimeError(
            f"Demo bundle '{bundle.version}' has no published download URL yet. "
            "Build one locally with 'mascope demo snapshot', or set its URL in "
            "tooling/cli/src/mascope_cli/cmd/demo/bundles.py once published."
        )

    archive = bundles.cache_root() / f"{bundle.version}{_suffix(bundle.url)}"
    _download(bundle.url, archive)

    mismatch = _checksum_mismatch(bundle, archive)
    if mismatch:
        # Every byte the server announced arrived, and they are still not the
        # published ones: damaged on the way, joined wrongly by a resume, or
        # cut short with no length announced to fall short of. One download
        # from the start tells those apart from a checksum that will never
        # match, which is reported.
        runtime.logger.warning(f"{mismatch}. Downloading it once more")
        _download(bundle.url, archive)
        mismatch = _checksum_mismatch(bundle, archive)
        if mismatch:
            raise RuntimeError(mismatch)

    runtime.logger.info("Extracting bundle...")
    _extract_archive(archive, dest)
    archive.unlink(missing_ok=True)

    problems = bundles.verify_manifest(version)
    if problems:
        raise RuntimeError(
            "Bundle verification failed after download:\n  - " + "\n  - ".join(problems)
        )

    runtime.logger.success(f"Demo bundle '{bundle.version}' fetched and verified")
    return dest


def _suffix(url: str) -> str:
    """Return the archive suffix (``.zip`` or ``.tar.gz``) implied by a URL."""
    lowered = url.lower()
    if lowered.endswith(".tar.gz") or lowered.endswith(".tgz"):
        return ".tar.gz"
    return ".zip"
