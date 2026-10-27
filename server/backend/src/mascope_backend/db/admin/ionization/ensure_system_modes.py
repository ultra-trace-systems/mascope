"""
Seed the ionization chemistry Mascope ships: its mechanisms, its modes, then
their collections.

Every deployment names its chemistries itself, so one reagent is spelled many
ways and nothing can refer to a chemistry as such. A mode carrying a
``system_key`` (:mod:`mascope_backend.ionization_catalogue`) is that name: the
same string on every server, so a method binding has an identity to point at.
And a run searches a channel only where the server holds its mechanism, so
the mechanisms a server holds decide the chemistry its runs search under.
Seeding them makes that the same on every server, a fresh one included, from
its first start.

Three passes, in this order, at every start (:func:`ensure_system_ionization`).
Each is idempotent, and they run in the main process before any worker
starts, so there is nothing to race with.

**Mechanisms** (:func:`ensure_system_ionization_mechanisms`). Each mechanism
the catalogue ships that the server does not hold is created under the
catalogue's fixed id, in a transaction of its own, through the path the API
creates one by (``add_ionization_mechanism``). That path builds the target ions
of every compound already in the library, because every compound gains ions
for every mechanism there is when it is created, and a library holding ions
for some mechanisms and not others matches nothing through the rest. It is the
one costly step, some seconds per mechanism on a library of a thousand
compounds, paid once, by the first start that lacks the mechanism, and each
mechanism logs its count and time. A mechanism is its standard notation, so a
row the server already holds under it, in either spelling, is the shipped one
and is kept as it is, under its own id. A row stored under the polarity its
notation does not make is a broken copy that no mode can hold and no run can
search: it is reported and left, and once it is deleted the next start creates
the mechanism as shipped.

**Modes** (:func:`ensure_system_ionization_modes`). A chemistry is seeded where
the mechanisms it names are present, which after the first pass is wherever
none of them failed or is stored broken. They are matched on name *and* the
polarity they are stored under, the name compared in the standard adduct
notation the column reads it in, so a row still holding the legacy spelling
(``+Br-`` for ``[M+Br]-``) counts.

Neither pass adds a mechanism to a mode, shipped or the deployment's own. A
secondary channel declared on a mode is that mode's own channel, which the
engine searches differently (``reagents.SecondaryChannel``); the row on its
own only makes the channel searchable. For the modes themselves:

- a row already carrying the ``system_key`` is left as it is, collections and
  all;
- a row under the catalogue's own id but no key - what a downgrade leaves
  behind - is claimed rather than duplicated, but only while it is still what
  was seeded: name, polarity, no token, the same mechanisms. Edited in the
  meantime, it is left alone and logged, because claiming it would lock
  somebody's edit under the catalogue's key;
- a chemistry whose name some other mode already uses is skipped and logged,
  because ``create_ionization_mode`` refuses a duplicate name and answers
  ``MultipleResultsFound`` when two rows already share one.

**Collections** (:func:`ensure_system_ionization_collections`). Each seeded
chemistry with a reagent of its own gets a calibrant and a diagnostic
collection (``ionization_catalogue.seeded_collections``), global like the mode
that reads them, under a fixed id. A collection is the seed's while it holds
what a release of Mascope seeded it with, which its digest says:

- missing, it is created, its compounds through the path the API creates one
  by (``create_target_compound``), which builds their target ions for every
  mechanism; a compound the library already holds under the same name and
  formula is reused;
- holding what this release seeds, it is left;
- holding what an earlier release seeded (``SEEDED_BEFORE``), it is brought
  to this release's, through the path a person's edit takes
  (``update_target_collection``), so the batches it serves are re-matched;
- holding anything else, the deployment has edited it, and it is left as it
  is.

A seeded mode is pointed at its collection only where it points at none. A
mode pointed at a collection of the deployment's keeps it: the deployment
chose it. The API cannot clear a mode's collection, so a mode points at none
only until the seed has pointed it, or once the collection it pointed at has
been deleted, and then the next start creates the seeded one again and points
the mode back at it, as it creates a deleted mechanism again. Files already
processed under the mode keep their results until they are processed again.

Entry Points:
- Async: `ensure_system_ionization()` for every pass, as a start runs them
- Sync: `run_ensure_system_ionization()` for CLI and scripts
"""

import asyncio
import time

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from mascope_backend.api.controllers.ionization_mechanisms.ionization_mechanisms_controller import (
    add_ionization_mechanism,
)
from mascope_backend.api.controllers.target.collections.target_collections_controller import (
    update_target_collection,
)
from mascope_backend.api.controllers.target.compounds.target_compounds_controller import (
    create_target_compound,
)
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    TargetCollectionUpdate,
)
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    TargetCollection,
    TargetCompoundInTargetCollection,
    async_session,
)
from mascope_backend.ionization_catalogue import (
    SEEDED_BEFORE,
    SYSTEM_MODES,
    SeededCollection,
    collection_digest,
    seeded_collections,
    shipped_mechanisms,
)
from mascope_backend.runtime import runtime


async def ensure_system_ionization() -> dict[str, dict[str, int]]:
    """Seed the mechanisms Mascope ships, then its modes, then their collections.

    :return: Each pass's counts, under ``"mechanisms"``, ``"modes"`` and
        ``"collections"``.
    :rtype: dict[str, dict[str, int]]
    """
    mechanisms = await ensure_system_ionization_mechanisms()
    modes = await ensure_system_ionization_modes()
    collections = await ensure_system_ionization_collections()
    return {"mechanisms": mechanisms, "modes": modes, "collections": collections}


async def ensure_system_ionization_mechanisms() -> dict[str, int]:
    """Create the mechanisms the catalogue ships that this server does not hold.

    One transaction per mechanism, so a start that fails or is stopped partway
    keeps the mechanisms it finished and the next start creates the rest.

    :return: Counts of the mechanisms created, already held, held only under
        the wrong polarity, blocked by another row on the fixed id, and failed.
    :rtype: dict[str, int]
    """
    counts = {
        "created": 0,
        "held": 0,
        "wrong_polarity": 0,
        "id_taken": 0,
        "failed": 0,
    }

    async with async_session() as session:
        # Read through the column, which answers the standard notation for a
        # row in either spelling.
        rows = (
            await session.execute(
                select(
                    IonizationMechanism.ionization_mechanism_id,
                    IonizationMechanism.ionization_mechanism,
                    IonizationMechanism.ionization_mechanism_polarity,
                )
            )
        ).all()
    held: dict[str, dict[str, str]] = {}
    for mechanism_id, notation, polarity in rows:
        held.setdefault(notation, {})[polarity] = mechanism_id
    ids_in_use = {mechanism_id for mechanism_id, _, _ in rows}

    for shipped in shipped_mechanisms():
        stored = held.get(shipped.notation)
        if stored is not None:
            if shipped.polarity in stored:
                counts["held"] += 1
                continue
            broken = ", ".join(sorted(stored.values()))
            runtime.logger.warning(
                f"Ionization mechanism {shipped.notation} is stored here only "
                f"under polarity {', '.join(sorted(stored))} ({broken}), but it "
                f"makes a {shipped.polarity} ion, so no mode can hold it and no "
                "run can search it. Delete that row to let Mascope create the "
                "mechanism it ships at the next start."
            )
            counts["wrong_polarity"] += 1
            continue
        if shipped.mechanism_id in ids_in_use:
            runtime.logger.warning(
                f"Ionization mechanism {shipped.notation} was not created: the "
                f"id Mascope creates it under, {shipped.mechanism_id}, is held "
                "by another mechanism."
            )
            counts["id_taken"] += 1
            continue

        started = time.perf_counter()
        try:
            async with async_session() as session:
                compounds, ions = await add_ionization_mechanism(
                    session,
                    IonizationMechanism(
                        ionization_mechanism_id=shipped.mechanism_id,
                        ionization_mechanism_polarity=shipped.polarity,
                        ionization_mechanism=shipped.notation,
                    ),
                )
                await session.commit()
        except Exception as e:
            runtime.logger.error(
                f"Could not create the ionization mechanism {shipped.notation} "
                f"Mascope ships: {e}"
            )
            counts["failed"] += 1
            continue
        runtime.logger.info(
            f"Ionization mechanism {shipped.notation} created as "
            f"{shipped.mechanism_id}: {ions} target ions for {compounds} "
            f"compounds in {time.perf_counter() - started:.1f} s"
        )
        counts["created"] += 1

    # Every start, so the log says the pass ran on a server that needed nothing.
    exceptions = "".join(
        f", {counts[key]} {what}"
        for key, what in (
            ("wrong_polarity", "stored under the wrong polarity"),
            ("id_taken", "blocked by another row on their id"),
            ("failed", "failed"),
        )
        if counts[key]
    )
    runtime.logger.info(
        f"System ionization mechanisms: {counts['created']} created, "
        f"{counts['held']} held{exceptions}"
    )
    return counts


async def ensure_system_ionization_modes() -> dict[str, int]:
    """Create the catalogue's modes that this deployment can hold.

    :return: Counts of the modes seeded, claimed, and skipped for a missing
        mechanism or a name in use.
    :rtype: dict[str, int]
    """
    counts = {
        "seeded": 0,
        "claimed": 0,
        "no_mechanism": 0,
        "name_taken": 0,
        "edited": 0,
    }

    async with async_session() as session:
        # Keyed on name AND polarity: a legacy row stored under the wrong
        # polarity must not be adopted. Every PATCH of a mode revalidates its
        # mechanisms against its polarity, so a mode built on a mismatched row
        # could never be given its collections, or edited at all.
        mechanisms = {
            (name, polarity): mechanism_id
            for mechanism_id, name, polarity in (
                await session.execute(
                    select(
                        IonizationMechanism.ionization_mechanism_id,
                        IonizationMechanism.ionization_mechanism,
                        IonizationMechanism.ionization_mechanism_polarity,
                    )
                )
            ).all()
        }
        every_row = (await session.execute(select(IonizationMode))).scalars().all()
        # The current versions. A superseded one keeps the key and the name
        # its successor has, and claims nothing.
        existing = {
            mode.ionization_mode_id: mode for mode in every_row if mode.is_active
        }
        ids_in_use = {mode.ionization_mode_id for mode in every_row}
        keyed = {
            mode.system_key for mode in existing.values() if mode.system_key is not None
        }
        names_in_use = {
            mode.ionization_mode_name: mode.ionization_mode_id
            for mode in existing.values()
        }
        # Every mechanism a shipped mode declares is on the shipped list.
        polarity_of = {
            shipped.notation: shipped.polarity for shipped in shipped_mechanisms()
        }

        for system_key, mode_id, name, polarity, wanted in SYSTEM_MODES:
            if system_key in keyed:
                continue

            mechanism_ids = [
                mechanisms.get((mechanism, polarity_of[mechanism]))
                for mechanism in wanted
            ]
            if any(mechanism_id is None for mechanism_id in mechanism_ids):
                counts["no_mechanism"] += 1
                continue

            claimable = existing.get(mode_id)
            if claimable is not None:
                # Only if the row is still what was seeded. A downgrade leaves
                # it editable like any other, and claiming a renamed or
                # re-pointed one would lock that edit under the catalogue's
                # key - which the guard then refuses to undo, token included.
                still_ours = (
                    claimable.ionization_mode_name == name
                    and claimable.ionization_mode_polarity == polarity
                    and claimable.ionization_mode_token is None
                    and set(claimable.ionization_mechanism_ids or [])
                    == set(mechanism_ids)
                )
                if not still_ours:
                    runtime.logger.warning(
                        f"Ionization mode {mode_id} sits under the id the "
                        f"chemistry {system_key!r} uses but has been edited "
                        "since, so it was not claimed. Delete it, or restore "
                        "its seeded name, polarity and mechanisms and clear "
                        "its token, to let Mascope claim it again."
                    )
                    counts["edited"] += 1
                    continue
                await session.execute(
                    update(IonizationMode)
                    .where(IonizationMode.ionization_mode_id == mode_id)
                    .values(system_key=system_key)
                )
                counts["claimed"] += 1
                continue
            if mode_id in ids_in_use:
                # A superseded version holds the catalogue's id: the chemistry
                # was claimed, edited, and has since lost its key.
                runtime.logger.warning(
                    f"Ionization mode {mode_id} sits under the id the chemistry "
                    f"{system_key!r} uses but is a version an edit has replaced, "
                    "so the chemistry was not created."
                )
                counts["edited"] += 1
                continue

            taken_by = names_in_use.get(name)
            if taken_by is not None:
                runtime.logger.warning(
                    f"Ionization mode name {name!r} is already used by mode "
                    f"{taken_by}, so the chemistry {system_key!r} Mascope ships "
                    "was not created. Rename that mode to take it."
                )
                counts["name_taken"] += 1
                continue

            session.add(
                IonizationMode(
                    ionization_mode_id=mode_id,
                    ionization_mode_name=name,
                    ionization_mode_token=None,
                    ionization_mode_polarity=polarity,
                    ionization_mechanism_ids=mechanism_ids,
                    system_key=system_key,
                )
            )
            names_in_use[name] = mode_id
            counts["seeded"] += 1

        await session.commit()

    if counts["seeded"] or counts["claimed"]:
        runtime.logger.info(
            f"System ionization modes: {counts['seeded']} created, "
            f"{counts['claimed']} claimed"
        )
    if counts["no_mechanism"]:
        runtime.logger.warning(
            f"System ionization modes: {counts['no_mechanism']} not created, "
            "an ionization mechanism they need could not be created here"
        )
    return counts


async def ensure_system_ionization_collections() -> dict[str, int]:
    """Create the collections of the seeded chemistries, keep the seed's own
    up to date, and point each seeded mode that points at none at its own.

    One transaction per collection, so a start that fails partway keeps what
    it finished, and a collection it did not finish is still missing, or
    still holds what it held, for the next start to try again.

    :return: Counts of the collections created, brought up to date, held as
        they are, left as the deployment edited them, skipped for want of
        their mode, and failed, and of the mode pointers set.
    :rtype: dict[str, int]
    """
    counts = {
        "created": 0,
        "updated": 0,
        "held": 0,
        "edited": 0,
        "no_mode": 0,
        "failed": 0,
        "pointed": 0,
    }
    seeded = seeded_collections()

    async with async_session() as session:
        # The current versions. A superseded one keeps its chemistry's key.
        modes = {
            mode.system_key: mode
            for mode in (
                await session.execute(
                    select(IonizationMode).where(
                        IonizationMode.system_key.is_not(None),
                        IonizationMode.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        }
        stored = {
            collection.target_collection_id: collection
            for collection in (
                await session.execute(
                    select(TargetCollection)
                    .where(
                        TargetCollection.target_collection_id.in_(
                            [collection.collection_id for collection in seeded]
                        )
                    )
                    .options(
                        selectinload(TargetCollection.target_compound).selectinload(
                            TargetCompoundInTargetCollection.target_compound
                        )
                    )
                )
            )
            .scalars()
            .all()
        }

    edited: list[str] = []
    pointers: list[tuple[str, str, str]] = []
    for collection in seeded:
        mode = modes.get(collection.system_key)
        if mode is None:
            # Its chemistry was not seeded here (a mechanism missing, or its
            # name taken), so there is no mode to read it.
            counts["no_mode"] += 1
            continue

        row = stored.get(collection.collection_id)
        try:
            if row is None:
                await _create_collection(collection)
                counts["created"] += 1
            else:
                held = _stored_digest(row)
                if held == collection.digest:
                    counts["held"] += 1
                elif held in SEEDED_BEFORE:
                    await _bring_up_to_date(collection)
                    counts["updated"] += 1
                else:
                    edited.append(collection.collection_id)
                    counts["edited"] += 1
        except Exception as e:
            runtime.logger.error(
                f"Could not seed the collection {collection.collection_id} "
                f"('{collection.name}') Mascope ships: {e}"
            )
            counts["failed"] += 1
            continue

        pointers.append(
            (mode.ionization_mode_id, collection.mode_field, collection.collection_id)
        )

    async with async_session() as session:
        for mode_id, field, collection_id in pointers:
            column = getattr(IonizationMode, field)
            # Only where it points at none. A mode pointed at a collection
            # keeps it: the deployment chose it, before this pass or while
            # it ran.
            pointed = await session.execute(
                update(IonizationMode)
                .where(
                    IonizationMode.ionization_mode_id == mode_id,
                    column.is_(None),
                )
                .values({field: collection_id})
            )
            counts["pointed"] += pointed.rowcount
        await session.commit()

    # Every start, so the log says the pass ran on a server that needed nothing.
    exceptions = "".join(
        f", {counts[key]} {what}"
        for key, what in (
            ("updated", "brought up to date"),
            ("no_mode", "without their mode"),
            ("failed", "failed"),
        )
        if counts[key]
    )
    if edited:
        exceptions += (
            f", {len(edited)} left as this server edited them ({', '.join(edited)})"
        )
    runtime.logger.info(
        f"System target collections: {counts['created']} created, "
        f"{counts['held']} held{exceptions}; {counts['pointed']} mode "
        f"collection{'' if counts['pointed'] == 1 else 's'} pointed at them"
    )
    return counts


def _stored_digest(collection: TargetCollection) -> str:
    """What a stored collection holds, as :func:`collection_digest` reads it.

    :param collection: The row, its compounds loaded.
    :return: Its digest.
    :rtype: str
    """
    return collection_digest(
        name=collection.target_collection_name,
        description=collection.target_collection_description,
        collection_type=collection.target_collection_type,
        workspace_id=collection.workspace_id,
        compounds=(
            (
                link.target_compound.target_compound_name,
                link.target_compound.target_compound_formula,
            )
            for link in collection.target_compound
        ),
    )


def _compounds_to_create(collection: SeededCollection) -> list[TargetCompoundBase]:
    return [
        TargetCompoundBase(
            target_compound_name=compound.name,
            target_compound_formula=compound.formula,
        )
        for compound in collection.compounds
    ]


async def _create_collection(collection: SeededCollection) -> None:
    """Create a seeded collection and its compounds, in one transaction.

    Written here rather than through ``create_target_collection``, which
    generates the id: the seed's collection is known by the one the catalogue
    gives it.
    """
    async with async_session() as session:
        compounds = await create_target_compound(
            target_compounds=_compounds_to_create(collection),
            independent_transaction=False,
            session=session,
        )
        session.add(
            TargetCollection(
                target_collection_id=collection.collection_id,
                target_collection_name=collection.name,
                target_collection_description=collection.description,
                target_collection_type=collection.collection_type,
                workspace_id=None,
            )
        )
        for compound_id in dict.fromkeys(compounds["target_compound_ids"]):
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=compound_id,
                    target_collection_id=collection.collection_id,
                )
            )
        await session.commit()


async def _bring_up_to_date(collection: SeededCollection) -> None:
    """Make a collection an earlier release seeded what this one seeds.

    Through the path a person's edit of the collection takes, so the batches it
    serves are marked for a rematch as they would be after that edit. The
    compounds it no longer holds stay in the library, as they do when a person
    takes one out of a collection.
    """
    compounds = await create_target_compound(
        target_compounds=_compounds_to_create(collection),
        independent_transaction=True,
    )
    await update_target_collection(
        target_collection_id=collection.collection_id,
        target_collection_update=TargetCollectionUpdate(
            target_collection_name=collection.name,
            target_collection_description=collection.description,
            target_collection_type=collection.collection_type,
            target_compound_ids=list(dict.fromkeys(compounds["target_compound_ids"])),
        ),
        independent_transaction=True,
    )


def run_ensure_system_ionization() -> dict[str, dict[str, int]]:
    """Synchronous entry point for CLI and scripts."""
    return asyncio.run(ensure_system_ionization())
