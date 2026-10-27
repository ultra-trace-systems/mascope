"""
The order of the MS1 golden exports (``export_goldens.get_golden_peaks`` and
``get_golden_ions``), against the database.

Two target ions of one formula are common in a real collection: an oxygenate's
``[M+NH4]+`` and an amine's ``[M+H]+`` are the same ion. Matching records a row
for each, on the same peak with the same score, so file, formula and m/z leave
the pair tied. The export breaks the tie on the target id, which the restored
seed keeps across a rebuild; without it the pair comes out in whichever order
the database read it, and two rebuilds of one bundle write different files.

The rows are inserted with the higher id first, so a read in insertion order
is the wrong one.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    MatchIon,
    MatchIsotope,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCompound,
    TargetIon,
    TargetIsotope,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.scripts import export_goldens


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

_FORMULA = "C10H20NO2+"
_MZ = 186.14886


@pytest_asyncio.fixture(scope="module")
async def isomers(async_session_factory) -> SimpleNamespace:
    """One sample whose two isomeric target ions both matched one peak, the
    ion with the higher id stored first."""
    ids = SimpleNamespace(
        workspace=gen_id(),
        dataset=gen_id(),
        batch=gen_id(),
        file=gen_id(),
        item=gen_id(),
        filename=f"golden-order_{gen_id()}",
        # The ion ids sort "0..." before "z..."; their isotopes follow suit.
        ions=[f"z{gen_id(15)}", f"0{gen_id(15)}"],
        isotopes=[f"z{gen_id(15)}", f"0{gen_id(15)}"],
    )
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids.workspace,
                workspace_name=f"Golden order WS {ids.workspace}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids.dataset,
                workspace_id=ids.workspace,
                dataset_name=f"Golden order DS {ids.dataset}",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids.batch,
                dataset_id=ids.dataset,
                sample_batch_name="Golden order batch",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids.file,
                filename=ids.filename,
                instrument="test-orbion",
                datetime=datetime(2026, 1, 1),
                datetime_utc=_NOW,
                length=60.0,
                range=[40.0, 600.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=ids.item,
                sample_batch_id=ids.batch,
                sample_file_id=ids.file,
                sample_item_name="Golden order item",
                sample_item_type="sample",
                polarity="+",
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()

    # One compound and mechanism per ion: the oxygenate's [M+NH4]+ and the
    # amine's [M+H]+. Each ion, its isotope and its matches go in a commit of
    # their own, the higher id first.
    for ion_id, isotope_id, compound, mechanism in zip(
        ids.ions,
        ids.isotopes,
        ("C10H16O2", "C10H19NO2"),
        ("[M+NH4]+", "[M+H]+"),
        strict=True,
    ):
        compound_id, mechanism_id = gen_id(), gen_id()
        async with async_session_factory() as session:
            session.add(
                TargetCompound(
                    target_compound_id=compound_id,
                    target_compound_formula=compound,
                )
            )
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=mechanism_id,
                    ionization_mechanism_polarity="+",
                    ionization_mechanism=f"{mechanism} {mechanism_id}",
                )
            )
            await session.flush()
            session.add(
                TargetIon(
                    target_ion_id=ion_id,
                    target_compound_id=compound_id,
                    ionization_mechanism_id=mechanism_id,
                    target_ion_formula=_FORMULA,
                )
            )
            await session.flush()
            session.add(
                TargetIsotope(
                    target_isotope_id=isotope_id,
                    target_ion_id=ion_id,
                    target_isotope_formula=_FORMULA,
                    mz=_MZ,
                    relative_abundance=1.0,
                    resolution="HIGH",
                )
            )
            await session.flush()
            session.add(
                MatchIon(
                    match_ion_id=gen_id(32),
                    sample_item_id=ids.item,
                    target_ion_id=ion_id,
                    match_score=0.9,
                    match_category=2,
                    sample_peak_intensity_sum=1000.0,
                    match_ion_utc_created=_NOW,
                )
            )
            session.add(
                MatchIsotope(
                    match_isotope_id=gen_id(32),
                    target_isotope_id=isotope_id,
                    sample_item_id=ids.item,
                    sample_peak_id="shared-peak",
                    sample_peak_mz=_MZ,
                    sample_peak_intensity=1000.0,
                    sample_peak_intensity_relative=1.0,
                    sample_peak_tof=0.0,
                    match_abundance_error=0.0,
                    match_mz_error=0.0,
                    match_score=0.9,
                    match_isotope_utc_created=_NOW,
                )
            )
            await session.commit()
    return ids


# The test database is bound by the conftest; the export's own engine
# configuration would rebind it to the runtime's.
_bound = patch.object(export_goldens, "configure_database_engine", AsyncMock())


@pytest.mark.asyncio
async def test_isomeric_ions_are_ordered_by_target_ion_id(isomers):
    with _bound:
        rows = await export_goldens._query_golden_ions()

    ours = [r for r in rows if r["filename"] == isomers.filename]
    assert [r["target_ion_id"] for r in ours] == sorted(isomers.ions)
    assert {r["target_ion_formula"] for r in ours} == {_FORMULA}


@pytest.mark.asyncio
async def test_isomeric_peaks_are_ordered_by_target_isotope_id(isomers):
    with _bound:
        rows = await export_goldens._query_golden_peaks()

    ours = [r for r in rows if r["filename"] == isomers.filename]
    assert [r["target_isotope_id"] for r in ours] == sorted(isomers.isotopes)
    assert {(r["target_isotope_formula"], r["mz"]) for r in ours} == {(_FORMULA, _MZ)}
