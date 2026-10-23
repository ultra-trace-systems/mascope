"""
Writing a package: the zip archive, and the checksum of every file in it.

Each file is hashed as it goes into the archive, from the bytes written, so
``manifest.json`` and ``SHA256SUMS`` check the archive itself rather than the
filestore it was copied from. A raw file is read once, a piece at a time:
nothing of it is held in memory.

The archive is the same for the same package: its files go in the same order,
each dated the package's creation time (to the two seconds a zip records) and
given the same permissions, so it differs only where what it describes does. ``manifest.json`` and
``SHA256SUMS`` come last, since they hold the checksums of everything before
them; ``SHA256SUMS`` holds the manifest's too.
"""

import asyncio
import hashlib
import os
import zipfile
from datetime import datetime

from mascope_backend.deposit.collect import Package, PackageFile, _json
from mascope_backend.deposit.documents import render_licence, render_readme
from mascope_backend.runtime import runtime


#: How much of a raw file is read at a time.
_CHUNK = 1 << 20

#: Deflate level. The lowest: it saves most of what the default would on a
#: raw file at a fraction of the time, and raw files are most of a package.
_COMPRESS_LEVEL = 1

#: A regular file, readable by all, writable by its owner.
_FILE_MODE = 0o100644


async def write_package(package: Package, output: str) -> dict:
    """
    Write a package to a zip archive at ``output``.

    The archive is written as ``<output>.part`` and renamed when complete, so
    a build that fails leaves no archive under the name asked for. Each file
    is written in a thread, one after another; a batch's spreadsheet is built
    just before it is written.

    :param package: The package, from ``collect_package``.
    :param output: The archive's path.
    :return: What was written: ``archive`` (the path), ``bytes`` (the
        archive's size), ``package`` (its directory in the archive),
        ``files`` (how many files it holds), ``raw_files`` and
        ``raw_files_included`` (sample files described, and of them with
        their raw file), ``raw_files_changed`` (raw files whose SHA-256 is
        not the one recorded at upload), ``batches`` and
        ``target_collections``.
    :rtype: dict
    """
    partial = f"{output}.part"
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    try:
        archive = await asyncio.to_thread(
            _Archive, partial, package.name, package.created
        )
        with archive:
            for path, text in (
                ("README.md", render_readme(package.manifest)),
                ("DATA_LICENSE", render_licence(package.manifest)),
            ):
                await asyncio.to_thread(archive.add_bytes, path, text.encode())
            for package_file in package.files:
                if package_file.produce is not None:
                    package_file.data = await package_file.produce()
                await asyncio.to_thread(archive.add, package_file)
                # Written and checksummed: a spreadsheet is not kept.
                if package_file.produce is not None:
                    package_file.data = None
            await asyncio.to_thread(
                archive.add_bytes, "manifest.json", _json(package.manifest)
            )
            await asyncio.to_thread(archive.add_sums)
        os.replace(partial, output)
    except BaseException:
        if os.path.exists(partial):
            os.remove(partial)
        raise

    raw = package.manifest["raw"]
    changed = [
        entry["filename"]
        for entry in raw
        if entry["included"]
        and entry["sha256_at_upload"]
        and entry["sha256_at_upload"] != entry["sha256"]
    ]
    for filename in changed:
        runtime.logger.warning(
            f"The raw file of '{filename}' is not the file that was uploaded: "
            "its SHA-256 differs from the one recorded at upload."
        )
    return {
        "archive": output,
        "bytes": os.path.getsize(output),
        "package": package.name,
        "files": archive.count,
        "raw_files": len(raw),
        "raw_files_included": sum(1 for entry in raw if entry["included"]),
        "raw_files_changed": len(changed),
        "batches": len(package.manifest["batches"]),
        "target_collections": len(package.manifest["target_collections"]),
    }


class _Archive:
    """A zip of one directory, each file checksummed as it is added."""

    def __init__(self, path: str, root: str, created: datetime):
        self._zip = zipfile.ZipFile(
            path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=_COMPRESS_LEVEL,
            allowZip64=True,
        )
        self._root = root
        self._date_time = created.timetuple()[:6]
        self._sums: list[tuple[str, str]] = []

    def __enter__(self) -> "_Archive":
        return self

    def __exit__(self, *exc) -> None:
        self._zip.close()

    @property
    def count(self) -> int:
        """How many files the archive holds, SHA256SUMS included once added."""
        return len(self._zip.infolist())

    def add(self, package_file: PackageFile) -> None:
        """Add one file of the package, and record its checksum in its entry."""
        if package_file.source is not None:
            digest, size = self.add_file(
                package_file.path, package_file.source, package_file.compress
            )
        else:
            digest, size = self.add_bytes(
                package_file.path, package_file.data, package_file.compress
            )
        if package_file.entry is not None:
            package_file.entry["sha256"] = digest
            package_file.entry["bytes"] = size

    def add_bytes(
        self, path: str, data: bytes, compress: bool = True
    ) -> tuple[str, int]:
        """Add a file the package makes. :return: Its SHA-256 and size."""
        digest = hashlib.sha256(data).hexdigest()
        self._zip.writestr(self._info(path, len(data), compress), data)
        self._sums.append((digest, path))
        return digest, len(data)

    def add_file(
        self, path: str, source: str, compress: bool = True
    ) -> tuple[str, int]:
        """Copy a file from disk into the archive. :return: Its SHA-256 and size."""
        hasher = hashlib.sha256()
        size = 0
        info = self._info(path, os.path.getsize(source), compress)
        with open(source, "rb") as src, self._zip.open(info, "w") as dst:
            while chunk := src.read(_CHUNK):
                hasher.update(chunk)
                dst.write(chunk)
                size += len(chunk)
        digest = hasher.hexdigest()
        self._sums.append((digest, path))
        return digest, size

    def add_sums(self) -> None:
        """Add ``SHA256SUMS``: every file so far, in the format of ``sha256sum``."""
        text = "".join(f"{digest}  {path}\n" for digest, path in self._sums)
        self._zip.writestr(
            self._info("SHA256SUMS", len(text), compress=True), text.encode()
        )

    def _info(self, path: str, size: int, compress: bool) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(f"{self._root}/{path}", date_time=self._date_time)
        info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        # The archive's level applies only to a member it names itself; one
        # given as a ZipInfo carries its own, in an attribute Python 3.12
        # keeps private (3.13 names it compress_level).
        info._compresslevel = _COMPRESS_LEVEL
        info.external_attr = _FILE_MODE << 16
        # Known before writing, so the header is made large enough for a raw
        # file over 4 GiB without being forced on every small one.
        info.file_size = size
        return info
