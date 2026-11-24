"""What the batch's untargeted search remembers of the anchors it searched.

The search (``batch_untargeted.run_batch_untargeted_search``) writes two
records on the anchors it searched, once every sample's rows are merged and
before anything else: the persistence record of an anchor persistence alone
admitted (``batch_peaks.PERSISTENCE_KEY``), and the record of an anchor it
found nothing for (:data:`SEARCHED_EMPTY_KEY`).

The second lets the next search leave that anchor out (MAS-1189). Searching it
again costs its sample a whole search - the peak read, the candidate grid, the
judging - and finds nothing again while nothing the search reads of it has
changed. What it reads of an anchor is its representative, the brightest
member that reads nothing: the sample, the peak, the ledger row the member was
folded from and its height; whether the anchor is persistent, which admits a
faint representative; and what it reads besides its representatives, the
search's key: every setting, the library, the chemistry, the engine
version and the deployed Mascope version. A member folded in that is not the brightest is not read by the
search, so it searches nothing again; a brighter one becomes the
representative, and the anchor is searched on it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any, Optional

from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.batch_lock import _acquire_batch_fold_lock
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    PERSISTENCE_KEY,
)
from mascope_backend.api.new.peak_assignments.config import (
    PEAK_ASSIGNMENT_ENGINE_VERSION,
    PeakAssignmentConfig,
)
from mascope_backend.db import BatchPeak, async_session
from mascope_backend.runtime import runtime


#: The anchor's provenance key the batch's untargeted search
#: (``batch_untargeted.py``) writes its record under where it searched the
#: anchor and found nothing: no row on any of its members. The record names
#: what that search read for it - the search's key (its settings, the
#: library, the chemistry, the engine and Mascope versions), the
#: representative it ran on (its sample, its peak, the ledger row it was
#: folded from, its height), whether the anchor was persistent and whether
#: persistence alone admitted it - and the run. A later search leaves the
#: anchor out while all of that still reads the same (:func:`searched_in_vain`).
#: It is about an anchor that reads nothing, so it stands only while the
#: anchor does (``batch_peaks_controller._consensus_of``).
SEARCHED_EMPTY_KEY = "searched_empty"


def searched_empty_record_of(anchor: Any) -> Optional[dict]:
    """The record an anchor carries under ``provenance.searched_empty``, or
    None: a dict naming the search's key is one, anything else is not."""
    provenance = getattr(anchor, "provenance", None)
    if not isinstance(provenance, dict):
        return None
    record = provenance.get(SEARCHED_EMPTY_KEY)
    if isinstance(record, dict) and record.get("key"):
        return record
    return None


def search_key(
    config: PeakAssignmentConfig,
    library: dict,
    chemistry: str,
    engine_version: Optional[str] = None,
    mascope_version: Optional[str] = None,
) -> str:
    """A short digest of what a search reads besides its representatives:
    every setting it runs on, the library (``batch_library.library_read``),
    the chemistry (``batch_library.chemistry_read``), the engine version and
    the deployed Mascope version. Equal for two searches that read these
    alike.

    The engine version (``PEAK_ASSIGNMENT_ENGINE_VERSION``) is not bumped with
    every change to the search, so the key also reads ``MASCOPE_VERSION`` as
    ``GET /api/version`` reads it: an upgrade searches every anchor again. A
    deployment that tracks ``latest`` reads ``latest`` before and after an
    upgrade, and so does not."""
    text = json.dumps(
        {
            "settings": config.model_dump(mode="json"),
            "library": library,
            "chemistry": chemistry,
            "engine_version": engine_version or PEAK_ASSIGNMENT_ENGINE_VERSION,
            "mascope_version": mascope_version or runtime.version or "unknown",
        },
        sort_keys=True,
    )
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def representative_record(member: Any) -> dict:
    """What a search reads of the member it runs an anchor's search on: the
    sample, the peak, the ledger row it was folded from - a new run of the
    sample folds a new one - and its height, which the floor admits it on."""
    intensity = getattr(member, "intensity", None)
    return {
        "sample_item_id": member.sample_item_id,
        "sample_peak_id": str(member.sample_peak_id),
        "peak_assignment_id": getattr(member, "peak_assignment_id", None),
        "intensity": None if intensity is None else float(intensity),
    }


def searched_empty_record(
    key: str,
    member: Any,
    *,
    persistent: bool,
    by_persistence: bool,
    batch_peak_run_id: Optional[str],
) -> dict:
    """The record of a search that found nothing for an anchor
    (:data:`SEARCHED_EMPTY_KEY`).

    :param key: The search's key (:func:`search_key`).
    :param member: The representative it ran on.
    :param persistent: Whether the anchor was persistent, which admits its
        representative however faint.
    :param by_persistence: Whether persistence alone admitted it, so a search
        that leaves it out still writes its persistence record.
    """
    return {
        "key": key,
        "representative": representative_record(member),
        "persistent": bool(persistent),
        "by_persistence": bool(by_persistence),
        "batch_peak_run_id": batch_peak_run_id,
    }


def searched_in_vain(
    record: Optional[dict], key: Optional[str], member: Any, persistent: bool
) -> bool:
    """Whether a search would read an anchor exactly as the search that found
    nothing for it did, and so is not run again.

    It is, while the record's key is this search's - the same settings,
    library, chemistry, engine version and Mascope version - the anchor's representative is
    the member that search ran on, unchanged, and the anchor is persistent or
    not as it was then. Any of these broken, the anchor is searched again.
    With no key (a search under a cap,
    ``batch_untargeted.run_batch_untargeted_search``)
    nothing is left out.

    :param record: The anchor's record (:func:`searched_empty_record_of`).
    :param key: This search's key, or None.
    :param member: The anchor's representative now.
    :param persistent: Whether the anchor is persistent now.
    """
    return (
        record is not None
        and key is not None
        and record.get("key") == key
        and record.get("representative") == representative_record(member)
        and record.get("persistent") is bool(persistent)
    )


def found_nothing(
    key: str,
    by_sample: dict[str, list[Any]],
    read: set[str],
    *,
    vouched: Callable[[str], bool],
    persistent: dict[str, Any],
    admitted_by_sample: dict[str, set[str]],
    batch_peak_run_id: Optional[str],
) -> dict[str, dict]:
    """The records of the anchors a search found nothing for: searched in a
    sample whose search it vouches for, and read by no row in any sample, as
    a line of another anchor's ion included.

    :param by_sample: The representatives each searched sample was given.
    :param read: The anchors some sample's search reads
        (``batch_untargeted.search_readings``).
    :param vouched: Whether the search of a sample searched every
        representative it was given and stands as searched: not one the cap
        cut short, nor one whose search again failed.
    :return: Anchor id -> its record (:func:`searched_empty_record`).
    """
    return {
        member.batch_peak_id: searched_empty_record(
            key,
            member,
            persistent=member.batch_peak_id in persistent,
            by_persistence=member.sample_peak_id
            in admitted_by_sample.get(sample_item_id, set()),
            batch_peak_run_id=batch_peak_run_id,
        )
        for sample_item_id, sample_representatives in by_sample.items()
        if vouched(sample_item_id)
        for member in sample_representatives
        if member.batch_peak_id not in read
    }


async def write_search_records(
    sample_batch_id: str,
    *,
    key: Optional[str],
    persistent: dict[str, Any],
    searched_before: dict[str, Any],
    by_sample: dict[str, list[Any]],
    read: set[str],
    annotations: dict[str, Any],
    admitted_by_sample: dict[str, set[str]],
    vouched: Callable[[str], bool],
    batch_peak_run_id: Optional[str],
) -> tuple[set[str], int]:
    """Write the search's records on the anchors it searched or left out.

    The persistence record: an anchor persistence alone admitted takes its
    record, with the reading the search elected on it; every other anchor
    this search searched loses any it carried, since this search admitted it
    on its height or not at all. An anchor left out takes the record a search
    of it would write, as it was admitted then and electing nothing. An
    anchor the search did not reach - one that reads something already -
    keeps what it has: its record is the admission its reading was elected
    on.

    With a key, the record of each searched anchor it found nothing for
    (:func:`found_nothing`), and none on every other anchor it searched.

    :param persistent: The batch's persistent anchors, their occurrences
        (``batch_untargeted.Occurrence``).
    :param searched_before: The anchors left out (:func:`searched_in_vain`).
    :param by_sample: The representatives of each sample searched.
    :param read: The anchors some sample's search reads.
    :param annotations: Anchor id -> the reading the merge gave it.
    :param admitted_by_sample: Per sample, the representatives persistence
        alone admitted.
    :param vouched: Whether a sample's search stands for what it found
        nothing for (:func:`found_nothing`).
    :return: The anchors that carry a persistence record now, and how many
        of them this search searched.
    """
    records = {
        member.batch_peak_id: persistent[member.batch_peak_id].record(
            annotations.get(member.batch_peak_id), batch_peak_run_id
        )
        for sample_item_id, peaks in admitted_by_sample.items()
        for member in by_sample.get(sample_item_id, [])
        if member.sample_peak_id in peaks
    }
    left_out = {
        anchor_id: persistent[anchor_id].record(None, batch_peak_run_id)
        for anchor_id, anchor in searched_before.items()
        if (searched_empty_record_of(anchor) or {}).get("by_persistence")
    }
    searched = [
        member.batch_peak_id for members in by_sample.values() for member in members
    ]
    await write_anchor_records(
        sample_batch_id,
        PERSISTENCE_KEY,
        searched + list(searched_before),
        {**records, **left_out},
    )
    if key is not None:
        await write_anchor_records(
            sample_batch_id,
            SEARCHED_EMPTY_KEY,
            searched,
            found_nothing(
                key,
                by_sample,
                read,
                vouched=vouched,
                persistent=persistent,
                admitted_by_sample=admitted_by_sample,
                batch_peak_run_id=batch_peak_run_id,
            ),
        )
    return set(records) | set(left_out), len(records)


async def write_anchor_records(
    sample_batch_id: str, key: str, searched: Iterable[str], records: dict[str, dict]
) -> None:
    """Set ``key`` on each of ``searched`` that ``records`` holds, and take it
    off each that it does not, in one transaction under the fold lock."""
    ids = sorted(set(searched))
    if not ids:
        return
    async with async_session() as session:
        await _acquire_batch_fold_lock(session, sample_batch_id)
        anchors = (
            (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.batch_peak_id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        for anchor in anchors:
            provenance = dict(anchor.provenance or {})
            record = records.get(anchor.batch_peak_id)
            if record is None and key not in provenance:
                continue
            if record is None:
                provenance.pop(key, None)
            else:
                provenance[key] = record
            anchor.provenance = provenance
        await session.commit()


def searched_before_note(n: int) -> str:
    """The search's message about the anchors it left out, or nothing: said
    because these batch peaks were not searched, and a person who changed
    nothing the search reads would otherwise wonder why."""
    if not n:
        return ""
    many = n != 1
    return (
        f" {n} unassigned batch peak{'s' if many else ''} the last search found "
        f"nothing for {'were' if many else 'was'} not searched again: nothing "
        f"that search read of {'them' if many else 'it'} has changed."
    )
