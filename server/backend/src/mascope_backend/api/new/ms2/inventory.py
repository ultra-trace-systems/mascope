"""The group inventory: each file's MS2 groups, as rows of ``ms2_group``.

Which samples of a batch have MS2, of which precursors, at what energies, is
one query of the inventory rather than a read of every file's MS2 store or raw
file (``docs/dev/ms2_analysis.md``, section 2.3). A file's rows are read from
its MS2 store (:meth:`mascope_signal.ms2_store.Ms2Store.inventory`) and
replaced whole each time the store is written (:func:`write_ms2_inventory`).

**A sample's groups.** A row describes a group of the whole file. A sample
holds those of its polarity whose span - the time of their first scan to that
of their last - meets its window, selected as the reader selects a scan by
time (:func:`mascope_signal.ms2_store._window_mask`). A window that holds a
group's span whole holds every one of its scans; one that meets it only in
part cuts it, and such a group is listed with ``whole`` false, its counts and
span being the file's. One query answers for one sample, a listing's every
sample (:func:`ms2_group_count`) or a batch's (:func:`batch_ms2_groups`).

A sample with no polarity holds the groups of both: its reads take every scan
of the file in its window.
"""

import asyncio

import numpy as np
from sqlalchemy import Float, String, and_, delete, func, insert, literal, or_, select
from sqlalchemy.sql.elements import ColumnElement

from mascope_backend.db import Ms2Group, SampleFile, SampleItem, async_session
from mascope_signal.ms2_store import Ms2Store


#: How the reader widens a window: both bounds by machine epsilon times the
#: upper one (``mascope_signal.ms2_store._window_mask``).
_EPS = float(np.finfo(np.float64).eps)

#: The columns a group is served with, besides its sample's and ``whole``.
_COLUMNS = (
    "sample_file_id",
    "polarity",
    "precursor_key",
    "parent_peak_mz",
    "activation",
    "activation_kind",
    "nce",
    "hcd_energy",
    "isolation_widths",
    "scan_count",
    "t_min",
    "t_max",
    "low_mass",
    "high_mass",
    "located_precursor_mz",
)


def _read_inventory(filename: str) -> list[dict] | None:
    """The file's groups as its MS2 store holds them; None for no store."""
    store = Ms2Store.open(filename)
    return None if store is None else store.inventory()


def _row(sample_file_id: str, group: dict) -> dict:
    return {
        "sample_file_id": sample_file_id,
        "polarity": group["polarity"],
        "precursor_key": group["key"],
        "parent_peak_mz": group["parent_peak_mz"],
        "activation": group["activation"],
        "activation_kind": group["activation_kind"],
        "nce": group["nce"],
        "hcd_energy": group["hcd_energy"],
        "isolation_widths": group["isolation_widths"],
        "scan_count": group["scan_count"],
        "t_min": group["t_min"],
        "t_max": group["t_max"],
        "low_mass": group["low_mass"],
        "high_mass": group["high_mass"],
    }


async def write_ms2_inventory(sample_file_id: str, filename: str) -> int:
    """Replace a file's rows with the groups its MS2 store holds.

    What a file's inventory is after its store is written or removed: the
    store's groups, or none for a file whose store cannot be read - none
    written, one of a layout or version this build does not read, one the
    props record that is not on disk.

    :param sample_file_id: The file's id.
    :param filename: Its stored name.
    :return: How many groups it now lists.
    """
    groups = await asyncio.to_thread(_read_inventory, filename) or []
    async with async_session() as session:
        await session.execute(
            delete(Ms2Group).where(Ms2Group.sample_file_id == sample_file_id)
        )
        if groups:
            await session.execute(
                insert(Ms2Group), [_row(sample_file_id, group) for group in groups]
            )
        await session.commit()
    return len(groups)


def _bounds(t0: ColumnElement, t1: ColumnElement) -> tuple:
    """A window's bounds widened as the reader widens them, None where open.

    With no upper bound the reader widens by the file's last scan's time,
    which no row holds; the group's own last scan's stands in for it. That
    is never later, so a group is held, or held whole, only where the reader
    holds its scans, and a start on one of its scans' times still takes that
    scan.
    """
    eps = func.coalesce(t1, Ms2Group.t_max) * _EPS
    return t0 - eps, t1 + eps


def meets_window(
    polarity: ColumnElement, t0: ColumnElement, t1: ColumnElement
) -> ColumnElement:
    """Whether a sample of ``polarity`` and window ``[t0, t1]`` holds the
    group: one of its scans may fall in the window.

    :param polarity: The sample's polarity; NULL holds either.
    :param t0: Its window's start [s]; NULL for the first scan.
    :param t1: Its end [s]; NULL for the last scan.
    """
    low, high = _bounds(t0, t1)
    return and_(
        or_(polarity.is_(None), Ms2Group.polarity == polarity),
        or_(t0.is_(None), Ms2Group.t_max > low),
        or_(t1.is_(None), Ms2Group.t_min < high),
    )


def holds_whole(t0: ColumnElement, t1: ColumnElement) -> ColumnElement:
    """Whether the window ``[t0, t1]`` holds every scan of the group."""
    low, high = _bounds(t0, t1)
    return and_(
        or_(t0.is_(None), Ms2Group.t_min > low),
        or_(t1.is_(None), Ms2Group.t_max < high),
    )


def ms2_group_count(sample) -> ColumnElement:
    """How many groups each sample of a listing holds, as a column of it.

    :param sample: The listing's sample entity (``Sample``, the view, or
        ``SampleItem``), whose rows the count is correlated with.
    :return: A scalar subquery, labelled ``ms2_group_count``.
    """
    return (
        select(func.count())
        .select_from(Ms2Group)
        .where(
            Ms2Group.sample_file_id == sample.sample_file_id,
            meets_window(sample.polarity, sample.t0, sample.t1),
        )
        .correlate(sample)
        .scalar_subquery()
        .label("ms2_group_count")
    )


def _ordered(statement):
    """A sample's groups in the summary's order: by parent peak, then by
    acquisition."""
    return statement.order_by(
        Ms2Group.parent_peak_mz, Ms2Group.t_min, Ms2Group.precursor_key
    )


def group_record(row, sample_item_id: str | None = None) -> dict:
    """A group as the routes serve it.

    Its row's columns, ``isolation_width`` (the one width its scans share,
    else None, as the summary has it), ``whole``, and the sample's id when
    given.
    """
    group = row.Ms2Group
    widths = group.isolation_widths
    record = {name: getattr(group, name) for name in _COLUMNS}
    record["isolation_width"] = (
        widths[0]["isolation_width"] if len(widths) == 1 else None
    )
    record["whole"] = bool(row.whole)
    if sample_item_id is not None:
        record = {"sample_item_id": sample_item_id, **record}
    return record


async def sample_ms2_inventory(sample) -> tuple[list[dict], set[tuple[str, str]]]:
    """The groups one sample holds, and every group its file lists.

    One query of the file's rows.

    :param sample: The sample (``Sample``, or anything with its
        ``sample_file_id``, ``polarity``, ``t0`` and ``t1``).
    :return: The sample's groups in the summary's order (:func:`group_record`),
        and the (polarity, key) of every row of its file.
    """
    polarity = literal(sample.polarity, String)
    t0, t1 = literal(sample.t0, Float), literal(sample.t1, Float)
    statement = _ordered(
        select(
            Ms2Group,
            meets_window(polarity, t0, t1).label("held"),
            holds_whole(t0, t1).label("whole"),
        ).where(Ms2Group.sample_file_id == sample.sample_file_id)
    )
    async with async_session() as session:
        rows = (await session.execute(statement)).all()
    listed = {(row.Ms2Group.polarity, row.Ms2Group.precursor_key) for row in rows}
    return [group_record(row) for row in rows if row.held], listed


async def sample_ms2_groups(sample) -> list[dict]:
    """The groups one sample holds, in the summary's order
    (:func:`sample_ms2_inventory`)."""
    groups, _ = await sample_ms2_inventory(sample)
    return groups


async def batch_ms2_groups(sample_batch_id: str) -> list[dict]:
    """Every group of every sample of a batch, in one query.

    Ordered as the samples listing orders them by default, by acquisition
    time, and within a sample in the summary's order.

    :param sample_batch_id: The batch.
    :return: One record per (sample, group), with the sample's id
        (:func:`group_record`); empty for a batch none of whose samples holds
        a group.
    """
    statement = (
        select(
            SampleItem.sample_item_id,
            Ms2Group,
            holds_whole(SampleItem.t0, SampleItem.t1).label("whole"),
        )
        .join(SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id)
        .join(
            Ms2Group,
            and_(
                Ms2Group.sample_file_id == SampleItem.sample_file_id,
                meets_window(SampleItem.polarity, SampleItem.t0, SampleItem.t1),
            ),
        )
        .where(SampleItem.sample_batch_id == sample_batch_id)
        .order_by(
            SampleFile.datetime_utc,
            SampleItem.sample_item_id,
            Ms2Group.parent_peak_mz,
            Ms2Group.t_min,
            Ms2Group.precursor_key,
        )
    )
    async with async_session() as session:
        rows = (await session.execute(statement)).all()
    return [group_record(row, row.sample_item_id) for row in rows]
