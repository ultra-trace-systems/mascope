"""
Tests: a start seeds the ionization mechanisms Mascope ships, then its modes.

A run searches a channel only where the server holds its mechanism, so a
server that holds none - a fresh one - searches nothing but what an operator
thought to type, and seeds none of the shipped modes either, since a mode is
seeded only where its mechanisms exist. So the mechanisms are seeded too, at
every start, through the path the API creates one by: the target ions of the
compounds already in the library are built with each, as they are when an
operator adds one.

These run against a database of their own, as a first start finds it: the
schema and nothing else. The shared integration database carries rows other
tests left, which would make "what one start creates" unanswerable.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import mascope_backend.db as db_module
import mascope_backend.db.admin.ionization.ensure_system_modes as seed_module
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    TargetCompound,
    TargetIon,
    TargetIsotope,
)
from mascope_backend.db.admin.ionization.ensure_system_modes import (
    ensure_system_ionization,
)
from mascope_backend.ionization_catalogue import SYSTEM_MODES, shipped_mechanisms


_SHIPPED = {mechanism.notation: mechanism for mechanism in shipped_mechanisms()}
_BROMIDE = next(entry for entry in SYSTEM_MODES if entry[0] == "bromide")


@pytest_asyncio.fixture(scope="module")
async def first_start_factory(async_engine_factory):
    """A database of its own: the schema, and nothing a start has written."""
    engine = await async_engine_factory("integration_first_start")
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def fresh_db(first_start_factory, bind_db, monkeypatch):
    """The application pointed at that database, emptied of earlier tests' rows.

    Requests ``bind_db`` so that it runs first: this rebinds what it bound.
    """
    async with first_start_factory() as session:
        for model in (
            TargetIsotope,
            TargetIon,
            TargetCompound,
            IonizationMode,
            IonizationMechanism,
        ):
            await session.execute(delete(model))
        await session.commit()
    monkeypatch.setattr(db_module, "ASYNC_SESSION_MAKER", first_start_factory)
    return first_start_factory


async def _add(factory, *rows):
    async with factory() as session:
        session.add_all(rows)
        await session.commit()


async def _mechanisms(factory) -> dict[str, list[tuple[str, str]]]:
    """Notation (as the column reads it) -> the (id, polarity) of each row."""
    async with factory() as session:
        rows = (await session.execute(select(IonizationMechanism))).scalars().all()
    held: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        held.setdefault(row.ionization_mechanism, []).append(
            (row.ionization_mechanism_id, row.ionization_mechanism_polarity)
        )
    return held


async def _modes(factory) -> dict[str, IonizationMode]:
    async with factory() as session:
        rows = (await session.execute(select(IonizationMode))).scalars().all()
    return {row.system_key: row for row in rows}


async def _count(factory, model) -> int:
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


def _compound(compound_id="cmpPinonic", formula="C10H16O3"):
    return TargetCompound(
        target_compound_id=compound_id,
        target_compound_name="pinonic acid",
        target_compound_formula=formula,
    )


@pytest.mark.asyncio
async def test_a_first_start_seeds_every_shipped_mechanism_and_mode(fresh_db):
    counts = await ensure_system_ionization()

    held = await _mechanisms(fresh_db)
    assert held == {
        notation: [(mechanism.mechanism_id, mechanism.polarity)]
        for notation, mechanism in _SHIPPED.items()
    }
    assert counts["mechanisms"]["created"] == len(_SHIPPED)

    modes = await _modes(fresh_db)
    assert set(modes) == {system_key for system_key, *_ in SYSTEM_MODES}
    assert counts["modes"]["seeded"] == len(SYSTEM_MODES)
    for system_key, mode_id, name, polarity, wanted in SYSTEM_MODES:
        mode = modes[system_key]
        assert mode.ionization_mode_id == mode_id
        assert mode.ionization_mode_polarity == polarity
        # The mode's own mechanisms, and nothing the profiles only open.
        assert mode.ionization_mechanism_ids == [
            _SHIPPED[notation].mechanism_id for notation in wanted
        ]


@pytest.mark.asyncio
async def test_a_compound_already_in_the_library_gets_its_ions(fresh_db):
    """The work the API does when a mechanism is created, done by the seed."""
    await _add(fresh_db, _compound())

    await ensure_system_ionization()

    async with fresh_db() as session:
        ions = (
            (
                await session.execute(
                    select(TargetIon).where(
                        TargetIon.target_compound_id == "cmpPinonic"
                    )
                )
            )
            .scalars()
            .all()
        )
        with_isotopes = set(
            (
                await session.execute(
                    select(TargetIsotope.target_ion_id).where(
                        TargetIsotope.target_ion_id.in_(
                            [ion.target_ion_id for ion in ions]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    by_mechanism = {ion.ionization_mechanism_id: ion for ion in ions}
    assert set(by_mechanism) == {m.mechanism_id for m in _SHIPPED.values()}
    assert with_isotopes == {ion.target_ion_id for ion in ions}
    assert by_mechanism[_SHIPPED["[M+Br]-"].mechanism_id].target_ion_formula == (
        "C10H16BrO3-"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["+Br-", "[M+Br]-"])
async def test_a_mechanism_the_server_holds_is_kept_under_its_own_id(
    fresh_db, spelling
):
    """In either spelling: the notation is the mechanism, the id the server's."""
    await _add(
        fresh_db,
        IonizationMechanism(
            ionization_mechanism_id="theirsBromide",
            ionization_mechanism_polarity="-",
            ionization_mechanism=spelling,
        ),
    )

    counts = await ensure_system_ionization()

    held = await _mechanisms(fresh_db)
    assert held["[M+Br]-"] == [("theirsBromide", "-")]
    assert counts["mechanisms"]["held"] == 1
    assert counts["mechanisms"]["created"] == len(_SHIPPED) - 1
    bromide = (await _modes(fresh_db))["bromide"]
    assert "theirsBromide" in bromide.ionization_mechanism_ids


@pytest.mark.asyncio
async def test_a_row_under_the_wrong_polarity_is_reported_and_left(fresh_db):
    """No mode can hold it and no run can search it, and the column is unique,
    so the seed can neither use it nor add the mechanism beside it. Deleted,
    the mechanism is created as shipped at the next start."""
    await _add(
        fresh_db,
        IonizationMechanism(
            ionization_mechanism_id="brokenBromide",
            ionization_mechanism_polarity="+",
            ionization_mechanism="[M+Br]-",
        ),
    )

    counts = await ensure_system_ionization()

    assert counts["mechanisms"]["wrong_polarity"] == 1
    assert (await _mechanisms(fresh_db))["[M+Br]-"] == [("brokenBromide", "+")]
    assert "bromide" not in await _modes(fresh_db)

    async with fresh_db() as session:
        await session.execute(
            delete(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism_id == "brokenBromide"
            )
        )
        await session.commit()
    await ensure_system_ionization()

    assert (await _mechanisms(fresh_db))["[M+Br]-"] == [
        (_SHIPPED["[M+Br]-"].mechanism_id, "-")
    ]
    assert "bromide" in await _modes(fresh_db)


@pytest.mark.asyncio
async def test_a_fixed_id_another_mechanism_holds_is_reported(fresh_db):
    await _add(
        fresh_db,
        IonizationMechanism(
            ionization_mechanism_id=_SHIPPED["[M+Br]-"].mechanism_id,
            ionization_mechanism_polarity="-",
            ionization_mechanism="[M+Cl]-",
        ),
    )

    counts = await ensure_system_ionization()

    assert counts["mechanisms"]["id_taken"] == 1
    assert "[M+Br]-" not in await _mechanisms(fresh_db)


@pytest.mark.asyncio
async def test_a_mechanism_that_fails_is_created_at_the_next_start(
    fresh_db, monkeypatch
):
    """One transaction per mechanism: the rest are kept, and so is the chance
    to try the one that failed again."""
    add = seed_module.add_ionization_mechanism

    async def failing_for_bromide(session, mechanism):
        if mechanism.ionization_mechanism == "[M+Br]-":
            raise RuntimeError("the database went away")
        return await add(session, mechanism)

    monkeypatch.setattr(seed_module, "add_ionization_mechanism", failing_for_bromide)
    await _add(fresh_db, _compound())

    counts = await ensure_system_ionization()

    assert counts["mechanisms"]["failed"] == 1
    assert counts["mechanisms"]["created"] == len(_SHIPPED) - 1
    assert "[M+Br]-" not in await _mechanisms(fresh_db)
    assert "bromide" not in await _modes(fresh_db)

    monkeypatch.setattr(seed_module, "add_ionization_mechanism", add)
    counts = await ensure_system_ionization()

    assert counts["mechanisms"]["created"] == 1
    assert "bromide" in await _modes(fresh_db)
    async with fresh_db() as session:
        bromide_ions = await session.scalar(
            select(func.count())
            .select_from(TargetIon)
            .where(
                TargetIon.ionization_mechanism_id == _SHIPPED["[M+Br]-"].mechanism_id
            )
        )
    assert bromide_ions == 1


@pytest.mark.asyncio
async def test_a_second_start_changes_nothing(fresh_db):
    await _add(fresh_db, _compound())
    await ensure_system_ionization()
    before = (
        await _mechanisms(fresh_db),
        {
            key: mode.ionization_mechanism_ids
            for key, mode in (await _modes(fresh_db)).items()
        },
        await _count(fresh_db, TargetIon),
        await _count(fresh_db, TargetIsotope),
    )

    counts = await ensure_system_ionization()

    after = (
        await _mechanisms(fresh_db),
        {
            key: mode.ionization_mechanism_ids
            for key, mode in (await _modes(fresh_db)).items()
        },
        await _count(fresh_db, TargetIon),
        await _count(fresh_db, TargetIsotope),
    )
    assert after == before
    assert counts["mechanisms"]["created"] == 0
    assert counts["mechanisms"]["held"] == len(_SHIPPED)
    assert counts["modes"]["seeded"] == 0


@pytest.mark.asyncio
async def test_a_start_that_creates_nothing_says_so(fresh_db, monkeypatch):
    """One line per start, so the log shows the pass ran and found everything."""
    await ensure_system_ionization()
    lines = []
    monkeypatch.setattr(seed_module.runtime.logger, "info", lines.append)

    await ensure_system_ionization()

    assert f"System ionization mechanisms: 0 created, {len(_SHIPPED)} held" in lines


@pytest.mark.asyncio
async def test_a_mode_the_deployment_made_is_left_as_it_is(fresh_db):
    """A shipped mechanism is never added to a mode: declared on one, a
    secondary channel is that mode's own and is searched differently."""
    await _add(
        fresh_db,
        IonizationMechanism(
            ionization_mechanism_id="theirsBromide",
            ionization_mechanism_polarity="-",
            ionization_mechanism="[M+Br]-",
        ),
        IonizationMode(
            ionization_mode_id="theirBromideMode",
            ionization_mode_name="Bromide",
            ionization_mode_token="BR",
            ionization_mode_polarity="-",
            ionization_mechanism_ids=["theirsBromide"],
        ),
    )

    await ensure_system_ionization()

    async with fresh_db() as session:
        theirs = await session.get(IonizationMode, "theirBromideMode")
    assert theirs.ionization_mechanism_ids == ["theirsBromide"]
    assert theirs.system_key is None
    # The shipped chemistry sits beside it under its own name.
    _, mode_id, *_ = _BROMIDE
    assert (await _modes(fresh_db))["bromide"].ionization_mode_id == mode_id
