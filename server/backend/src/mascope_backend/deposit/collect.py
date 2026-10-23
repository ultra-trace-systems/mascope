"""
What a dataset's deposit package holds, read from the database and the
filestore: the manifest, and every file the archive is to carry.

Nothing is written here. :func:`collect_package` gathers the records, renders
the JSON documents and finds the raw files on disk;
``mascope_backend.deposit.archive`` writes and checksums them. A batch's
spreadsheet is built only when it is about to be written, one at a time, so a
dataset of many batches never holds them all in memory.
Every list is in a fixed order, so the same dataset packs the same way.
"""

import asyncio
import functools
import io
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import undefer

from mascope_backend import vocabulary
from mascope_backend.api.controllers.sample.batches.export import service as export
from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    _stored_data_file,
)
from mascope_backend.api.new.temp.storage import download_name
from mascope_backend.db import (
    Dataset,
    InstrumentFunction,
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    async_session,
)
from mascope_backend.deployment import compose_global_id, deployment_id
from mascope_backend.json_safe import non_finite_to_none
from mascope_backend.provenance import build_provenance
from mascope_backend.runtime import runtime


#: The manifest's format. Moves only when the meaning of a key that has
#: shipped changes; a key may be added under the same name.
PACKAGE_SCHEMA = "mascope-package/1"

#: The descriptive metadata of a dataset, as its API names the fields.
DESCRIPTIVE_FIELDS = (
    "license",
    "authors",
    "keywords",
    "related_identifiers",
    "funding",
)

#: How much of a user-typed name a file name in the package keeps, so that
#: the id after it always fits.
_NAME_IN_PATH = 80

#: What no file system accepts in a file name, the path separators first.
_UNSAFE_PATH_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class DatasetNotFound(LookupError):
    """There is no dataset by the id asked for."""


@dataclass
class PackageFile:
    """
    One file of the package, before it is written.

    :ivar path: Where it goes, relative to the package's directory, with
        ``/`` between the parts.
    :ivar data: Its content, for a file the package makes.
    :ivar produce: What makes its content when it is about to be written, for
        a file too large to keep in memory until then: a batch's spreadsheet.
    :ivar source: The file on disk it is copied from, for a raw file.
    :ivar entry: The manifest entry that names the file: its ``sha256`` and
        ``bytes`` are filled in as it is written.
    :ivar compress: Whether to deflate it; a spreadsheet is a zip already.
    """

    path: str
    data: bytes | None = None
    produce: Callable[[], Awaitable[bytes]] | None = None
    source: str | None = None
    entry: dict | None = None
    compress: bool = True


@dataclass
class Package:
    """
    Everything a dataset's archive holds, ready to write.

    :ivar name: The package's directory in the archive, and the archive's
        file name without ``.zip``.
    :ivar created: When it was built: the provenance block's time.
    :ivar manifest: ``manifest.json`` as a dict, short of the checksums the
        writing fills in.
    :ivar files: The files after README.md and DATA_LICENSE, in the order
        the archive holds them.
    """

    name: str
    created: datetime
    manifest: dict
    files: list[PackageFile] = field(default_factory=list)


async def collect_package(dataset_id: str) -> Package:
    """
    Gather what the deposit package of a dataset holds.

    :param dataset_id: The dataset.
    :return: The package, ready for ``write_package``.
    :rtype: Package
    :raises DatasetNotFound: If there is no such dataset.
    """
    async with async_session() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            raise DatasetNotFound(f"There is no dataset '{dataset_id}'")
        records = await _read_records(session, dataset)

    namespace = deployment_id()
    runtime.logger.info(
        f"Packaging dataset '{dataset.dataset_name}': "
        f"{len(records.batches)} batch(es), {len(records.files)} raw file(s)"
    )

    provenance = build_provenance(
        inputs={
            "dataset_id": dataset.dataset_id,
            "sample_batch_ids": [batch.sample_batch_id for batch in records.batches],
        }
    )
    created = datetime.strptime(
        provenance["generated_utc"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)

    models = await asyncio.to_thread(
        lambda: {
            sample_file.sample_file_id: export._instrument_model(sample_file.filename)
            for sample_file in records.files
        }
    )
    raw_entries, raw_files = await asyncio.to_thread(
        _raw_files, records.files, models, namespace
    )

    files: list[PackageFile] = list(raw_files)
    batch_entries = []
    for batch in records.batches:
        entry, spreadsheet = _batch(batch, records, namespace)
        batch_entries.append(entry)
        files.append(spreadsheet)

    collection_entries = []
    for collection in records.collections:
        entry, document = _collection(collection, records, namespace)
        collection_entries.append(entry)
        files.append(document)

    instrument_functions = PackageFile(
        path="instrument_functions.json",
        data=_json(
            {
                "instrument_functions": [
                    _instrument_function(function)
                    for function in records.instrument_functions
                ]
            }
        ),
        entry={"path": "instrument_functions.json", "sha256": None, "bytes": None},
    )
    files.append(instrument_functions)

    manifest = {
        "schema": PACKAGE_SCHEMA,
        "created": provenance["generated_utc"],
        "dataset": _dataset(dataset, namespace),
        "measurement": _measurement(records.files, raw_entries),
        "produced_with": {
            **provenance["produced_with"],
            "opentfraw_version": _opentfraw_version(),
        },
        "vocabulary": {"PSI-MS": vocabulary.PSI_MS_VERSION},
        "provenance": provenance,
        "raw": raw_entries,
        "batches": batch_entries,
        "target_collections": collection_entries,
        "ionization_modes": [
            _ionization_mode(mode, records.mechanisms) for mode in records.modes
        ],
        "instrument_functions": instrument_functions.entry,
    }
    name = download_name(
        "mascope-package", dataset.dataset_id, dataset.dataset_name, extension="zip"
    ).removesuffix(".zip")
    return Package(name=name, created=created, manifest=manifest, files=files)


@dataclass
class _Records:
    """The rows a package is built from, each list in its package order."""

    batches: list[SampleBatch]
    items_by_batch: dict[str, list[SampleItem]]
    files: list[SampleFile]
    collection_ids_by_batch: dict[str, list[str]]
    collections: list[TargetCollection]
    roles_by_collection: dict[str, list[str]]
    compounds_by_collection: dict[str, list[TargetCompound]]
    ions_by_compound: dict[str, list[TargetIon]]
    modes: list[IonizationMode]
    mechanisms: dict[str, IonizationMechanism]
    instrument_functions: list[InstrumentFunction]


async def _read_records(session, dataset: Dataset) -> _Records:
    """
    Read every row the package of ``dataset`` names.

    :param session: An open session.
    :param dataset: The dataset.
    :return: The rows.
    :rtype: _Records
    """
    batches = list(
        (
            await session.scalars(
                select(SampleBatch)
                .where(SampleBatch.dataset_id == dataset.dataset_id)
                .order_by(
                    SampleBatch.sample_batch_utc_created, SampleBatch.sample_batch_id
                )
            )
        ).all()
    )
    batch_ids = [batch.sample_batch_id for batch in batches]

    rows = (
        await session.execute(
            select(SampleItem, SampleFile)
            .join(SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id)
            .where(SampleItem.sample_batch_id.in_(batch_ids))
            .options(undefer(SampleFile.acquisition))
            .order_by(
                SampleFile.datetime_utc,
                SampleItem.sample_item_name,
                SampleItem.sample_item_id,
            )
        )
    ).all()
    items_by_batch: dict[str, list[SampleItem]] = defaultdict(list)
    files_by_id: dict[str, SampleFile] = {}
    for item, sample_file in rows:
        items_by_batch[item.sample_batch_id].append(item)
        files_by_id[sample_file.sample_file_id] = sample_file
    files = sorted(files_by_id.values(), key=lambda f: (f.filename, f.sample_file_id))

    collection_ids_by_batch: dict[str, list[str]] = defaultdict(list)
    roles_by_collection: dict[str, list[str]] = defaultdict(list)
    for link in (
        await session.scalars(
            select(TargetCollectionInSampleBatch)
            .where(TargetCollectionInSampleBatch.sample_batch_id.in_(batch_ids))
            .order_by(TargetCollectionInSampleBatch.target_collection_id)
        )
    ).all():
        collection_ids_by_batch[link.sample_batch_id].append(link.target_collection_id)
        if "matched" not in roles_by_collection[link.target_collection_id]:
            roles_by_collection[link.target_collection_id].append("matched")

    mode_ids = {
        item.ionization_mode_id
        for items in items_by_batch.values()
        for item in items
        if item.ionization_mode_id
    }
    modes = list(
        (
            await session.scalars(
                select(IonizationMode)
                .where(IonizationMode.ionization_mode_id.in_(sorted(mode_ids)))
                .order_by(
                    IonizationMode.ionization_mode_name,
                    IonizationMode.ionization_mode_id,
                )
            )
        ).all()
    )
    for mode in modes:
        for role, collection_id in (
            ("calibration", mode.calibration_collection_id),
            ("diagnostic", mode.diagnostic_collection_id),
        ):
            if collection_id and role not in roles_by_collection[collection_id]:
                roles_by_collection[collection_id].append(role)

    collections = list(
        (
            await session.scalars(
                select(TargetCollection)
                .where(
                    TargetCollection.target_collection_id.in_(
                        sorted(roles_by_collection)
                    )
                )
                .order_by(
                    TargetCollection.target_collection_name,
                    TargetCollection.target_collection_id,
                )
            )
        ).all()
    )
    compounds_by_collection: dict[str, list[TargetCompound]] = defaultdict(list)
    for compound, collection_id in (
        await session.execute(
            select(
                TargetCompound, TargetCompoundInTargetCollection.target_collection_id
            )
            .join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetCompound.target_compound_id,
            )
            .where(
                TargetCompoundInTargetCollection.target_collection_id.in_(
                    sorted(roles_by_collection)
                )
            )
            .order_by(
                TargetCompound.target_compound_formula,
                TargetCompound.target_compound_name,
                TargetCompound.target_compound_id,
            )
        )
    ).all():
        compounds_by_collection[collection_id].append(compound)

    compound_ids = {
        compound.target_compound_id
        for compounds in compounds_by_collection.values()
        for compound in compounds
    }
    ions_by_compound: dict[str, list[TargetIon]] = defaultdict(list)
    for ion in (
        await session.scalars(
            select(TargetIon)
            .where(TargetIon.target_compound_id.in_(sorted(compound_ids)))
            .order_by(TargetIon.target_ion_formula, TargetIon.target_ion_id)
        )
    ).all():
        ions_by_compound[ion.target_compound_id].append(ion)

    mechanism_ids = {
        mechanism_id
        for mode in modes
        for mechanism_id in mode.ionization_mechanism_ids or []
    } | {
        ion.ionization_mechanism_id
        for ions in ions_by_compound.values()
        for ion in ions
    }
    mechanisms = {
        mechanism.ionization_mechanism_id: mechanism
        for mechanism in (
            await session.scalars(
                select(IonizationMechanism).where(
                    IonizationMechanism.ionization_mechanism_id.in_(
                        sorted(mechanism_ids)
                    )
                )
            )
        ).all()
    }

    function_ids = {f.instrument_function_id for f in files if f.instrument_function_id}
    instrument_functions = list(
        (
            await session.scalars(
                select(InstrumentFunction)
                .where(
                    InstrumentFunction.instrument_function_id.in_(sorted(function_ids))
                )
                .order_by(InstrumentFunction.instrument_function_id)
            )
        ).all()
    )

    return _Records(
        batches=batches,
        items_by_batch=items_by_batch,
        files=files,
        collection_ids_by_batch=collection_ids_by_batch,
        collections=collections,
        roles_by_collection=roles_by_collection,
        compounds_by_collection=compounds_by_collection,
        ions_by_compound=ions_by_compound,
        modes=modes,
        mechanisms=mechanisms,
        instrument_functions=instrument_functions,
    )


def _ids(entity: str, local_id: str | None, namespace: str | None) -> dict:
    """
    A record's id, and its global id where the deployment has an id.

    The provenance block's rule: a deployment without an id gets no global
    id key rather than a made-up one.
    """
    ids = {f"{entity}_id": local_id}
    if namespace is not None and local_id is not None:
        ids[f"{entity}_global_id"] = compose_global_id(namespace, entity, local_id)
    return ids


def _dataset(dataset: Dataset, namespace: str | None) -> dict:
    """The dataset's metadata record, by the names its API uses."""
    return {
        **_ids("dataset", dataset.dataset_id, namespace),
        "dataset_name": dataset.dataset_name,
        "dataset_description": dataset.dataset_description or None,
        "dataset_type": dataset.dataset_type,
        "instrument": dataset.instrument,
        "dataset_utc_created": _utc(dataset.dataset_utc_created),
        "dataset_utc_modified": _utc(dataset.dataset_utc_modified),
        **{name: getattr(dataset, name) or None for name in DESCRIPTIVE_FIELDS},
    }


def _raw_files(
    files: list[SampleFile], models: dict[str, str | None], namespace: str | None
) -> tuple[list[dict], list[PackageFile]]:
    """
    Each sample file's manifest entry, and the files the archive carries for
    it: its raw file where the filestore keeps one, and the acquisition
    record it came with. Blocking: it looks for the files on disk.

    :return: The entries, and the files in archive order.
    """
    entries: list[dict] = []
    package_files: list[PackageFile] = []
    taken: set[str] = set()
    for sample_file in files:
        source = _stored_data_file(sample_file.filename)
        extension = source.rsplit(".", 1)[-1] if source else None
        name = _unique(
            _path_safe(sample_file.filename) + (f".{extension}" if extension else ""),
            sample_file.sample_file_id,
            taken,
        )
        model = models.get(sample_file.sample_file_id)
        calibration, verified, error_ppm = export._calibration_summary(
            sample_file.mz_calibration
        )
        entry = {
            "name": name if source else None,
            "sha256": None,
            "bytes": None,
            "included": source is not None,
            "path": f"raw/{name}" if source else None,
            **_ids("sample_file", sample_file.sample_file_id, namespace),
            "filename": sample_file.filename,
            "source_filename": sample_file.source_filename,
            "instrument": sample_file.instrument,
            "instrument_type": sample_file.instrument_type,
            "instrument_model": model,
            "method_file": sample_file.method_file,
            "datetime": _local(sample_file.datetime),
            "datetime_utc": _utc(sample_file.datetime_utc),
            "acquisition_timezone": sample_file.acquisition_timezone,
            "length": sample_file.length,
            "range": sample_file.range or None,
            "polarity": sample_file.polarity,
            "mz_calibration": {
                "status": calibration,
                "verified": verified,
                "post_fit_mz_error_ppm": error_ppm,
            },
            "instrument_function_id": sample_file.instrument_function_id,
            "sha256_at_upload": sample_file.sha256,
            "cv_terms": _cv_terms(
                model, sample_file.instrument_type, sample_file.polarity
            ),
            "acquisition": None,
        }
        if source is None:
            entry["reason"] = (
                "The filestore keeps no raw file of this sample file, only the "
                "data converted from it."
            )
        entries.append(entry)
        if source is not None:
            package_files.append(
                PackageFile(path=entry["path"], source=source, entry=entry)
            )
        if sample_file.acquisition:
            record_path = f"raw/{name}.mascope.json"
            entry["acquisition"] = {
                "acquisition_id": sample_file.acquisition_id,
                "step_id": sample_file.step_id,
                "sequence_run_id": sample_file.sequence_run_id,
                "agent_id": sample_file.agent_id,
                "path": record_path,
                "sha256": None,
                "bytes": None,
            }
            package_files.append(
                PackageFile(
                    path=record_path,
                    data=_json(sample_file.acquisition),
                    entry=entry["acquisition"],
                )
            )
    return entries, package_files


def _batch(
    batch: SampleBatch, records: _Records, namespace: str | None
) -> tuple[dict, PackageFile]:
    """A batch's manifest entry, and its spreadsheet, to be built."""
    path = "batches/" + _file_name(
        batch.sample_batch_name, batch.sample_batch_id, "xlsx"
    )
    entry = {
        **_ids("sample_batch", batch.sample_batch_id, namespace),
        "sample_batch_name": batch.sample_batch_name,
        "sample_batch_description": batch.sample_batch_description or None,
        "sample_batch_type": batch.sample_batch_type,
        "polarity": batch.polarity,
        "sample_batch_utc_created": _utc(batch.sample_batch_utc_created),
        "target_collection_ids": records.collection_ids_by_batch.get(
            batch.sample_batch_id, []
        ),
        "spreadsheet": {"path": path, "sha256": None, "bytes": None},
        "samples": [
            {
                **_ids("sample_item", item.sample_item_id, namespace),
                "sample_item_name": item.sample_item_name,
                "sample_item_type": item.sample_item_type,
                "sample_file_id": item.sample_file_id,
                "polarity": item.polarity,
                "t0": item.t0,
                "t1": item.t1,
                "ionization_mode_id": item.ionization_mode_id,
                "filter_id": item.filter_id,
                "sample_item_attributes": item.sample_item_attributes or None,
            }
            for item in records.items_by_batch.get(batch.sample_batch_id, [])
        ],
    }
    spreadsheet = PackageFile(
        path=path,
        produce=functools.partial(_spreadsheet, batch.sample_batch_id),
        entry=entry["spreadsheet"],
        compress=False,
    )
    return entry, spreadsheet


async def _spreadsheet(sample_batch_id: str) -> bytes:
    """A batch's spreadsheet, as the app's export writes it."""
    sheets = await export.build_batch_sheets(sample_batch_id)
    workbook = io.BytesIO()
    await asyncio.to_thread(export.write_batch_workbook, workbook, sheets)
    return workbook.getvalue()


def _collection(
    collection: TargetCollection, records: _Records, namespace: str | None
) -> tuple[dict, PackageFile]:
    """A target collection's manifest entry, and its document."""
    compounds = records.compounds_by_collection.get(collection.target_collection_id, [])
    ions = [
        ion
        for compound in compounds
        for ion in records.ions_by_compound.get(compound.target_compound_id, [])
    ]
    path = "target_collections/" + _file_name(
        collection.target_collection_name, collection.target_collection_id, "json"
    )
    header = {
        **_ids("target_collection", collection.target_collection_id, namespace),
        "target_collection_name": collection.target_collection_name,
        "target_collection_description": (
            collection.target_collection_description or None
        ),
        "target_collection_type": collection.target_collection_type,
    }
    document = {
        **header,
        "target_compounds": [
            {
                **_ids("target_compound", compound.target_compound_id, namespace),
                "target_compound_name": compound.target_compound_name,
                "target_compound_formula": compound.target_compound_formula,
                "cas_number": compound.cas_number,
                "inchikey": compound.inchikey,
                "inchi": compound.inchi,
                "smiles": compound.smiles,
                "target_ions": [
                    {
                        **_ids("target_ion", ion.target_ion_id, namespace),
                        "target_ion_formula": ion.target_ion_formula,
                        **_mechanism(
                            ion.ionization_mechanism_id,
                            records.mechanisms.get(ion.ionization_mechanism_id),
                        ),
                    }
                    for ion in records.ions_by_compound.get(
                        compound.target_compound_id, []
                    )
                ],
            }
            for compound in compounds
        ],
    }
    entry = {
        **header,
        "used_as": records.roles_by_collection[collection.target_collection_id],
        "target_compounds": len(compounds),
        "target_ions": len(ions),
        "path": path,
        "sha256": None,
        "bytes": None,
    }
    return entry, PackageFile(path=path, data=_json(document), entry=entry)


def _mechanism(mechanism_id: str, mechanism: IonizationMechanism | None) -> dict:
    """An ionization mechanism, as an ion or a mode names it."""
    return {
        "ionization_mechanism_id": mechanism_id,
        "ionization_mechanism": mechanism.ionization_mechanism if mechanism else None,
        "ionization_mechanism_polarity": (
            mechanism.ionization_mechanism_polarity if mechanism else None
        ),
    }


def _ionization_mode(
    mode: IonizationMode, mechanisms: dict[str, IonizationMechanism]
) -> dict:
    """An ionization mode the dataset's samples were processed under."""
    return {
        "ionization_mode_id": mode.ionization_mode_id,
        "ionization_mode_name": mode.ionization_mode_name,
        "ionization_mode_token": mode.ionization_mode_token,
        "instrument": mode.instrument,
        "ionization_mode_polarity": mode.ionization_mode_polarity,
        "system_key": mode.system_key,
        "ionization_mechanisms": [
            _mechanism(mechanism_id, mechanisms.get(mechanism_id))
            for mechanism_id in mode.ionization_mechanism_ids or []
        ],
        "calibration_collection_id": mode.calibration_collection_id,
        "diagnostic_collection_id": mode.diagnostic_collection_id,
    }


def _instrument_function(function: InstrumentFunction) -> dict:
    """An instrument function a file was processed with."""
    return {
        "instrument_function_id": function.instrument_function_id,
        "instrument": function.instrument,
        "method_file": function.method_file,
        "datetime_utc": _utc(function.datetime_utc),
        "peakshape": function.peakshape,
        "resolution_function": function.resolution_function,
    }


def _measurement(files: list[SampleFile], raw_entries: list[dict]) -> dict:
    """
    What the dataset measured, across its files: the demo bundle's
    ``measurement``, for a dataset that may span instruments.
    """
    times = sorted(f.datetime_utc for f in files if f.datetime_utc is not None)

    def distinct(values) -> list:
        return sorted({value for value in values if value})

    return {
        "instruments": distinct(f.instrument for f in files),
        "instrument_types": distinct(f.instrument_type for f in files),
        "instrument_models": distinct(e["instrument_model"] for e in raw_entries),
        "acquired": {
            "first": _utc(times[0]) if times else None,
            "last": _utc(times[-1]) if times else None,
        },
    }


def _cv_terms(
    model: str | None, instrument_type: str | None, polarity: str | None
) -> dict:
    """A file's acquisition context as PSI-MS terms; null where there is none."""

    def term(found: vocabulary.CvTerm | None) -> dict | None:
        return {"accession": found.accession, "name": found.name} if found else None

    return {
        "instrument_model": term(vocabulary.instrument_model_term(model)),
        "ion_source": term(vocabulary.source_term(instrument_type)),
        "mass_analyser": term(vocabulary.analyser_term(instrument_type)),
        "detector": term(vocabulary.detector_term(instrument_type)),
        "polarity": [term(found) for found in vocabulary.polarity_terms(polarity)],
    }


def _opentfraw_version() -> str | None:
    """
    The raw-file reader's distribution and version, as the demo bundle's
    ``produced_with.opentfraw_version`` gives it.
    """
    for dist in ("mascope-opentfraw", "opentfraw"):
        try:
            return f"{dist}=={metadata.version(dist)}"
        except metadata.PackageNotFoundError:
            continue
    return None


def _file_name(name: str | None, record_id: str, extension: str) -> str:
    """A file name for a record named by a user: the name, then the id."""
    return download_name((name or "")[:_NAME_IN_PATH], record_id, extension=extension)


def _path_safe(name: str) -> str:
    """A stored file name as one segment of a path in the archive."""
    return _UNSAFE_PATH_CHARS.sub("_", name).strip(". ") or "file"


def _unique(name: str, record_id: str, taken: set[str]) -> str:
    """``name``, or ``name`` marked with the record's id if it is taken."""
    if name in taken:
        stem, dot, extension = name.rpartition(".")
        name = f"{stem}_{record_id}.{extension}" if dot else f"{name}_{record_id}"
    taken.add(name)
    return name


def _utc(value: datetime | None) -> str | None:
    """An instant as RFC 3339 UTC; a naive value is taken to be UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local(value: datetime | None) -> str | None:
    """A wall-clock time with no zone, as the instrument recorded it."""
    return value.replace(tzinfo=None).isoformat() if value is not None else None


def _json(document: Any) -> bytes:
    """
    A document as the package writes JSON: indented, UTF-8, one newline at
    the end. NaN and infinity, which JSON has no numbers for, become null.
    """
    text = json.dumps(
        non_finite_to_none(document), indent=2, ensure_ascii=False, allow_nan=False
    )
    return (text + "\n").encode("utf-8")
