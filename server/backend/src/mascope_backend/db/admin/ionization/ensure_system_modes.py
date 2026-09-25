"""
Seed the ionization modes Mascope ships, from the mechanisms a server has.

Every deployment names its chemistries itself, so one reagent is spelled many
ways and nothing can refer to a chemistry as such. A mode carrying a
``system_key`` (:mod:`mascope_backend.ionization_catalogue`) is that name: the
same string on every server, so a method binding has an identity to point at.

**Mechanisms are never created here.** Creating one through the API also
builds the target ions of every compound already in the library, and every
compound imported afterwards gains ions for it - cost a deployment that does
not run the chemistry should not pay, and work a bare INSERT would skip,
leaving an adopted mode unable to match anything in an existing library. So a
chemistry is seeded only where the mechanisms it names are already present,
matched on name *and* the polarity they are stored under. The name is compared
in the standard adduct notation the column reads it in, so a row still holding
the legacy spelling (``+Br-`` for ``[M+Br]-``) counts. The rest are left
alone; a deployment that adds the mechanism properly gets its mode at the next
start.

Idempotent, and run in the main process before any worker starts, so there is
nothing to race with:

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
- Async: `ensure_system_ionization_modes()` for use in async code
- Sync: `run_ensure_system_ionization_modes()` for CLI and scripts
"""

import asyncio

from sqlalchemy import select, update

from mascope_backend.db import IonizationMechanism, IonizationMode, async_session
from mascope_backend.ionization_catalogue import MECHANISM_POLARITIES, SYSTEM_MODES
from mascope_backend.runtime import runtime


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

        for system_key, mode_id, name, polarity, wanted in SYSTEM_MODES:
            if system_key in keyed:
                continue

            mechanism_ids = [
                mechanisms.get((mechanism, MECHANISM_POLARITIES[mechanism]))
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
        runtime.logger.debug(
            f"System ionization modes: {counts['no_mechanism']} not created, "
            "their ionization mechanisms are not configured here"
        )
    return counts


def run_ensure_system_ionization_modes() -> dict[str, int]:
    """Synchronous entry point for CLI and scripts."""
    return asyncio.run(ensure_system_ionization_modes())
