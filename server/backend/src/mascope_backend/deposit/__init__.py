"""
The deposit package of a dataset: one archive a person uploads to a data
repository such as Zenodo, which on download tells a stranger what was
measured, how, under what licence, by whom, and with which software.

Mascope is the on-ramp to a repository, not the repository: it writes the
archive, a person uploads it, and the DOI is the repository's to mint. The
dataset's related identifiers are where that DOI is recorded afterwards, so
that a package built again names the deposit.

The archive is a zip holding one directory, the package, named after the
dataset (``mascope-package_<dataset id>_<dataset name>``)::

    README.md                      the dataset, for a person: what, how, who,
                                   under what licence, which software
    DATA_LICENSE                   the dataset's licence
    manifest.json                  the same and more, for a program, with the
                                   SHA-256 and size of every file it names
    SHA256SUMS                     every other file's SHA-256, as
                                   ``sha256sum -c SHA256SUMS`` reads them
    raw/<file>.raw | .h5           each sample file's raw file, as it was
                                   uploaded
    raw/<file>.raw.mascope.json    the acquisition record the instrument's
                                   control program sent with it, if it sent one
    batches/<batch>.xlsx           each batch's spreadsheet, the one the app's
                                   "Export spreadsheet" writes
    target_collections/<c>.json    each collection a batch was matched against
                                   or an ionization mode calibrates with: its
                                   compounds with their identity, and their ions
    instrument_functions.json      the peak shape and resolution each file was
                                   processed with

``manifest.json`` follows the demo bundle's (``docs/demo_dataset.md``): its
``raw`` entries carry ``name``, ``sha256`` and ``bytes``, its
``produced_with`` the bundle's keys, and its ``provenance`` is the block every
export carries (``mascope_backend.provenance``), whose ``inputs`` name the
dataset, with its global id, and its batches. The ionization modes the samples
were processed under, with their mechanisms, are in it too. Every record it
names carries its id. Where the deployment has an id, the records a global id
can name (``GLOBAL_ID_ENTITIES`` in ``mascope_backend.deployment``) carry it
too: the dataset, its batches, samples and sample files, and the collections,
compounds and ions. Ionization modes, mechanisms and instrument functions
carry their id alone. Its ``schema`` is
:data:`PACKAGE_SCHEMA`; like the provenance block's keys, the manifest's are a
contract: a key may be added, and one that has shipped is never renamed or
given a new meaning without a new schema.

Every file is checksummed as it is written, so the checksums are of the bytes
in the archive. A raw file the filestore does not keep - a file of which only
the converted data is left - is still described, with ``included`` false and
the reason. One whose SHA-256 is not the one recorded at its upload is packed
as it is, both hashes in its entry, and reported.

The archive is built from the database and the filestore of one deployment,
by :func:`build_package`: the operator command ``mascope export package``
(``python -m mascope_backend.deposit``) runs it on the server. It checks no
workspace role; whoever runs it has the database.
"""

import os

from mascope_backend.deposit.archive import write_package
from mascope_backend.deposit.collect import (
    PACKAGE_SCHEMA,
    DatasetNotFound,
    Package,
    collect_package,
)


__all__ = [
    "PACKAGE_SCHEMA",
    "DatasetNotFound",
    "Package",
    "build_package",
    "collect_package",
    "write_package",
]


async def build_package(dataset_id: str, output: str) -> dict:
    """
    Build the deposit package of a dataset and write it to ``output``.

    Reads through its own sessions, so it needs a configured engine. Every
    file is written in a thread: copying the raw files is most of the work.

    :param dataset_id: The dataset to package.
    :param output: The archive's path, or an existing directory to write it
        into as ``<package name>.zip``. Written as ``<path>.part`` and moved
        into place when complete, so no half-written archive has its name.
    :return: What was written: see :func:`write_package`.
    :rtype: dict
    :raises DatasetNotFound: If there is no such dataset.
    """
    package = await collect_package(dataset_id)
    if os.path.isdir(output):
        output = os.path.join(output, f"{package.name}.zip")
    return await write_package(package, output)
