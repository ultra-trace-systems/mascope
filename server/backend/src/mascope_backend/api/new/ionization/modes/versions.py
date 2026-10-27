"""Versions of an ionization mode: what an edit keeps, and who follows it.

A mode is a versioned setup (``docs/dev/ingest_routing_and_splitting.md``,
phase 8, model 4.3). Each row is one version, and its id names that version
alone: a row is not edited in what a run computes from it. An edit that
changes one of :data:`VERSIONED_FIELDS` inserts the next version instead -
``version`` one more, ``supersedes_id`` the row it replaces - and marks the
row it replaces inactive. An edit of the name, the token or the instrument
scope changes no result, only which files are routed to the mode and what it
is called, and is made on the active row in place.

Two kinds of reference meet a new version differently:

- **What was computed under a version keeps it.** A sample item, and so its
  matches, calibration and assignment runs, points at the version it was
  bound under; the edit moves none of them. Re-processing is what moves them:
  a rematch, a recalibration, an assignment run or a re-bound file reads the
  setup as it stands, so it first moves its items onto the current version
  (:func:`advance_to_current_versions`), and what it then computes is
  recorded under that version.
- **What routes new work follows the setup.** A method binding names the mode
  a file of its method is bound to, so the edit repoints the bindings at the
  new version (:func:`repoint_routing`), and the rungs that choose a mode for
  a new file - by declaration, token, binding or detection - see the active
  rows only.

The batch ledger is partitioned per setup, not per version: an anchor made
under one version stays the anchor of the same species under the next, so
the partition key is the setup's first version (:func:`root_ids`).
"""

from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.db import IonizationMode, MethodBinding, SampleItem, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


#: The fields a run computes from. An edit that changes one mints a version.
VERSIONED_FIELDS = (
    "ionization_mode_polarity",
    "ionization_mechanism_ids",
    "calibration_collection_id",
    "diagnostic_collection_id",
)

#: Columns a new version does not copy from the row it supersedes.
_OWN_COLUMNS = frozenset(
    {"ionization_mode_id", "version", "is_active", "supersedes_id"}
)


class SupersededVersionError(ValueError):
    """An edit or delete named a version a later one has replaced."""


async def _links(session: AsyncSession) -> dict[str, tuple[str | None, bool]]:
    """Every mode row's predecessor and whether it is active.

    Read whole: the table holds a deployment's modes and their versions, tens
    of rows, and every walk below needs the chain rather than a row.
    """
    rows = await session.execute(
        select(
            IonizationMode.ionization_mode_id,
            IonizationMode.supersedes_id,
            IonizationMode.is_active,
        )
    )
    return {mode_id: (supersedes, active) for mode_id, supersedes, active in rows}


def _successors(links: dict[str, tuple[str | None, bool]]) -> dict[str, str]:
    return {
        supersedes: mode_id
        for mode_id, (supersedes, _) in links.items()
        if supersedes is not None
    }


def _chain_back(links: dict[str, tuple[str | None, bool]], mode_id: str) -> list[str]:
    """``mode_id`` and every version before it, newest first."""
    chain, seen = [], set()
    current: str | None = mode_id
    while current is not None and current in links and current not in seen:
        chain.append(current)
        seen.add(current)
        current = links[current][0]
    return chain


def make_version(current: IonizationMode, changes: dict) -> IonizationMode:
    """The version that replaces ``current``, with ``changes`` applied.

    Not added to a session: the caller marks ``current`` inactive and flushes
    first, since the active rows are what the token and system-key indexes
    keep unique.

    :param current: The active version being edited.
    :param changes: The edited fields and their new values.
    :return: The new, active version.
    """
    copied = {
        column.name: getattr(current, column.name)
        for column in IonizationMode.__table__.columns
        if column.name not in _OWN_COLUMNS
    }
    copied.update(changes)
    return IonizationMode(
        ionization_mode_id=gen_id(16),
        version=current.version + 1,
        is_active=True,
        supersedes_id=current.ionization_mode_id,
        **copied,
    )


async def repoint_routing(session: AsyncSession, old_id: str, new_id: str) -> None:
    """Move what routes new files from one version to the next.

    A method binding names the mode a file of its method binds to, and the
    candidate it is counting towards. Both follow the edit, so a method keeps
    routing to the setup it learned rather than to a version nobody can bind
    to any more. The binding's ``chemistry_keys`` history is left as it is:
    a version that changes the mechanisms is a new chemistry to it, as an
    in-place edit of them always was.
    """
    await session.execute(
        update(MethodBinding)
        .where(MethodBinding.ionization_mode_id == old_id)
        .values(ionization_mode_id=new_id)
    )
    await session.execute(
        update(MethodBinding)
        .where(MethodBinding.candidate_mode_id == old_id)
        .values(candidate_mode_id=new_id)
    )


async def lineage(session: AsyncSession, mode_id: str) -> list[str]:
    """Every version of the setup ``mode_id`` belongs to, newest first."""
    links = await _links(session)
    successors = _successors(links)
    newest = mode_id
    while newest in successors:
        newest = successors[newest]
    return _chain_back(links, newest)


async def superseded_ids(
    session: AsyncSession, mode_ids: Iterable[str]
) -> dict[str, list[str]]:
    """The earlier versions of each mode, newest first.

    What lets a reader that holds only the current versions resolve an item
    bound under an earlier one.
    """
    links = await _links(session)
    return {mode_id: _chain_back(links, mode_id)[1:] for mode_id in mode_ids}


async def root_ids(session: AsyncSession, mode_ids: Iterable[str]) -> dict[str, str]:
    """Each mode's first version: the setup's identity across its versions."""
    links = await _links(session)
    return {
        mode_id: (_chain_back(links, mode_id) or [mode_id])[-1] for mode_id in mode_ids
    }


async def current_versions(
    session: AsyncSession, mode_ids: Iterable[str | None]
) -> dict[str, IonizationMode | None]:
    """The active version of each mode's setup.

    :param mode_ids: Any versions; None and unknown ids are left out.
    :return: Each given id's active version, the row itself where it is
        active, or None where no version of its setup is.
    """
    wanted = {mode_id for mode_id in mode_ids if mode_id is not None}
    if not wanted:
        return {}
    links = await _links(session)
    successors = _successors(links)
    heads: dict[str, str | None] = {}
    for mode_id in wanted:
        if mode_id not in links:
            continue
        current, seen = mode_id, {mode_id}
        while not links[current][1] and current in successors:
            current = successors[current]
            if current in seen:
                break
            seen.add(current)
        heads[mode_id] = current if links[current][1] else None
    rows = {
        row.ionization_mode_id: row
        for row in (
            await session.execute(
                select(IonizationMode).where(
                    IonizationMode.ionization_mode_id.in_(
                        {head for head in heads.values() if head is not None}
                    )
                )
            )
        )
        .scalars()
        .all()
    }
    return {
        mode_id: rows.get(head) if head is not None else None
        for mode_id, head in heads.items()
    }


async def advance_to_current_versions(
    session: AsyncSession,
    *,
    sample_item_ids: Iterable[str] | None = None,
    sample_batch_ids: Iterable[str] | None = None,
) -> int:
    """Move items bound under a superseded version onto the current one.

    Called where a re-process starts, before it reads the items' modes, so it
    computes under the setup as it stands and what it writes is recorded
    under that version. An item whose setup has no active version left keeps
    the one it has. The caller commits.

    :param sample_item_ids: The items to move.
    :param sample_batch_ids: Or every item of these batches.
    :return: How many items moved.
    """
    item_filter = []
    if sample_item_ids is not None:
        item_filter.append(SampleItem.sample_item_id.in_(list(sample_item_ids)))
    if sample_batch_ids is not None:
        item_filter.append(SampleItem.sample_batch_id.in_(list(sample_batch_ids)))
    if not item_filter:
        return 0

    stale = (
        (
            await session.execute(
                select(SampleItem.ionization_mode_id)
                .join(
                    IonizationMode,
                    IonizationMode.ionization_mode_id == SampleItem.ionization_mode_id,
                )
                .where(*item_filter, IonizationMode.is_active.is_(False))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if not stale:
        return 0
    moved = 0
    for old_id, head in (await current_versions(session, stale)).items():
        if head is None:
            continue
        result = await session.execute(
            update(SampleItem)
            .where(*item_filter, SampleItem.ionization_mode_id == old_id)
            .values(ionization_mode_id=head.ionization_mode_id)
        )
        moved += result.rowcount or 0
    return moved


async def move_to_current_versions(
    *,
    sample_item_ids: Iterable[str] | None = None,
    sample_batch_ids: Iterable[str] | None = None,
) -> int:
    """:func:`advance_to_current_versions` in a transaction of its own.

    For a re-process that reads its items' modes through sessions of its own:
    the move is committed before any of them reads.
    """
    async with async_session() as session:
        moved = await advance_to_current_versions(
            session, sample_item_ids=sample_item_ids, sample_batch_ids=sample_batch_ids
        )
        await session.commit()
    if moved:
        runtime.logger.info(
            f"Moved {moved} samples onto the current version of their "
            "ionization mode before re-processing them"
        )
    return moved
