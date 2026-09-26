"""
Seed the ionization chemistry Mascope ships: its mechanisms, then its modes.

Every deployment names its chemistries itself, so one reagent is spelled many
ways and nothing can refer to a chemistry as such. A mode carrying a
``system_key`` (:mod:`mascope_backend.ionization_catalogue`) is that name: the
same string on every server, so a method binding has an identity to point at.
And a run searches a channel only where the server holds its mechanism, so
the mechanisms a server holds decide the chemistry its runs search under.
Seeding them makes that the same on every server, a fresh one included, from
its first start.

Two passes, in this order, at every start (:func:`ensure_system_ionization`).
Both are idempotent, and they run in the main process before any worker
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

Entry Points:
- Async: `ensure_system_ionization()` for both passes, as a start runs them
- Sync: `run_ensure_system_ionization()` for CLI and scripts
"""

import asyncio
import time

from sqlalchemy import select, update

from mascope_backend.api.controllers.ionization_mechanisms.ionization_mechanisms_controller import (
    add_ionization_mechanism,
)
from mascope_backend.db import IonizationMechanism, IonizationMode, async_session
from mascope_backend.ionization_catalogue import SYSTEM_MODES, shipped_mechanisms
from mascope_backend.runtime import runtime


async def ensure_system_ionization() -> dict[str, dict[str, int]]:
    """Seed the mechanisms Mascope ships, then its modes.

    :return: Each pass's counts, under ``"mechanisms"`` and ``"modes"``.
    :rtype: dict[str, dict[str, int]]
    """
    mechanisms = await ensure_system_ionization_mechanisms()
    modes = await ensure_system_ionization_modes()
    return {"mechanisms": mechanisms, "modes": modes}


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
        existing = {
            mode.ionization_mode_id: mode
            for mode in (await session.execute(select(IonizationMode))).scalars().all()
        }
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


def run_ensure_system_ionization() -> dict[str, dict[str, int]]:
    """Synchronous entry point for CLI and scripts."""
    return asyncio.run(ensure_system_ionization())
