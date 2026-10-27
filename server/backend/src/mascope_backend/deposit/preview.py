"""
What a dataset's deposit package would hold, before it is built, and what the
dataset still lacks for a deposit.

The app's "Prepare for publication" shows this before it builds anything. It
reads the package :func:`~mascope_backend.deposit.collect.collect_package`
gathers, the one the archive is written from, so what it lists is what the
archive will hold. Nothing is written and no spreadsheet is built.

A gap is a part of the dataset's record a repository asks for that is not
there. The descriptive metadata is optional in Mascope on purpose; it is here
that its absence shows. A gap does not stop a build: the archive says "not
recorded" where a field is empty, and the operator command builds it alike.
"""

import asyncio
import os

from mascope_backend.deposit.collect import Package, collect_package
from mascope_backend.deposit.crate import METADATA_FILE


#: The files every archive holds beside the package's own files.
DOCUMENTS = (
    "README.md",
    "DATA_LICENSE",
    "manifest.json",
    METADATA_FILE,
    "SHA256SUMS",
)


async def preview_package(dataset_id: str) -> dict:
    """
    What the deposit package of a dataset would hold, and its gaps.

    :param dataset_id: The dataset.
    :return: ``archive`` (the archive's file name), ``dataset`` (the metadata
        record as the manifest gives it), ``files`` (how many files the
        archive would hold), ``raw_bytes`` (the size of the raw files it
        would copy), ``raw_files`` and ``raw_files_included`` (sample files
        described, and of them with their raw file), ``raw_files_not_kept``
        (the names of the sample files whose raw file the filestore no
        longer keeps), ``acquisition_records``, ``batches`` (each with its
        number of samples), ``target_collections`` (each with what it is
        used as), ``ionization_modes`` and ``gaps`` (see
        :func:`package_gaps`).
    :rtype: dict
    :raises DatasetNotFound: If there is no such dataset.
    """
    package = await collect_package(dataset_id)
    raw_bytes = await asyncio.to_thread(_raw_bytes, package)
    manifest = package.manifest
    raw = manifest["raw"]
    return {
        "archive": f"{package.name}.zip",
        "dataset": manifest["dataset"],
        "files": len(DOCUMENTS) + len(package.files),
        "raw_bytes": raw_bytes,
        "raw_files": len(raw),
        "raw_files_included": sum(1 for entry in raw if entry["included"]),
        "raw_files_not_kept": [
            entry["filename"] for entry in raw if not entry["included"]
        ],
        "acquisition_records": sum(1 for entry in raw if entry["acquisition"]),
        "batches": [
            {
                "sample_batch_id": batch["sample_batch_id"],
                "sample_batch_name": batch["sample_batch_name"],
                "samples": len(batch["samples"]),
            }
            for batch in manifest["batches"]
        ],
        "target_collections": [
            {
                "target_collection_id": collection["target_collection_id"],
                "target_collection_name": collection["target_collection_name"],
                "used_as": collection["used_as"],
            }
            for collection in manifest["target_collections"]
        ],
        "ionization_modes": [
            {
                "ionization_mode_id": mode["ionization_mode_id"],
                "ionization_mode_name": mode["ionization_mode_name"],
            }
            for mode in manifest["ionization_modes"]
        ],
        "gaps": package_gaps(manifest),
    }


def package_gaps(manifest: dict) -> list[dict]:
    """
    What a package's dataset lacks for a deposit, in the order to fix it.

    A repository asks for a licence, the creators and a description before it
    publishes a record, and a package of no samples describes no measurement.
    Keywords, related identifiers and funding are optional there too, and are
    no gap.

    :param manifest: A package's manifest.
    :return: One ``{"field": ..., "message": ...}`` per gap; ``field`` is the
        dataset field to fill in, or ``samples``.
    :rtype: list[dict]
    """
    dataset = manifest["dataset"]
    gaps = []
    if not dataset["license"]:
        gaps.append(
            {
                "field": "license",
                "message": "No licence. Without one, nobody but the authors may "
                "reuse the data.",
            }
        )
    if not dataset["authors"]:
        gaps.append(
            {
                "field": "authors",
                "message": "No authors. A repository cites a deposit by its creators.",
            }
        )
    if not dataset["dataset_description"]:
        gaps.append(
            {
                "field": "dataset_description",
                "message": "No description. A repository shows it as what the "
                "deposit is.",
            }
        )
    if not manifest["raw"]:
        gaps.append(
            {
                "field": "samples",
                "message": "No samples. The package would describe no measurement.",
            }
        )
    return gaps


def _raw_bytes(package: Package) -> int:
    """The size of the raw files a package copies. Blocking."""
    return sum(os.path.getsize(f.source) for f in package.files if f.source)
