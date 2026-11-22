"""
The rebuild of the stored TOF isotope lines (#3131): which ions it finds
stale, what a run replaces and removes, and what it leaves alone.

The database is the unit tests' own. The script opens sessions of its own,
so the fixture commits its rows and deletes them after each test; the
scopes that would reach other tests' rows are only ever dry runs.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from test_utils import gen_test_id

from mascope_backend.api.controllers.target.isotopes.target_isotopes_controller import (
    get_target_isotopes,
)
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    MatchCollection,
    MatchCompound,
    MatchIon,
    MatchIsotope,
    MatchSample,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    Workspace,
)
from mascope_backend.db.scripts import rebuild_tof_target_isotopes as script


MECHANISM = "[M-H]-"
#: A second mechanism, which the calibration mode does not use.
OTHER_MECHANISM = "[M+Cl]-"


def test_the_script_can_be_run():
    """``mascope db script run`` finds a script by its callable main()."""
    assert callable(script.main)


def test_it_does_not_declare_that_it_writes_nothing():
    """A run replaces rows and removes matches, so the runner backs up first."""
    from pathlib import Path

    from mascope_cli.pg import script_writes_nothing

    assert not script_writes_nothing(Path(script.__file__).read_text(encoding="utf-8"))


# --- The scope an operator types ---


def test_the_default_scope_is_every_ion():
    assert script.read_scope({}) == script.Scope(kind=script.ALL)
    assert script.read_scope({script.SCOPE_ENV: "all"}).kind == script.ALL


def test_the_calibration_scope_is_read():
    assert script.read_scope({script.SCOPE_ENV: " Calibration "}) == script.Scope(
        kind=script.CALIBRATION
    )


def test_named_collections_are_read_once_each_in_order():
    scope = script.read_scope({script.COLLECTIONS_ENV: " b, a,b ,, "})
    assert scope == script.Scope(kind=script.COLLECTIONS, collection_ids=("b", "a"))
    assert script.read_scope(
        {script.SCOPE_ENV: "collections", script.COLLECTIONS_ENV: "a"}
    ) == script.Scope(kind=script.COLLECTIONS, collection_ids=("a",))


@pytest.mark.parametrize(
    "environ",
    [
        {script.SCOPE_ENV: "everything"},
        {script.SCOPE_ENV: "collections"},
        {script.SCOPE_ENV: "calibration", script.COLLECTIONS_ENV: "a"},
    ],
)
def test_a_scope_that_is_not_one_is_refused(environ):
    with pytest.raises(script.BadScope):
        script.read_scope(environ)


def test_lines_compare_to_six_decimals_in_any_order():
    a = [("C", 100.0000001, 0.5), ("D", 101.0, 0.25)]
    b = [("D", 101.0000004, 0.2500001), ("C", 100.0, 0.5)]
    assert script.lines_of(a) == script.lines_of(b)
    assert script.lines_of(a) != script.lines_of([("C", 100.00001, 0.5)])


# --- The database ---


def _fresh(compound_id: str, formula: str, mechanism: IonizationMechanism):
    """An ion and its rows as the generator makes them now."""
    ions, isotopes = generate_target_ions_from_composition(
        TargetCompoundBase(
            target_compound_id=compound_id, target_compound_formula=formula
        ),
        [mechanism],
    )
    (ion,) = ions
    return ion, isotopes


def _stale(isotopes: list[TargetIsotope]) -> list[TargetIsotope]:
    """The rows of an ion made under an older grouping: its fine structure
    stored as its TOF lines, every isotopologue a line of its own, as split
    clusters were before they merged."""
    high = [isotope for isotope in isotopes if isotope.resolution == "HIGH"]
    low = [
        TargetIsotope(
            target_isotope_id=gen_test_id(),
            target_ion_id=isotope.target_ion_id,
            mz=isotope.mz,
            relative_abundance=isotope.relative_abundance,
            resolution="LOW",
            target_isotope_formula=isotope.target_isotope_formula,
        )
        for isotope in high
    ]
    assert len(low) > sum(1 for isotope in isotopes if isotope.resolution == "LOW")
    return high + low


def _match_isotope(sample_item_id: str, target_isotope_id: str) -> MatchIsotope:
    return MatchIsotope(
        match_isotope_id=gen_test_id(),
        target_isotope_id=target_isotope_id,
        sample_item_id=sample_item_id,
        sample_peak_id=gen_test_id(),
        sample_peak_mz=100.0,
        sample_peak_intensity=1.0,
        sample_peak_intensity_relative=1.0,
        sample_peak_tof=1.0,
        match_abundance_error=0.0,
        match_mz_error=0.0,
        match_score=0.9,
    )


def _match_ion(sample_item_id: str, target_ion_id: str) -> MatchIon:
    return MatchIon(
        match_ion_id=gen_test_id(),
        sample_item_id=sample_item_id,
        target_ion_id=target_ion_id,
        match_score=0.9,
        match_category=2,
        sample_peak_intensity_sum=1.0,
    )


@pytest_asyncio.fixture
async def library(async_session_factory):
    """A small library and the batches that read it.

    Collections:

    - ``calibration``, named by a mode: a stale ion and a current one;
    - ``targets``: a stale ion (``stale``), a current one (``current``), and
      one stored under an ion formula the generator does not make
      (``foreign``), all three with stale LOW rows but ``current``;
    - ``narrow``: the current ion's compound alone;
    - ``other``: a stale ion, in no batch.

    Batches:

    - ``tof``: TOF, holds ``targets``, its sample matched on the stale ion's
      old LOW row and on the current ion;
    - ``orbi``: Orbitrap, holds ``targets``, matched on the stale ion's HIGH
      row;
    - ``recal``: TOF, holds ``targets``, waiting on a recalibration;
    - ``loose``: TOF, holds no collection, its sample matched on the stale
      ion's old LOW row;
    - ``narrow``: TOF, holds ``narrow``, matched on the current ion.
    """
    added = []
    ids = {
        "workspace": gen_test_id(),
        "dataset": gen_test_id(),
        "mode": gen_test_id(),
    }
    collections = {
        key: gen_test_id() for key in ("calibration", "targets", "narrow", "other")
    }
    async with async_session_factory() as session:
        mechanisms = {}
        for notation in (MECHANISM, OTHER_MECHANISM):
            found = (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism == notation
                    )
                )
            ).scalar_one_or_none()
            if found is None:
                found = IonizationMechanism(
                    ionization_mechanism_id=gen_test_id(),
                    ionization_mechanism=notation,
                    ionization_mechanism_polarity="-",
                )
                session.add(found)
                added.append(found)
                await session.flush()
            mechanisms[notation] = found
        mechanism = mechanisms[MECHANISM]

        rows = [
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"rebuild {ids['workspace']}",
                workspace_status="active",
            ),
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="rebuild",
                dataset_type="ACQUISITION",
                instrument="rebuild-tof",
            ),
        ]
        for key, collection_id in collections.items():
            rows.append(
                TargetCollection(
                    target_collection_id=collection_id,
                    target_collection_name=f"rebuild {key} {collection_id}",
                    target_collection_type=(
                        "CALIBRATION" if key == "calibration" else "TARGETS"
                    ),
                )
            )
        rows.append(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"Rebuild {ids['mode']}",
                ionization_mode_polarity="-",
                ionization_mechanism_ids=[mechanism.ionization_mechanism_id],
                calibration_collection_id=collections["calibration"],
            )
        )
        session.add_all(rows)
        await session.flush()

        ions = {}
        isotopes = {}
        compounds = {}
        for key, formula, homes, stale, foreign, notation in [
            ("cal_stale", "C6H5ClO", ["calibration"], True, False, MECHANISM),
            ("cal_current", "C7H6O2", ["calibration"], False, False, MECHANISM),
            # Of the collection, under a mechanism the mode does not use
            ("cal_chloride", "C7H6O3", ["calibration"], True, False, OTHER_MECHANISM),
            ("stale", "C6H4Cl2O", ["targets"], True, False, MECHANISM),
            ("current", "C8H8O3", ["targets", "narrow"], False, False, MECHANISM),
            ("foreign", "C9H10O2", ["targets"], True, True, MECHANISM),
            ("other", "C10H12O2", ["other"], True, False, MECHANISM),
            ("orphan", "C11H14O2", [], True, False, MECHANISM),
        ]:
            compound_id = gen_test_id()
            compounds[key] = compound_id
            session.add(
                TargetCompound(
                    target_compound_id=compound_id, target_compound_formula=formula
                )
            )
            await session.flush()
            session.add_all(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound_id,
                    target_collection_id=collections[home],
                )
                for home in homes
            )
            ion, made = _fresh(compound_id, formula, mechanisms[notation])
            if foreign:
                ion.target_ion_formula = "Q" + ion.target_ion_formula
            if stale:
                made = _stale(made)
            ions[key] = ion
            isotopes[key] = made
            session.add(ion)
            await session.flush()
            session.add_all(made)
        await session.flush()

        # The stale ion's HIGH rows differ too: counted, never written
        stale_high = next(i for i in isotopes["stale"] if i.resolution == "HIGH")
        stale_high.relative_abundance = stale_high.relative_abundance * 0.5

        batches = {}
        samples = {}
        for key, instrument_type, status, holds in [
            ("tof", "tof", "ready", ["targets"]),
            ("orbi", "orbi", "ready", ["targets"]),
            ("recal", "tof", "recalibrate", ["targets"]),
            ("loose", "tof", "ready", []),
            ("narrow", "tof", "ready", ["narrow"]),
        ]:
            batch_id = gen_test_id()
            batches[key] = batch_id
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=ids["dataset"],
                    sample_batch_name=f"rebuild {key} {batch_id}",
                    sample_batch_type="ANALYSIS",
                    status=status,
                    polarity="-",
                )
            )
            await session.flush()
            session.add_all(
                TargetCollectionInSampleBatch(
                    target_collection_id=collections[home], sample_batch_id=batch_id
                )
                for home in holds
            )
            file_id = gen_test_id()
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"rebuild-{key}-{file_id}",
                    instrument="rebuild-tof",
                    instrument_type=instrument_type,
                    datetime=datetime(2026, 10, 1, 12),
                    datetime_utc=datetime(2026, 10, 1, 12, tzinfo=UTC),
                    length=1.0,
                    range=[10.0, 500.0],
                    polarity="-",
                )
            )
            await session.flush()
            sample_id = gen_test_id()
            samples[key] = sample_id
            session.add(
                SampleItem(
                    sample_item_id=sample_id,
                    sample_batch_id=batch_id,
                    sample_file_id=file_id,
                    sample_item_name=f"rebuild {key}",
                    sample_item_type="ACQUISITION",
                )
            )
        await session.flush()

        def first(key, resolution):
            return next(
                i.target_isotope_id for i in isotopes[key] if i.resolution == resolution
            )

        session.add_all(
            [
                _match_isotope(samples["tof"], first("stale", "LOW")),
                _match_isotope(samples["tof"], first("current", "LOW")),
                _match_ion(samples["tof"], ions["stale"].target_ion_id),
                _match_ion(samples["tof"], ions["current"].target_ion_id),
                MatchCompound(
                    match_compound_id=gen_test_id(),
                    sample_item_id=samples["tof"],
                    target_compound_id=compounds["stale"],
                    match_score=0.9,
                    match_category=2,
                    sample_peak_intensity_sum=1.0,
                ),
                MatchCollection(
                    match_collection_id=gen_test_id(),
                    sample_item_id=samples["tof"],
                    target_collection_id=collections["targets"],
                    match_score=0.9,
                    match_category=2,
                    sample_peak_intensity_sum=1.0,
                ),
                MatchSample(
                    match_sample_id=gen_test_id(),
                    sample_item_id=samples["tof"],
                    match_score=0.9,
                    match_category=2,
                    sample_peak_intensity_sum=1.0,
                ),
                _match_isotope(samples["orbi"], first("stale", "HIGH")),
                _match_ion(samples["orbi"], ions["stale"].target_ion_id),
                _match_isotope(samples["recal"], first("current", "LOW")),
                _match_isotope(samples["loose"], first("stale", "LOW")),
                _match_isotope(samples["narrow"], first("current", "LOW")),
                _match_ion(samples["narrow"], ions["current"].target_ion_id),
            ]
        )
        await session.commit()

    yield {
        "mechanism": mechanism,
        "mode_id": ids["mode"],
        "collections": collections,
        "compounds": compounds,
        "ions": {key: ion.target_ion_id for key, ion in ions.items()},
        "batches": batches,
        "samples": samples,
    }

    async with async_session_factory() as session:
        sample_ids = list(samples.values())
        for table in (
            MatchIsotope,
            MatchIon,
            MatchCompound,
            MatchCollection,
            MatchSample,
        ):
            await session.execute(
                delete(table).where(table.sample_item_id.in_(sample_ids))
            )
        file_ids = select(SampleItem.sample_file_id).where(
            SampleItem.sample_item_id.in_(sample_ids)
        )
        file_ids = (await session.execute(file_ids)).scalars().all()
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(sample_ids))
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(file_ids))
        )
        await session.execute(
            delete(TargetCollectionInSampleBatch).where(
                TargetCollectionInSampleBatch.sample_batch_id.in_(batches.values())
            )
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id.in_(batches.values()))
        )
        await session.execute(
            delete(TargetIsotope).where(
                TargetIsotope.target_ion_id.in_(
                    [ion.target_ion_id for ion in ions.values()]
                )
            )
        )
        await session.execute(
            delete(TargetIon).where(
                TargetIon.target_ion_id.in_(
                    [ion.target_ion_id for ion in ions.values()]
                )
            )
        )
        await session.execute(
            delete(TargetCompoundInTargetCollection).where(
                TargetCompoundInTargetCollection.target_compound_id.in_(
                    compounds.values()
                )
            )
        )
        await session.execute(
            delete(TargetCompound).where(
                TargetCompound.target_compound_id.in_(compounds.values())
            )
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.execute(
            delete(TargetCollection).where(
                TargetCollection.target_collection_id.in_(collections.values())
            )
        )
        await session.execute(
            delete(Dataset).where(Dataset.dataset_id == ids["dataset"])
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        for row in added:
            await session.delete(row)
        await session.commit()


async def _rows(session_factory, ion_id: str, resolution: str) -> list[tuple]:
    """An ion's stored rows of one resolution: ``(id, formula, mz, abundance)``."""
    async with session_factory() as session:
        found = (
            await session.execute(
                select(
                    TargetIsotope.target_isotope_id,
                    TargetIsotope.target_isotope_formula,
                    TargetIsotope.mz,
                    TargetIsotope.relative_abundance,
                )
                .where(
                    TargetIsotope.target_ion_id == ion_id,
                    TargetIsotope.resolution == resolution,
                )
                .order_by(TargetIsotope.target_isotope_id)
            )
        ).all()
    return [tuple(row) for row in found]


async def _state(session_factory, library) -> dict:
    """Everything a run could change: every ion's rows, every sample's
    matches, every batch's status."""
    state = {"rows": {}, "matches": {}, "status": {}, "modified": {}}
    for key, ion_id in library["ions"].items():
        for resolution in ("LOW", "HIGH"):
            state["rows"][key, resolution] = await _rows(
                session_factory, ion_id, resolution
            )
    async with session_factory() as session:
        for key, sample_id in library["samples"].items():
            state["matches"][key] = {
                table.__tablename__: sorted(
                    (
                        await session.execute(
                            select(column).where(table.sample_item_id == sample_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                for table, column in (
                    (MatchIsotope, MatchIsotope.match_isotope_id),
                    (MatchIon, MatchIon.match_ion_id),
                    (MatchCompound, MatchCompound.match_compound_id),
                    (MatchCollection, MatchCollection.match_collection_id),
                    (MatchSample, MatchSample.match_sample_id),
                )
            }
            state["modified"][key] = await session.scalar(
                select(SampleItem.sample_item_utc_modified).where(
                    SampleItem.sample_item_id == sample_id
                )
            )
        for key, batch_id in library["batches"].items():
            state["status"][key], state["modified"]["batch", key] = (
                await session.execute(
                    select(
                        SampleBatch.status, SampleBatch.sample_batch_utc_modified
                    ).where(SampleBatch.sample_batch_id == batch_id)
                )
            ).one()
    return state


def _scope(library, *keys) -> script.Scope:
    return script.Scope(
        kind=script.COLLECTIONS,
        collection_ids=tuple(library["collections"][key] for key in keys),
    )


def _plan(totals, collection_id) -> script.CollectionPlan:
    (plan,) = [plan for plan in totals.plans if plan.collection_id == collection_id]
    return plan


def _fresh_low(library, key, formula) -> list:
    _, isotopes = _fresh(library["compounds"][key], formula, library["mechanism"])
    return script.lines_of(
        (i.target_isotope_formula, i.mz, i.relative_abundance)
        for i in isotopes
        if i.resolution == "LOW"
    )


@pytest.mark.asyncio
async def test_a_dry_run_reports_the_stale_ion_and_writes_nothing(
    async_session_factory, library
):
    before = await _state(async_session_factory, library)

    totals = await script.rebuild(_scope(library, "targets"), dry_run=True)

    assert await _state(async_session_factory, library) == before
    plan = _plan(totals, library["collections"]["targets"])
    assert len(plan.ions) == 3
    (changed,) = plan.changed
    assert changed.target_ion_id == library["ions"]["stale"]
    assert script.lines_of(changed.low) == script.lines_of(
        (formula, mz, abundance)
        for _, formula, mz, abundance in before["rows"]["stale", "LOW"]
    )
    assert script.lines_of(changed.fresh.low) == _fresh_low(
        library, "stale", "C6H4Cl2O"
    )
    assert [ion.target_ion_id for ion in plan.not_made] == [library["ions"]["foreign"]]
    assert plan.high_differ == 1
    batches = library["batches"]
    assert set(plan.batches) == {batches["tof"], batches["recal"], batches["loose"]}
    samples = library["samples"]
    assert set(plan.samples) == {samples["tof"], samples["recal"], samples["loose"]}
    assert (plan.match_isotopes, plan.match_ions) == (4, 2)


@pytest.mark.asyncio
async def test_a_run_replaces_the_stale_lines_and_flags_the_tof_batches(
    async_session_factory, library
):
    before = await _state(async_session_factory, library)

    totals = await script.rebuild(_scope(library, "targets"), dry_run=False)

    after = await _state(async_session_factory, library)
    plan = _plan(totals, library["collections"]["targets"])
    assert [ion.target_ion_id for ion in plan.changed] == [library["ions"]["stale"]]

    # The stale ion's LOW rows are what a new ion gets, under new ids
    new_low = after["rows"]["stale", "LOW"]
    assert script.lines_of(row[1:] for row in new_low) == _fresh_low(
        library, "stale", "C6H4Cl2O"
    )
    assert not {row[0] for row in new_low} & {
        row[0] for row in before["rows"]["stale", "LOW"]
    }
    # No HIGH row is written, the differing one included
    for key in library["ions"]:
        assert after["rows"][key, "HIGH"] == before["rows"][key, "HIGH"]
    # The ions whose lines agree, and the one not made as stored, keep theirs
    for key in (
        "current",
        "foreign",
        "cal_stale",
        "cal_current",
        "cal_chloride",
        "other",
        "orphan",
    ):
        assert after["rows"][key, "LOW"] == before["rows"][key, "LOW"]

    # Every match of the reached TOF batches' samples goes, every level
    for key in ("tof", "recal", "loose"):
        assert before["matches"][key]["match_isotope"]
        assert all(not ids for ids in after["matches"][key].values())
        assert after["modified"][key] != before["modified"][key]
    assert all(before["matches"]["tof"].values())
    assert after["status"]["tof"] == "rematch"
    assert after["status"]["loose"] == "rematch"
    for key in ("tof", "loose"):
        assert after["modified"]["batch", key] != before["modified"]["batch", key]
    # A pending recalibration is stronger and stays
    assert after["status"]["recal"] == "recalibrate"
    # The Orbitrap batch and the batch of the current ion alone are untouched
    for key in ("orbi", "narrow"):
        assert after["matches"][key] == before["matches"][key]
        assert after["status"][key] == "ready"
        assert after["modified"][key] == before["modified"][key]
        assert after["modified"]["batch", key] == before["modified"]["batch", key]


@pytest.mark.asyncio
async def test_a_second_run_finds_nothing_to_do(async_session_factory, library):
    await script.rebuild(_scope(library, "targets"), dry_run=False)
    async with async_session_factory() as session:
        await session.execute(
            update(SampleBatch)
            .where(SampleBatch.sample_batch_id.in_(library["batches"].values()))
            .values(status="ready")
        )
        await session.commit()
    before = await _state(async_session_factory, library)

    totals = await script.rebuild(_scope(library, "targets"), dry_run=False)

    assert not _plan(totals, library["collections"]["targets"]).changed
    assert not totals.changed and not totals.batches
    assert await _state(async_session_factory, library) == before


@pytest.mark.asyncio
async def test_each_scope_keeps_to_its_collections(async_session_factory, library):
    collections = library["collections"]
    mine = set(collections.values())

    named = await script.rebuild(_scope(library, "other", "narrow"), dry_run=True)
    assert sorted(plan.collection_id for plan in named.plans) == sorted(
        [collections["other"], collections["narrow"]]
    )

    calibration = await script.rebuild(
        script.Scope(kind=script.CALIBRATION), dry_run=True
    )
    read = {plan.collection_id for plan in calibration.plans}
    assert read & mine == {collections["calibration"]}

    every = await script.rebuild(script.Scope(kind=script.ALL), dry_run=True)
    read = {plan.collection_id for plan in every.plans}
    assert mine <= read
    loose = _plan(every, script.NO_COLLECTION)
    assert library["ions"]["orphan"] in {ion.target_ion_id for ion in loose.changed}
    assert not {ion.target_ion_id for ion in loose.ions} & {
        library["ions"][key] for key in ("stale", "current", "other")
    }

    # A run on one collection leaves the others' stale ions as they are
    before = await _state(async_session_factory, library)
    await script.rebuild(_scope(library, "other"), dry_run=False)
    after = await _state(async_session_factory, library)
    assert after["rows"]["other", "LOW"] != before["rows"]["other", "LOW"]
    for key in ("stale", "cal_stale"):
        assert after["rows"][key, "LOW"] == before["rows"][key, "LOW"]
    # Its ion is in no batch: nothing is flagged
    assert after["status"] == before["status"]
    assert after["matches"] == before["matches"]


@pytest.mark.asyncio
async def test_a_named_collection_that_is_not_there_is_refused(library):
    with pytest.raises(script.BadScope, match="no-such-collection"):
        await script.rebuild(
            script.Scope(
                kind=script.COLLECTIONS, collection_ids=("no-such-collection",)
            ),
            dry_run=True,
        )


async def _fit_hash(library) -> str:
    """The hash a fit of the mode reads, from the rows as the fit reads them
    (``calibration_mz_fit._resolve_calibration_isotopes``)."""
    import pandas as pd

    from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
        calibration_isotopes_sha256,
    )

    found = await get_target_isotopes(
        target_compound_ids=[
            library["compounds"]["cal_stale"],
            library["compounds"]["cal_current"],
        ],
        ionization_mechanism_ids=[library["mechanism"].ionization_mechanism_id],
        resolution="LOW",
    )
    return calibration_isotopes_sha256(pd.DataFrame(found["data"]))


@pytest.mark.asyncio
async def test_a_calibration_collection_reports_its_modes_hash_before_and_after(
    library,
):
    before = await _fit_hash(library)

    totals = await script.rebuild(_scope(library, "calibration"), dry_run=False)

    (mode,) = _plan(totals, library["collections"]["calibration"]).modes
    assert mode.ionization_mode_id == library["mode_id"]
    assert mode.before == before
    assert mode.after == await _fit_hash(library)
    assert mode.before != mode.after
    assert totals.hashes_changed == 1
