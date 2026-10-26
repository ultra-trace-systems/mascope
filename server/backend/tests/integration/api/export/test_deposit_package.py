"""
Tests: the deposit package of a dataset (``mascope_backend.deposit``).

A dataset is built here with what a deposit has to carry: a metadata record,
two batches sharing a file, an Orbitrap raw file with an acquisition record, a
TOF file, a file whose raw file the filestore no longer keeps, an ionization
mode with a calibration collection, a matched collection with a compound's
identity and its ion, and an instrument function. Its package is built from
the test database and a filestore of the test's own, and read back as a
stranger would: the zip, its README, its checksums and its manifest.

The spreadsheets read their samples through ``sample_view``, which the test
schema does not create, so it is created here.
"""

import hashlib
import io
import json
import os
import uuid
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import text

from mascope_backend.api.controllers.sample.batches.export import service
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
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.views import Sample
from mascope_backend.deposit import (
    PACKAGE_SCHEMA,
    DatasetNotFound,
    build_package,
    collect_package,
)
from mascope_backend.runtime import runtime
from mascope_file.name import parse_path_from_item_filename


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DEPLOYMENT = "package-test"
_GLUCOSE_INCHIKEY = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
#: A batch name longer than a file name in the package keeps of it.
_LONG_NAME = "Second batch " + "x" * 150
_GLUCOSE_INCHI = "InChI=1S/C6H12O6/c7-1-2-3(8)4(9)5(10)6(11)12-2/h2-11H,1H2"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def package_sample_view(async_engine):
    """The spreadsheets read samples through ``sample_view``, which
    ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest.fixture(autouse=True)
def deployment(monkeypatch):
    """A configured deployment id, so global ids read no real filestore."""
    monkeypatch.setattr(runtime.config, "deployment_id", _DEPLOYMENT)


@pytest.fixture
def filestore(tmp_path, monkeypatch):
    """A filestore of the test's own."""
    root = tmp_path / "filestore"
    root.mkdir()
    monkeypatch.setattr(runtime.meta, "filestore", str(root))
    return root


@pytest.fixture(autouse=True)
def raw_header(monkeypatch):
    """The model an Orbitrap raw file's header names. The test raw files are
    not real acquisitions, so the reader is stood in for."""
    monkeypatch.setattr(
        service.m_metadata,
        "metadata_factory",
        lambda filename: SimpleNamespace(
            instrument_details={"Model": "Orbitrap Exploris 120"}
        ),
    )


def _store(filename: str, extension: str, content: bytes) -> str:
    """Put a raw file where the filestore keeps a sample file's."""
    directory = parse_path_from_item_filename(filename)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"data.{extension}")
    with open(path, "wb") as f:
        f.write(content)
    return path


#: The computer an acquisition record names, which no package may publish.
_HOST_NAME = "instrument-pc-x"
_MACHINE_GUID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


def _acquisition(sample_file_id: str) -> dict:
    """An acquisition record, its identifiers its own."""
    ids = {
        name: str(uuid.uuid5(uuid.NAMESPACE_OID, f"{name}:{sample_file_id}"))
        for name in ("acquisition_id", "step_id", "sequence_run_id", "agent_id")
    }
    return {
        "schema": "mascope-acquisition/1",
        "source_filename": "acquisition.raw",
        "ionization": "Br",
        "machine": {"name": _HOST_NAME, "windows_machine_guid": _MACHINE_GUID},
        **ids,
    }


@pytest_asyncio.fixture
async def dataset(async_session_factory, filestore):
    """
    A dataset of two batches. The first holds an Orbitrap sample, processed
    under an ionization mode and matched against a collection, and a TOF
    sample; the second the same Orbitrap file again, and a sample whose raw
    file the filestore no longer keeps.
    """
    ids = SimpleNamespace(
        workspace=gen_id(),
        dataset=gen_id(),
        first_batch=gen_id(),
        second_batch=gen_id(),
        orbi_file=gen_id(),
        tof_file=gen_id(),
        gone_file=gen_id(),
        orbi_item=gen_id(),
        tof_item=gen_id(),
        orbi_again_item=gen_id(),
        gone_item=gen_id(),
        mechanism=gen_id(),
        mode_only_mechanism=gen_id(),
        ion_only_mechanism=gen_id(),
        mode=gen_id(),
        matched=gen_id(),
        calibrants=gen_id(),
        compound=gen_id(),
        calibrant=gen_id(),
        ion=gen_id(),
        nitrate_ion=gen_id(),
        function=gen_id(32),
    )
    names = SimpleNamespace(
        orbi=f"instrument-x_2026.01.01-12h00m00s_{ids.orbi_file}",
        tof=f"instrument-y_2026.01.02-09h00m00s_{ids.tof_file}",
        gone=f"instrument-x_2026.01.03-10h00m00s_{ids.gone_file}",
    )
    raw = SimpleNamespace(
        orbi=b"orbitrap raw bytes " * 1000, tof=b"tof h5 bytes " * 500
    )
    _store(names.orbi, "raw", raw.orbi)
    _store(names.tof, "h5", raw.tof)

    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.workspace,
                workspace_name=f"Package WS {ids.workspace}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids.dataset,
                workspace_id=ids.workspace,
                dataset_name=f"Package DS {ids.dataset}",
                dataset_description="Ambient air, winter campaign.",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
                license="CC-BY-4.0",
                authors=[
                    {
                        "name": "Author One",
                        "orcid": "0000-0002-1825-0097",
                        "affiliation": "Lab A",
                    },
                    {"name": "Author Two", "orcid": None, "affiliation": None},
                ],
                keywords=["aerosol", "CI-MS"],
                related_identifiers=[
                    {
                        "identifier": "10.1234/example",
                        "relation": "IsSupplementTo",
                        "identifier_type": "DOI",
                    }
                ],
                funding=[{"funder": "Funder A", "award": "123"}],
            )
        )
        for batch_id, name, created in (
            (ids.first_batch, "First batch", _NOW),
            (ids.second_batch, _LONG_NAME, datetime(2026, 1, 5, tzinfo=_NOW.tzinfo)),
        ):
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=ids.dataset,
                    sample_batch_name=name,
                    sample_batch_utc_created=created,
                )
            )
        session.add(
            InstrumentFunction(
                instrument_function_id=ids.function,
                instrument="instrument-x",
                method_file="C:/methods/method-a.meth",
                datetime_utc=_NOW,
                peakshape={"x": [-1.0, 0.0, 1.0], "y": [0.1, 1.0, 0.1]},
                resolution_function={"model": "constant", "par": [120000.0]},
            )
        )
        await session.flush()
        session.add(
            SampleFile(
                sample_file_id=ids.orbi_file,
                filename=names.orbi,
                instrument="instrument-x",
                instrument_type="orbi",
                method_file="C:/methods/method-a.meth",
                datetime=datetime(2026, 1, 1, 14, 0, 0),
                datetime_utc=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[50.0, 750.0],
                mz_calibration={
                    "status": "ok",
                    "verified": True,
                    "quality": {"post_fit_mz_error_ppm": 0.42},
                    "seal": "not for export",
                },
                polarity="-",
                instrument_function_id=ids.function,
                acquisition=_acquisition(ids.orbi_file),
                **{
                    key: value
                    for key, value in _acquisition(ids.orbi_file).items()
                    if key.endswith("_id")
                },
                sha256=hashlib.sha256(raw.orbi).hexdigest(),
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids.tof_file,
                filename=names.tof,
                instrument="instrument-y",
                instrument_type="tof",
                datetime=datetime(2026, 1, 2, 9, 0, 0),
                datetime_utc=datetime(2026, 1, 2, 8, 0, 0, tzinfo=timezone.utc),
                # Not a number JSON has: the manifest gives it as null.
                length=float("nan"),
                range=[],
                polarity="+",
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids.gone_file,
                filename=names.gone,
                instrument="instrument-x",
                instrument_type="orbi",
                datetime=datetime(2026, 1, 3, 10, 0, 0),
                datetime_utc=datetime(2026, 1, 3, 8, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[50.0, 750.0],
                polarity="-",
            )
        )
        # One mechanism the mode and an ion share, one only the mode names
        # and one only an ion does.
        for mechanism_id, adduct in (
            (ids.mechanism, "[M+Br]-"),
            (ids.mode_only_mechanism, "[M-H]-"),
            (ids.ion_only_mechanism, "[M+NO3]-"),
        ):
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=mechanism_id,
                    ionization_mechanism_polarity="-",
                    ionization_mechanism=f"{adduct} {mechanism_id}",
                )
            )
        session.add(
            IonizationMode(
                ionization_mode_id=ids.mode,
                ionization_mode_name="Bromide package test",
                ionization_mode_token=f"Br-{ids.mode}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[ids.mechanism, ids.mode_only_mechanism],
                calibration_collection_id=ids.calibrants,
                diagnostic_collection_id=ids.matched,
            )
        )
        for collection_id, name, kind in (
            (ids.matched, "Matched compounds", "TARGETS"),
            (ids.calibrants, "Calibrants", "CALIBRANTS"),
        ):
            session.add(
                TargetCollection(
                    target_collection_id=collection_id,
                    target_collection_name=f"{name} {collection_id}",
                    target_collection_description="",
                    target_collection_type=kind,
                    workspace_id=ids.workspace,
                )
            )
        await session.flush()
        for item_id, batch_id, file_id, mode_id, name, polarity in (
            (ids.orbi_item, ids.first_batch, ids.orbi_file, ids.mode, "Orbi", "-"),
            (ids.tof_item, ids.first_batch, ids.tof_file, None, "TOF", "+"),
            (
                ids.orbi_again_item,
                ids.second_batch,
                ids.orbi_file,
                ids.mode,
                "Orbi again",
                "-",
            ),
            (ids.gone_item, ids.second_batch, ids.gone_file, None, "Gone", "-"),
        ):
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=file_id,
                    ionization_mode_id=mode_id,
                    sample_item_name=name,
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={"site": "roof"},
                    polarity=polarity,
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=ids.matched, sample_batch_id=ids.first_batch
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=ids.compound,
                target_compound_name="Glucose",
                target_compound_formula="C6H12O6",
                cas_number="50-99-7",
                inchikey=_GLUCOSE_INCHIKEY,
                inchi=_GLUCOSE_INCHI,
                smiles="OC[C@H]1OC(O)[C@H](O)[C@@H](O)[C@@H]1O",
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=ids.calibrant,
                target_compound_name="Calibrant",
                target_compound_formula="C2H4O2",
            )
        )
        await session.flush()
        for compound_id, collection_id in (
            (ids.compound, ids.matched),
            (ids.calibrant, ids.calibrants),
        ):
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound_id, target_collection_id=collection_id
                )
            )
        for ion_id, mechanism_id, formula in (
            (ids.ion, ids.mechanism, "C6H12O6Br-"),
            (ids.nitrate_ion, ids.ion_only_mechanism, "C6H12O6NO3-"),
        ):
            session.add(
                TargetIon(
                    target_ion_id=ion_id,
                    target_compound_id=ids.compound,
                    ionization_mechanism_id=mechanism_id,
                    target_ion_formula=formula,
                )
            )
        await session.commit()
    return SimpleNamespace(ids=ids, names=names, raw=raw)


@pytest.fixture
def out(tmp_path):
    """Where a test's archives go, apart from its filestore."""
    directory = tmp_path / "out"
    directory.mkdir()
    return directory


@pytest_asyncio.fixture
async def package(dataset, out, monkeypatch):
    """The dataset's package, built, and read back as a stranger would."""
    monkeypatch.setattr(runtime, "_version", "v9.9.9-test", raising=False)
    summary = await build_package(dataset.ids.dataset, str(out))
    archive = zipfile.ZipFile(summary["archive"])
    root = summary["package"]

    def read(path: str) -> bytes:
        return archive.read(f"{root}/{path}")

    return SimpleNamespace(
        summary=summary,
        archive=archive,
        root=root,
        read=read,
        paths=sorted(name.removeprefix(f"{root}/") for name in archive.namelist()),
        manifest=json.loads(read("manifest.json")),
        readme=read("README.md").decode(),
        licence=read("DATA_LICENSE").decode(),
    )


def _raw_entry(package, sample_file_id: str) -> dict:
    return next(
        entry
        for entry in package.manifest["raw"]
        if entry["sample_file_id"] == sample_file_id
    )


@pytest.mark.asyncio
async def test_the_archive_is_one_directory_named_after_the_dataset(
    package, dataset, out
):
    """Unzipped, the package is one directory, and the archive is named as
    it is: the dataset's id first, so a long name cannot cut it off."""
    assert (
        package.root
        == f"mascope-package_{dataset.ids.dataset}_Package_DS_{dataset.ids.dataset}"
    )
    assert package.summary["archive"] == str(out / f"{package.root}.zip")
    assert all(
        name.startswith(f"{package.root}/") for name in package.archive.namelist()
    )
    assert [path.name for path in out.iterdir()] == [f"{package.root}.zip"]
    # Every file is dated the package's creation, the time its manifest
    # gives, to the two seconds a zip records.
    created = datetime.strptime(package.manifest["created"], "%Y-%m-%dT%H:%M:%SZ")
    created = created.replace(second=created.second // 2 * 2)
    assert {info.date_time for info in package.archive.infolist()} == {
        created.timetuple()[:6]
    }


@pytest.mark.asyncio
async def test_the_package_holds_every_file_the_layout_names(package, dataset):
    names = dataset.names
    ids = dataset.ids
    assert package.paths == sorted(
        [
            "README.md",
            "DATA_LICENSE",
            "manifest.json",
            "SHA256SUMS",
            f"raw/{names.orbi}.raw",
            f"raw/{names.orbi}.raw.mascope.json",
            f"raw/{names.tof}.h5",
            f"batches/First_batch_{ids.first_batch}.xlsx",
            # The name is cut, the id after it is not.
            f"batches/Second_batch_{'x' * 67}_{ids.second_batch}.xlsx",
            f"target_collections/Calibrants_{ids.calibrants}_{ids.calibrants}.json",
            f"target_collections/Matched_compounds_{ids.matched}_{ids.matched}.json",
            "instrument_functions.json",
        ]
    )
    # README first, the checksums last, which hold the manifest's too.
    order = [
        name.removeprefix(f"{package.root}/") for name in package.archive.namelist()
    ]
    assert order[:2] == ["README.md", "DATA_LICENSE"]
    assert order[-2:] == ["manifest.json", "SHA256SUMS"]


@pytest.mark.asyncio
async def test_sha256sums_checks_every_other_file(package):
    """``sha256sum -c SHA256SUMS`` in the unzipped directory checks every
    file but itself, against the bytes in the archive."""
    lines = package.read("SHA256SUMS").decode().splitlines()
    listed = {}
    for line in lines:
        digest, path = line.split("  ", 1)
        listed[path] = digest
    assert sorted(listed) == [path for path in package.paths if path != "SHA256SUMS"]
    for path, digest in listed.items():
        assert hashlib.sha256(package.read(path)).hexdigest() == digest, path


@pytest.mark.asyncio
async def test_the_manifest_checksums_every_file_it_names(package):
    manifest = package.manifest
    named = [entry for entry in manifest["raw"] if entry["included"]]
    named += [entry["acquisition"] for entry in manifest["raw"] if entry["acquisition"]]
    named += [batch["spreadsheet"] for batch in manifest["batches"]]
    named += manifest["target_collections"]
    named += [manifest["instrument_functions"]]
    assert len(named) == len(package.paths) - 4
    for entry in named:
        data = package.read(entry["path"])
        assert entry["sha256"] == hashlib.sha256(data).hexdigest(), entry["path"]
        assert entry["bytes"] == len(data), entry["path"]


@pytest.mark.asyncio
async def test_raw_files_are_the_bytes_uploaded_one_entry_per_file(package, dataset):
    """A file two batches share is packed once, as the demo bundle's ``raw``
    entries give it: its name, its SHA-256 and its size."""
    names, raw, ids = dataset.names, dataset.raw, dataset.ids
    assert package.read(f"raw/{names.orbi}.raw") == raw.orbi
    assert package.read(f"raw/{names.tof}.h5") == raw.tof
    # In the order of their names.
    assert [entry["sample_file_id"] for entry in package.manifest["raw"]] == [
        ids.orbi_file,
        ids.gone_file,
        ids.tof_file,
    ]
    orbi = _raw_entry(package, ids.orbi_file)
    assert list(orbi)[:3] == ["name", "sha256", "bytes"]
    assert orbi["name"] == f"{names.orbi}.raw"
    assert orbi["sha256"] == hashlib.sha256(raw.orbi).hexdigest()
    assert orbi["sha256"] == orbi["sha256_at_upload"]
    assert package.summary["raw_files_changed"] == 0


@pytest.mark.asyncio
async def test_a_file_the_filestore_no_longer_keeps_is_described_not_packed(
    package, dataset
):
    gone = _raw_entry(package, dataset.ids.gone_file)
    assert gone["included"] is False
    assert gone["path"] is None and gone["sha256"] is None
    assert "keeps no raw file" in gone["reason"]
    assert gone["filename"] == dataset.names.gone
    assert package.summary["raw_files"] == 3
    assert package.summary["raw_files_included"] == 2
    assert "## Not included" in package.readme
    assert f"`{dataset.names.gone}`" in package.readme


@pytest.mark.asyncio
async def test_the_acquisition_record_lies_beside_its_raw_file(package, dataset):
    """The record the control program sent is written back as the sidecar
    it was, ``<raw file>.mascope.json``, but for the computer it names, and
    the manifest names its ids."""
    ids = dataset.ids
    record = json.loads(package.read(f"raw/{dataset.names.orbi}.raw.mascope.json"))
    sent = _acquisition(ids.orbi_file)
    assert sent.pop("machine")
    assert record == sent
    acquisition = _raw_entry(package, ids.orbi_file)["acquisition"]
    assert acquisition["acquisition_id"] == record["acquisition_id"]
    assert acquisition["step_id"] == record["step_id"]
    assert acquisition["sequence_run_id"] == record["sequence_run_id"]
    assert acquisition["agent_id"] == record["agent_id"]
    assert _raw_entry(package, ids.tof_file)["acquisition"] is None


@pytest.mark.asyncio
async def test_no_file_of_the_package_names_the_acquiring_computer(
    package, dataset, async_session_factory
):
    """The host name and machine GUID of the computer that wrote an
    acquisition record are in no file of the package, while the database
    keeps the record whole."""
    for path in package.paths:
        data = package.read(path)
        assert _HOST_NAME.encode() not in data, path
        assert _MACHINE_GUID.encode() not in data, path
    async with async_session_factory() as session:
        stored = await session.get(SampleFile, dataset.ids.orbi_file)
        await session.refresh(stored, ["acquisition"])
        assert stored.acquisition == _acquisition(dataset.ids.orbi_file)


@pytest.mark.asyncio
async def test_each_raw_file_carries_its_acquisition_context(package, dataset):
    ids = dataset.ids
    orbi = _raw_entry(package, ids.orbi_file)
    assert orbi["sample_file_global_id"] == f"{_DEPLOYMENT}:sample_file:{ids.orbi_file}"
    assert orbi["instrument"] == "instrument-x"
    assert orbi["instrument_model"] == "Orbitrap Exploris 120"
    assert orbi["method_file"] == "C:/methods/method-a.meth"
    assert orbi["datetime"] == "2026-01-01T14:00:00"
    assert orbi["datetime_utc"] == "2026-01-01T12:00:00Z"
    assert orbi["range"] == [50.0, 750.0]
    assert orbi["instrument_function_id"] == ids.function
    # The record's verdict, not the record: its seal stays in Mascope.
    assert orbi["mz_calibration"] == {
        "status": "ok",
        "verified": True,
        "post_fit_mz_error_ppm": 0.42,
    }
    assert orbi["cv_terms"] == {
        "instrument_model": {
            "accession": "MS:1003095",
            "name": "Orbitrap Exploris 120",
        },
        "ion_source": {"accession": "MS:1000071", "name": "chemical ionization"},
        "mass_analyser": {"accession": "MS:1000484", "name": "orbitrap"},
        "detector": {"accession": "MS:1000624", "name": "inductive detector"},
        "polarity": [{"accession": "MS:1000129", "name": "negative scan"}],
    }
    tof = _raw_entry(package, ids.tof_file)
    assert tof["length"] is None
    assert tof["instrument_model"] is None
    assert tof["cv_terms"]["mass_analyser"]["accession"] == "MS:1000084"
    assert tof["cv_terms"]["polarity"] == [
        {"accession": "MS:1000130", "name": "positive scan"}
    ]
    assert package.manifest["measurement"] == {
        "instruments": ["instrument-x", "instrument-y"],
        "instrument_types": ["orbi", "tof"],
        "instrument_models": ["Orbitrap Exploris 120"],
        "acquired": {"first": "2026-01-01T12:00:00Z", "last": "2026-01-03T08:00:00Z"},
    }


@pytest.mark.asyncio
async def test_the_metadata_record_is_the_datasets(package, dataset):
    ids = dataset.ids
    record = package.manifest["dataset"]
    assert record == {
        "dataset_id": ids.dataset,
        "dataset_global_id": f"{_DEPLOYMENT}:dataset:{ids.dataset}",
        "dataset_name": f"Package DS {ids.dataset}",
        "dataset_description": "Ambient air, winter campaign.",
        "dataset_type": "ANALYSIS",
        "instrument": None,
        "dataset_utc_created": "2026-01-01T00:00:00Z",
        "dataset_utc_modified": record["dataset_utc_modified"],
        "license": "CC-BY-4.0",
        "authors": [
            {
                "name": "Author One",
                "orcid": "0000-0002-1825-0097",
                "affiliation": "Lab A",
            },
            {"name": "Author Two", "orcid": None, "affiliation": None},
        ],
        "keywords": ["aerosol", "CI-MS"],
        "related_identifiers": [
            {
                "identifier": "10.1234/example",
                "relation": "IsSupplementTo",
                "identifier_type": "DOI",
            }
        ],
        "funding": [{"funder": "Funder A", "award": "123"}],
    }


@pytest.mark.asyncio
async def test_the_readme_tells_a_stranger_what_how_who_and_which_software(
    package, dataset
):
    ids = dataset.ids
    readme = package.readme
    assert readme.startswith(f"# Package DS {dataset.ids.dataset}\n")
    assert "Ambient air, winter campaign." in readme
    assert "| Licence | CC-BY-4.0 |" in readme
    assert (
        "Author One (ORCID [0000-0002-1825-0097](https://orcid.org/0000-0002-1825-0097)), Lab A; Author Two"
        in readme
    )
    assert "IsSupplementTo: 10.1234/example (DOI)" in readme
    assert "Funder A, 123" in readme
    assert "3 sample files from instrument-x, instrument-y" in readme
    assert "Orbitrap Exploris 120 (MS:1003095)" in readme
    assert (
        "Ionization mode **Bromide package test**, polarity -, ions formed as "
        f"\\[M+Br\\]- {ids.mechanism}, \\[M-H\\]- {ids.mode_only_mechanism}, "
        f"m/z calibrated against **Calibrants {ids.calibrants}**."
    ) in readme
    assert (
        f"- Target collection **Calibrants {ids.calibrants}** (calibration): "
        "1 compound, 0 ions."
    ) in readme
    assert (
        f"- Target collection **Matched compounds {ids.matched}** "
        "(matched, diagnostic): 1 compound, 2 ions."
    ) in readme
    # The commonest first: two samples were acquired negative, one positive.
    assert (
        "- Polarities: negative scan (MS:1000129), positive scan (MS:1000130)."
    ) in readme
    for row in (
        "`raw/`",
        "`raw/*.mascope.json`",
        "`batches/`",
        "`target_collections/`",
    ):
        assert f"| {row} |" in readme
    assert "without the computer it names (`machine`)" in readme
    assert "may name the instrument and the computer that acquired it" in readme
    assert "Batch **First batch**: 2 samples" in readme
    assert "Mascope v9.9.9-test" in readme
    assert f"deployment `{_DEPLOYMENT}`" in readme
    assert "https://doi.org/10.5281/zenodo.21037634" in readme
    assert "sha256sum -c SHA256SUMS" in readme
    assert "https://creativecommons.org/licenses/by/4.0/" in package.licence


@pytest.mark.asyncio
async def test_provenance_names_the_dataset_its_batches_and_the_build(package, dataset):
    ids = dataset.ids
    manifest = package.manifest
    assert manifest["schema"] == PACKAGE_SCHEMA
    provenance = manifest["provenance"]
    assert provenance["deployment_id"] == _DEPLOYMENT
    assert provenance["inputs"] == {
        "dataset_id": ids.dataset,
        "dataset_global_id": f"{_DEPLOYMENT}:dataset:{ids.dataset}",
        "sample_batch_ids": [ids.first_batch, ids.second_batch],
    }
    assert manifest["created"] == provenance["generated_utc"]
    # The demo bundle's produced_with: the provenance block's, and the reader.
    produced = manifest["produced_with"]
    assert produced["mascope_version"] == "v9.9.9-test"
    assert set(provenance["produced_with"]) < set(produced)
    assert produced["opentfraw_version"].startswith("opentfraw==")
    assert manifest["vocabulary"] == {"PSI-MS": "4.2.6"}


@pytest.mark.asyncio
async def test_each_batch_is_its_spreadsheet_and_its_samples(package, dataset):
    """A batch's spreadsheet in the package is the app's export of it."""
    ids = dataset.ids
    first, second = package.manifest["batches"]
    assert (
        first["sample_batch_global_id"]
        == f"{_DEPLOYMENT}:sample_batch:{ids.first_batch}"
    )
    assert first["target_collection_ids"] == [ids.matched]
    assert second["target_collection_ids"] == []
    assert [sample["sample_item_id"] for sample in first["samples"]] == [
        ids.orbi_item,
        ids.tof_item,
    ]
    orbi = first["samples"][0]
    assert orbi == {
        "sample_item_id": ids.orbi_item,
        "sample_item_global_id": f"{_DEPLOYMENT}:sample_item:{ids.orbi_item}",
        "sample_item_name": "Orbi",
        "sample_item_type": "ANALYSIS",
        "sample_file_id": ids.orbi_file,
        "polarity": "-",
        "t0": 0.0,
        "t1": 60.0,
        "ionization_mode_id": ids.mode,
        "filter_id": None,
        "sample_item_attributes": {"site": "roof"},
    }
    # A spreadsheet is a zip already, so it is stored as it is.
    info = package.archive.getinfo(f"{package.root}/{first['spreadsheet']['path']}")
    assert info.compress_type == zipfile.ZIP_STORED
    with pd.ExcelFile(io.BytesIO(package.read(first["spreadsheet"]["path"]))) as book:
        assert book.sheet_names == ["Batch", "Samples", "Provenance"]
        samples = pd.read_excel(book, "Samples")
        assert list(samples["Sample item ID"]) == [ids.orbi_item, ids.tof_item]
        batch_rows = pd.read_excel(book, "Batch", header=None)
        assert batch_rows.iloc[0].tolist() == ["Name", "First batch"]


@pytest.mark.asyncio
async def test_collections_carry_compound_identity_and_their_ions(package, dataset):
    """Both the collection a batch was matched against and the one its
    ionization mode calibrates with are packed, each saying which it is."""
    ids = dataset.ids
    entries = {
        entry["target_collection_id"]: entry
        for entry in package.manifest["target_collections"]
    }
    assert entries[ids.matched]["used_as"] == ["matched", "diagnostic"]
    assert entries[ids.calibrants]["used_as"] == ["calibration"]
    assert entries[ids.matched]["target_compounds"] == 1
    assert entries[ids.matched]["target_ions"] == 2
    document = json.loads(package.read(entries[ids.matched]["path"]))
    assert document["target_collection_global_id"] == (
        f"{_DEPLOYMENT}:target_collection:{ids.matched}"
    )
    (compound,) = document["target_compounds"]
    assert compound["target_compound_global_id"] == (
        f"{_DEPLOYMENT}:target_compound:{ids.compound}"
    )
    assert compound["cas_number"] == "50-99-7"
    assert compound["inchikey"] == _GLUCOSE_INCHIKEY
    assert compound["inchi"] == _GLUCOSE_INCHI
    assert compound["smiles"].startswith("OC[C@H]1")
    assert compound["target_ions"] == [
        {
            "target_ion_id": ids.ion,
            "target_ion_global_id": f"{_DEPLOYMENT}:target_ion:{ids.ion}",
            "target_ion_formula": "C6H12O6Br-",
            "ionization_mechanism_id": ids.mechanism,
            "ionization_mechanism": f"[M+Br]- {ids.mechanism}",
            "ionization_mechanism_polarity": "-",
        },
        {
            "target_ion_id": ids.nitrate_ion,
            "target_ion_global_id": f"{_DEPLOYMENT}:target_ion:{ids.nitrate_ion}",
            "target_ion_formula": "C6H12O6NO3-",
            "ionization_mechanism_id": ids.ion_only_mechanism,
            "ionization_mechanism": f"[M+NO3]- {ids.ion_only_mechanism}",
            "ionization_mechanism_polarity": "-",
        },
    ]


@pytest.mark.asyncio
async def test_the_ionization_setup_and_instrument_functions_are_packed(
    package, dataset
):
    ids = dataset.ids
    assert package.manifest["ionization_modes"] == [
        {
            "ionization_mode_id": ids.mode,
            "ionization_mode_name": "Bromide package test",
            "ionization_mode_token": f"Br-{ids.mode}",
            "instrument": None,
            "ionization_mode_polarity": "-",
            "system_key": None,
            "ionization_mechanisms": [
                {
                    "ionization_mechanism_id": ids.mechanism,
                    "ionization_mechanism": f"[M+Br]- {ids.mechanism}",
                    "ionization_mechanism_polarity": "-",
                },
                {
                    "ionization_mechanism_id": ids.mode_only_mechanism,
                    "ionization_mechanism": f"[M-H]- {ids.mode_only_mechanism}",
                    "ionization_mechanism_polarity": "-",
                },
            ],
            "calibration_collection_id": ids.calibrants,
            "diagnostic_collection_id": ids.matched,
        }
    ]
    functions = json.loads(package.read("instrument_functions.json"))
    assert functions == {
        "instrument_functions": [
            {
                "instrument_function_id": ids.function,
                "instrument": "instrument-x",
                "method_file": "C:/methods/method-a.meth",
                "datetime_utc": "2026-01-01T00:00:00Z",
                "peakshape": {"x": [-1.0, 0.0, 1.0], "y": [0.1, 1.0, 0.1]},
                "resolution_function": {"model": "constant", "par": [120000.0]},
            }
        ]
    }


@pytest.mark.asyncio
async def test_a_raw_file_changed_since_upload_is_reported(
    dataset, out, async_session_factory, monkeypatch
):
    """The upload hash is kept beside the one taken now, and a file that
    differs is counted and warned of, so the operator sees it before
    depositing."""
    warnings = []
    monkeypatch.setattr(runtime.logger, "warning", warnings.append)
    async with async_session_factory() as session:
        sample_file = await session.get(SampleFile, dataset.ids.orbi_file)
        sample_file.sha256 = "0" * 64
        await session.commit()
    summary = await build_package(dataset.ids.dataset, str(out / "changed.zip"))
    assert summary["archive"] == str(out / "changed.zip")
    assert summary["raw_files_changed"] == 1
    assert [w for w in warnings if dataset.names.orbi in w] == [
        f"The raw file of '{dataset.names.orbi}' is not the file that was "
        "uploaded: its SHA-256 differs from the one recorded at upload."
    ]
    with zipfile.ZipFile(summary["archive"]) as archive:
        manifest = json.loads(archive.read(f"{summary['package']}/manifest.json"))
    orbi = next(
        e for e in manifest["raw"] if e["sample_file_id"] == dataset.ids.orbi_file
    )
    assert orbi["sha256_at_upload"] == "0" * 64
    assert orbi["sha256"] == hashlib.sha256(dataset.raw.orbi).hexdigest()


@pytest.mark.asyncio
async def test_a_deployment_without_an_id_gets_no_global_ids(dataset, monkeypatch):
    """The provenance block's rule: no global id rather than a made-up one.
    The test's filestore has no deployment file, so with none configured the
    deployment has no id."""
    monkeypatch.setattr(runtime.config, "deployment_id", None)
    package = await collect_package(dataset.ids.dataset)
    manifest = package.manifest
    assert "dataset_global_id" not in manifest["dataset"]
    assert all("sample_file_global_id" not in entry for entry in manifest["raw"])
    assert all(
        "sample_item_global_id" not in sample
        for batch in manifest["batches"]
        for sample in batch["samples"]
    )
    assert manifest["provenance"]["deployment_id"] is None


@pytest.mark.asyncio
async def test_an_unknown_dataset_is_refused(filestore):
    with pytest.raises(DatasetNotFound):
        await collect_package("no-such-dataset")


@pytest.mark.asyncio
async def test_a_build_that_fails_leaves_no_archive(dataset, out, monkeypatch):
    """A spreadsheet that cannot be built fails the build, and neither the
    archive nor its partial file is left behind."""

    async def broken(sample_batch_id):
        raise RuntimeError("no spreadsheet")

    monkeypatch.setattr(service, "build_batch_sheets", broken)
    with pytest.raises(RuntimeError, match="no spreadsheet"):
        await build_package(dataset.ids.dataset, str(out / "failed.zip"))
    assert list(out.iterdir()) == []
