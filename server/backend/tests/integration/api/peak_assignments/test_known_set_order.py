"""Integration: a run's known set lists its ions in an order the SQL states.

The known set keeps its ions in the order its inputs give them
(``target_ions_compute.lines_at_file``), and the reference expansion lists a
formula's ions in the order of the sample's mechanisms. So the mechanism
queries order by id rather than leaving the rows in the table's heap order,
which an ``UPDATE`` of one row changes while the mechanism set stays the same
(MAS-1164).

The mechanisms are stored here in the reverse of their id order, and one of
them is then rewritten, so a query that states no order reads them in a third
order.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

from mascope_backend.api.new.peak_assignments import sample_inputs
from mascope_backend.api.new.peak_assignments.sample_mechanisms import (
    fetch_mechanisms_by_notation,
    fetch_sample_mechanisms,
)
from mascope_backend.db import (
    Dataset,
    IonizationMechanism,
    IonizationMode,
    ReferenceCompound,
    ReferenceSource,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_reference import canonical_formula, monoisotopic_mass


pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: Three mechanisms no other test stores, by the last letter of their ids.
_NOTATIONS = {"a": "[M+Ag]+", "b": "[M+Li]+", "c": "[M+Cu]+"}

#: The reference list's formulas: each one is expanded through every mechanism.
_FORMULAS = ("C6H12O6", "C5H10O5")


@pytest_asyncio.fixture
async def world(async_session_factory):
    """A sample whose mode binds three mechanisms, stored in the reverse of
    their id order, and an active reference list of two formulas."""
    prefix = gen_id(15)
    mechanism_ids = {key: f"{prefix}{key}" for key in _NOTATIONS}
    ids = {
        "workspace": gen_id(),
        "dataset": gen_id(),
        "batch": gen_id(),
        "file": gen_id(),
        "item": gen_id(),
        "mode": gen_id(),
    }
    async with async_session_factory() as session:
        # One row at a time, each committed, so the heap holds them c, b, a.
        for key in ("c", "b", "a"):
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=mechanism_ids[key],
                    ionization_mechanism_polarity="+",
                    ionization_mechanism=_NOTATIONS[key],
                )
            )
            await session.commit()
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Known set order {ids['workspace']}",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Known set order",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Known set order",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"test-orbion_{ids['file']}.raw",
                instrument="test-orbion",
                datetime=datetime(2026, 1, 1),
                datetime_utc=_NOW,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            IonizationMode(
                ionization_mode_id=ids["mode"],
                ionization_mode_name=f"Known set order {ids['mode']}",
                ionization_mode_token=f"kso{ids['mode']}",
                ionization_mode_polarity="+",
                # The mode's own order, which the ids keep as stored.
                ionization_mechanism_ids=[mechanism_ids[k] for k in ("b", "c", "a")],
            )
        )
        session.add(
            SampleItem(
                sample_item_id=ids["item"],
                sample_batch_id=ids["batch"],
                sample_file_id=ids["file"],
                sample_item_name="Known set order",
                sample_item_type="SAMPLE",
                sample_item_attributes={},
                polarity="+",
                ionization_mode_id=ids["mode"],
                sample_item_utc_created=_NOW,
            )
        )
        source = ReferenceSource(
            name=f"order-{prefix}",
            version="test",
            license="CC-BY-4.0",
            record_count=len(_FORMULAS),
            is_active=True,
            ingested_at=_NOW,
            known_window={},
        )
        session.add(source)
        await session.flush()
        source_id = source.reference_source_id
        for formula in _FORMULAS:
            canonical = canonical_formula(formula)
            session.add(
                ReferenceCompound(
                    reference_source_id=source_id,
                    formula=canonical,
                    monoisotopic_mass=monoisotopic_mass(canonical),
                    inchikey=None,
                    name=f"order {formula}",
                    smiles=None,
                    inchi=None,
                    source_native_id=f"order-{formula}",
                    xrefs={},
                    license="CC-BY-4.0",
                )
            )
        await session.commit()

    sample = SimpleNamespace(
        sample_item_id=ids["item"],
        sample_item_name="Known set order",
        sample_batch_id=ids["batch"],
        filename=f"test-orbion_{ids['file']}.raw",
        instrument="test-orbion",
        polarity="+",
    )
    yield SimpleNamespace(sample=sample, mechanism_ids=mechanism_ids)

    async with async_session_factory() as session:
        await session.execute(
            delete(ReferenceCompound).where(
                ReferenceCompound.reference_source_id == source_id
            )
        )
        await session.execute(
            delete(ReferenceSource).where(
                ReferenceSource.reference_source_id == source_id
            )
        )
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id == ids["item"])
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == ids["mode"]
            )
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id == ids["file"])
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
        )
        await session.execute(
            delete(Dataset).where(Dataset.dataset_id == ids["dataset"])
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == ids["workspace"])
        )
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id.in_(mechanism_ids.values())
            )
        )
        await session.commit()


async def _rewrite(async_session_factory, mechanism_id):
    """Rewrite one mechanism row, which moves it to the end of the heap."""
    async with async_session_factory() as session:
        await session.execute(
            update(IonizationMechanism)
            .where(IonizationMechanism.ionization_mechanism_id == mechanism_id)
            .values(name="rewritten")
        )
        await session.commit()


def _sequence(known):
    """The (formula, mechanism, m/z) sequence of a known set's rows from the
    fixture's own reference list."""
    ours = known[known["target_compound_formula"].isin(_FORMULAS)]
    return list(
        zip(
            ours["target_compound_formula"],
            ours["ionization_mechanism"],
            ours["mz"].round(6),
        )
    )


async def _known_isotopes(sample, mechanism_ids, mechanisms):
    """The sample's known set for this mechanism list, through whatever the
    reference cache holds."""
    return await sample_inputs._fetch_known_isotopes(
        sample,
        0.01,
        mechanism_ids,
        mechanisms,
        known_window=None,
        resolution=None,
    )


async def _known_set(sample):
    """The (formula, mechanism, m/z) sequence of the sample's known set, as a
    fresh process builds it, its own reference list's rows alone."""
    mechanism_ids, mechanisms = await fetch_sample_mechanisms(sample)
    sample_inputs._reference_isotope_cache.clear()
    try:
        known = await _known_isotopes(sample, mechanism_ids, mechanisms)
    finally:
        sample_inputs._reference_isotope_cache.clear()
    ours = known[known["target_compound_formula"].isin(_FORMULAS)]
    return list(
        zip(
            ours["target_compound_formula"],
            ours["ionization_mechanism"],
            ours["mz"].round(6),
        )
    )


async def test_the_sample_mechanisms_come_back_in_id_order(
    async_session_factory, world
):
    ids = world.mechanism_ids
    in_id_order = [ids["a"], ids["b"], ids["c"]]

    mode_ids, mechanisms = await fetch_sample_mechanisms(world.sample)
    assert [m.ionization_mechanism_id for m in mechanisms] == in_id_order
    # The mode's ids keep the mode's own order.
    assert mode_ids == [ids["b"], ids["c"], ids["a"]]

    await _rewrite(async_session_factory, ids["c"])
    _, mechanisms = await fetch_sample_mechanisms(world.sample)
    assert [m.ionization_mechanism_id for m in mechanisms] == in_id_order


async def test_the_secondary_channels_come_back_in_id_order(
    async_session_factory, world
):
    ids = world.mechanism_ids
    in_id_order = [ids["a"], ids["b"], ids["c"]]
    notations = list(_NOTATIONS.values())

    rows = await fetch_mechanisms_by_notation(notations, "+")
    assert [m.ionization_mechanism_id for m in rows] == in_id_order

    await _rewrite(async_session_factory, ids["c"])
    rows = await fetch_mechanisms_by_notation(notations, "+")
    assert [m.ionization_mechanism_id for m in rows] == in_id_order


async def test_a_rewritten_mechanism_leaves_the_known_set_in_its_order(
    async_session_factory, world
):
    before = await _known_set(world.sample)
    # Every formula through every mechanism, each formula's ions in the
    # mechanisms' id order.
    assert {(formula, mechanism) for formula, mechanism, _ in before} == {
        (formula, mechanism)
        for formula in _FORMULAS
        for mechanism in _NOTATIONS.values()
    }
    first_formula = [m for f, m, _ in before if f == before[0][0]]
    assert list(dict.fromkeys(first_formula)) == [
        _NOTATIONS["a"],
        _NOTATIONS["b"],
        _NOTATIONS["c"],
    ]

    await _rewrite(async_session_factory, world.mechanism_ids["c"])

    assert await _known_set(world.sample) == before


async def test_the_reference_cache_serves_a_list_only_in_its_own_order(world):
    """A searched list with a secondary channel appended, or a decoy's list,
    is not in id order. A frame the cache keeps from such a list must not be
    served to a list of the same mechanisms in another order: the sample's
    known set would then depend on which run filled the cache first."""
    mechanism_ids, in_id_order = await fetch_sample_mechanisms(world.sample)
    by_key = {m.ionization_mechanism_id[-1]: m for m in in_id_order}
    appended = [by_key["a"], by_key["c"], by_key["b"]]

    sample_inputs._reference_isotope_cache.clear()
    try:
        fresh = _sequence(
            await _known_isotopes(world.sample, mechanism_ids, in_id_order)
        )
        sample_inputs._reference_isotope_cache.clear()
        # Another run fills the cache from the same mechanisms in another
        # order, then this list is read with the cache kept.
        await _known_isotopes(world.sample, mechanism_ids, appended)
        served = _sequence(
            await _known_isotopes(world.sample, mechanism_ids, in_id_order)
        )
    finally:
        sample_inputs._reference_isotope_cache.clear()

    assert served == fresh
