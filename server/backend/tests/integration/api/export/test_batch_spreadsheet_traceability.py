"""
Tests: the batch spreadsheet export can be traced back to what produced it.

An exported spreadsheet names the dataset, batch and target collections it was
built from, gives every row the id of the record behind it, carries each
sample's acquisition context - instrument, method, polarity, m/z range, UTC
time, ionization mode, instrument function, calibration verdict - and ends
with a Provenance sheet naming the build that wrote it.

All of it is appended: the sheets, columns and rows a reader of an earlier
export knows keep their names and their places, which these tests pin as
well. The export reads its samples through ``sample_view``, which the test
schema does not create, so it is created here.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import text

from mascope_backend.api.controllers.sample.batches.export import service
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    MatchCompound,
    MatchIon,
    MatchSample,
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
from mascope_backend.runtime import runtime


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

# Undecorated: the background-task notification machinery is not under test.
_export = service.sample_batch_export_spreadsheet.__wrapped__

#: The columns of the sheets as they were before ids and acquisition context
#: were added, in their order. A reader that picks columns by position must
#: keep finding these where they were.
LEGACY_SAMPLE_COLUMNS = [
    "Sample name",
    "Filename",
    "Datetime",
    "Sample type",
    "TIC",
    "Filter ID",
    "Total peak intensity (cps)",
    "Match score",
]
LEGACY_COMPOUND_COLUMNS = [
    "Sample name",
    "Filename",
    "Sample type",
    "Compound name",
    "Compound formula",
    "Total peak intensity (cps)",
    "Match score",
]
LEGACY_ION_COLUMNS = [
    "Sample name",
    "Filename",
    "Sample type",
    "Compound name",
    "Compound formula",
    "Ionization mechanism",
    "Ion formula",
    "Total peak intensity (cps)",
    "Match score",
]
LEGACY_BATCH_LABELS = [
    "Name",
    "Description",
    "Type",
    "Polarity",
    "Dataset",
    "",
    "Target collections",
    "",
    "Total Samples",
    "Total Compounds",
    "Total Ions",
    "Total Isotopes",
]


@pytest_asyncio.fixture(scope="module", autouse=True)
async def export_sample_view(async_engine):
    """The export reads samples through ``sample_view``, which
    ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest.fixture(autouse=True)
def configured_deployment(monkeypatch):
    """A configured id, so the Provenance sheet reads no real filestore."""
    monkeypatch.setattr(runtime.config, "deployment_id", "export-test")


def _acquisition_of(sample_file_id: str) -> dict:
    """The identifiers of a file's acquisition record, its own for each file."""
    return {
        name: str(uuid.uuid5(uuid.NAMESPACE_OID, f"{name}:{sample_file_id}"))
        for name in ("acquisition_id", "step_id", "sequence_run_id", "agent_id")
    }


@pytest_asyncio.fixture
async def batch(async_session_factory):
    """
    A batch of two samples: one from a calibrated file, processed under an
    ionization mode and matched against one compound; one from a file with
    no calibration, no method and no mode, and no matches.
    """
    ids = SimpleNamespace(
        workspace=gen_id(),
        dataset=gen_id(),
        batch=gen_id(),
        calibrated_file=gen_id(),
        bare_file=gen_id(),
        matched_item=gen_id(),
        bare_item=gen_id(),
        mechanism=gen_id(),
        mode=gen_id(),
        collection=gen_id(),
        compound=gen_id(),
        ion=gen_id(),
    )
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.workspace,
                workspace_name=f"Export WS {ids.workspace}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids.dataset,
                workspace_id=ids.workspace,
                dataset_name=f"Export DS {ids.dataset}",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids.batch,
                dataset_id=ids.dataset,
                sample_batch_name="Traceable batch",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids.calibrated_file,
                filename=f"instrument-x_{ids.calibrated_file}.raw",
                instrument="instrument-x",
                instrument_type="orbi",
                method_file="C:/methods/method-a.meth",
                datetime=datetime(2026, 1, 1, 14, 0, 0),
                datetime_utc=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[50.0, 750.0],
                mz_calibration={
                    "mode": "one-point",
                    "par": {"calibration_factor": 1.0},
                    "status": "ok",
                    "verified": True,
                    "quality": {"n_points": 5, "post_fit_mz_error_ppm": 0.42},
                    "seal": "not for export",
                },
                polarity="neg",
                # This file came with an acquisition record and a verified
                # hash; the other one below came with neither.
                **_acquisition_of(ids.calibrated_file),
                sha256="ab" * 32,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids.bare_file,
                filename=f"instrument-y_{ids.bare_file}.raw",
                instrument="instrument-y",
                instrument_type="tof",
                datetime=datetime(2026, 1, 2, 9, 0, 0),
                datetime_utc=datetime(2026, 1, 2, 8, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[],
                polarity="neg",
            )
        )
        session.add(
            IonizationMechanism(
                ionization_mechanism_id=ids.mechanism,
                ionization_mechanism_polarity="-",
                ionization_mechanism=f"[M+Br]- {ids.mechanism}",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids.mode,
                ionization_mode_name="Bromide export test",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[ids.mechanism],
            )
        )
        for item_id, file_id, mode_id, name in (
            (ids.matched_item, ids.calibrated_file, ids.mode, "Matched sample"),
            (ids.bare_item, ids.bare_file, None, "Bare sample"),
        ):
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=ids.batch,
                    sample_file_id=file_id,
                    ionization_mode_id=mode_id,
                    sample_item_name=name,
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={},
                    polarity="-",
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        session.add(
            TargetCollection(
                target_collection_id=ids.collection,
                target_collection_name=f"Export collection {ids.collection}",
                target_collection_description="",
                target_collection_type="TARGETS",
                workspace_id=ids.workspace,
            )
        )
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=ids.collection, sample_batch_id=ids.batch
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=ids.compound,
                target_compound_name="Glucose",
                target_compound_formula="C6H12O6",
            )
        )
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=ids.compound, target_collection_id=ids.collection
            )
        )
        session.add(
            TargetIon(
                target_ion_id=ids.ion,
                target_compound_id=ids.compound,
                ionization_mechanism_id=ids.mechanism,
                target_ion_formula="C6H12O6Br-",
            )
        )
        session.add(
            MatchSample(
                match_sample_id=gen_id(32),
                sample_item_id=ids.matched_item,
                match_score=0.9,
                match_category=2,
                sample_peak_intensity_sum=1000.0,
            )
        )
        session.add(
            MatchCompound(
                match_compound_id=gen_id(32),
                sample_item_id=ids.matched_item,
                target_compound_id=ids.compound,
                match_score=0.9,
                match_category=2,
                sample_peak_intensity_sum=1000.0,
            )
        )
        session.add(
            MatchIon(
                match_ion_id=gen_id(32),
                sample_item_id=ids.matched_item,
                target_ion_id=ids.ion,
                match_score=0.9,
                match_category=2,
                sample_peak_intensity_sum=1000.0,
            )
        )
        await session.commit()
    return ids


@pytest_asyncio.fixture
async def workbook(batch, tmp_path, monkeypatch, test_users):
    """Export the batch and read every sheet back."""
    # The directory, not user_temp_path: that helper is what keeps a file
    # name inside the user's directory, and the export's naming goes with it.
    monkeypatch.setattr(
        "mascope_backend.api.new.temp.storage.user_temp_dir",
        lambda user_id, create=True: str(tmp_path),
    )
    monkeypatch.setattr(runtime, "_version", "v9.9.9-test", raising=False)

    result = await _export(
        batch.batch, user_id=test_users["editor"].id, process_id="export-test"
    )

    with pd.ExcelFile(tmp_path / result["data"]["file"]) as book:
        return SimpleNamespace(
            sheet_names=book.sheet_names,
            batch_rows=_key_values(pd.read_excel(book, "Batch", header=None)),
            samples=pd.read_excel(book, "Samples"),
            compounds=pd.read_excel(book, "Match compounds"),
            ions=pd.read_excel(book, "Match ions"),
            provenance=dict(
                _key_values(pd.read_excel(book, "Provenance", header=None))
            ),
        )


def _key_values(frame: pd.DataFrame) -> list[tuple]:
    """A two-column sheet as (label, value) rows, blanks as empty labels."""
    frame = frame.reindex(columns=range(2))
    return [
        ("" if pd.isna(label) else label, None if pd.isna(value) else value)
        for label, value in frame.itertuples(index=False)
    ]


@pytest.mark.asyncio
async def test_the_sheets_keep_their_order_and_provenance_comes_last(workbook):
    assert workbook.sheet_names == [
        "Batch",
        "Samples",
        "Match compounds",
        "Match ions",
        "Provenance",
    ]


@pytest.mark.asyncio
async def test_the_batch_sheet_names_the_records_it_was_built_from(workbook, batch):
    labels = [label for label, _ in workbook.batch_rows]
    assert labels[: len(LEGACY_BATCH_LABELS)] == LEGACY_BATCH_LABELS

    values = dict(workbook.batch_rows)
    assert values["Dataset ID"] == batch.dataset
    assert values["Sample batch ID"] == batch.batch
    assert values["Target collection IDs"] == batch.collection
    # A batch can span instruments; every one is named
    assert values["Instruments"] == "instrument-x, instrument-y"
    assert values["Method files"] == "C:/methods/method-a.meth"


@pytest.mark.asyncio
async def test_every_sample_row_carries_its_ids_and_acquisition_context(
    workbook, batch
):
    samples = workbook.samples
    assert list(samples.columns[: len(LEGACY_SAMPLE_COLUMNS)]) == (
        LEGACY_SAMPLE_COLUMNS
    )

    rows = samples.set_index("Sample item ID")
    matched = rows.loc[batch.matched_item]
    assert matched["Sample file ID"] == batch.calibrated_file
    assert matched["Instrument"] == "instrument-x"
    assert matched["Instrument type"] == "orbi"
    assert matched["Method file"] == "C:/methods/method-a.meth"
    assert matched["Polarity"] == "-"
    assert matched["m/z range min"] == 50.0
    assert matched["m/z range max"] == 750.0
    # Excel holds no time zones, so the UTC column is written as naive UTC
    assert pd.Timestamp(matched["Datetime UTC"]) == pd.Timestamp("2026-01-01 12:00:00")
    assert matched["Ionization mode"] == "Bromide export test"
    assert matched["Ionization mode ID"] == batch.mode
    assert matched["m/z calibration"] == "ok"
    assert bool(matched["m/z calibration verified"]) is True
    assert matched["m/z calibration error (ppm)"] == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_a_sample_row_names_its_acquisition_and_its_files_hash(workbook, batch):
    """What the instrument's control program said of the file, by identifier:
    the way from an exported row back to the run and the step behind it."""
    rows = workbook.samples.set_index("Sample item ID")
    matched = rows.loc[batch.matched_item]
    said = _acquisition_of(batch.calibrated_file)

    assert matched["Acquisition ID"] == said["acquisition_id"]
    assert matched["Step ID"] == said["step_id"]
    assert matched["Sequence run ID"] == said["sequence_run_id"]
    assert matched["Agent ID"] == said["agent_id"]
    assert matched["File SHA-256"] == "ab" * 32

    # A file that came with no record leaves them empty.
    bare = rows.loc[batch.bare_item]
    for column in (
        "Acquisition ID",
        "Step ID",
        "Sequence run ID",
        "Agent ID",
        "File SHA-256",
    ):
        assert pd.isna(bare[column]), column


@pytest.mark.asyncio
async def test_a_sample_without_calibration_mode_or_method_says_so(workbook, batch):
    """Absence is legible: no calibration record reads as ``none``, not as a
    blank that could be a lost value."""
    bare = workbook.samples.set_index("Sample item ID").loc[batch.bare_item]

    assert bare["Sample file ID"] == batch.bare_file
    assert bare["m/z calibration"] == "none"
    assert pd.isna(bare["m/z calibration verified"])
    assert pd.isna(bare["Ionization mode"])
    assert pd.isna(bare["Method file"])
    assert pd.isna(bare["m/z range min"]) and pd.isna(bare["m/z range max"])


@pytest.mark.asyncio
async def test_the_calibration_fit_itself_is_not_exported(workbook):
    """The verdict is exported, not the sealed fit: its parameters mean nothing
    outside Mascope, and the seal is this server's own."""
    cells = [str(cell) for cell in workbook.samples.to_numpy().ravel()]
    assert not any("not for export" in cell for cell in cells)


@pytest.mark.asyncio
async def test_every_match_row_names_its_sample_and_target(workbook, batch):
    compounds, ions = workbook.compounds, workbook.ions
    assert list(compounds.columns[: len(LEGACY_COMPOUND_COLUMNS)]) == (
        LEGACY_COMPOUND_COLUMNS
    )
    assert list(ions.columns[: len(LEGACY_ION_COLUMNS)]) == LEGACY_ION_COLUMNS

    compound = compounds.iloc[0]
    assert compound["Sample item ID"] == batch.matched_item
    assert compound["Target compound ID"] == batch.compound

    ion = ions.iloc[0]
    assert ion["Sample item ID"] == batch.matched_item
    assert ion["Target compound ID"] == batch.compound
    assert ion["Target ion ID"] == batch.ion


@pytest.mark.asyncio
async def test_the_provenance_sheet_names_the_build_and_the_inputs(workbook, batch):
    provenance = workbook.provenance

    assert provenance["provenance_version"] == 1
    assert provenance["deployment_id"] == "export-test"
    assert provenance["produced_with.mascope_version"] == "v9.9.9-test"
    assert "produced_with.match_score_version" in provenance
    assert "produced_with.peak_assignment_engine_version" in provenance
    assert provenance["inputs.dataset_id"] == batch.dataset
    assert provenance["inputs.sample_batch_id"] == batch.batch
    assert provenance["inputs.target_collection_ids"] == batch.collection
