"""A claim's rows on the verification tables, as the writes and the reads meet them.

What both verification services need (``verification.py`` per sample,
``batch_peak_verification.py`` per batch peak; the claims themselves are
``claims.py``):

- recording a verdict supersedes the live one on its identity, which a
  rejection on a reference standard reads first, to record the confirmation
  it refutes (:func:`supersede_live`);
- what a claim records beside its verdict: the structure or class named, the
  level, its letter, a level-1 batch claim's window, and in ``context`` the
  mapping's version and the statement (:func:`stated_by`,
  :func:`claim_columns`), and what a borrowed row carries of it
  (:func:`claim_fields`);
- a batch-level claim reaches one sample with its structure and its level only
  where its window covers the sample's acquisition (:func:`for_sample`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, update

from mascope_backend.api.new.peak_assignments.claims import (
    Statement,
    claim_level,
    level_reaches,
    mapping_context,
    refutation,
)


async def supersede_live(
    session,
    model: Any,
    conditions: tuple,
    *,
    verdict: str,
    evidence_level: Optional[str],
    now: datetime,
) -> Optional[dict]:
    """Stamp the live verdict on an identity superseded, and say what it refuted.

    :param session: An open session, inside the write's lock.
    :param model: The verification table's model.
    :param conditions: The identity's live-row conditions.
    :param verdict: The new verdict.
    :param evidence_level: Its basis.
    :param now: The new verdict's time, stamped on the old one.
    :return: The confirmation a rejection on a reference standard refutes
        (``claims.refutation``), read before it is stamped; else None.
    """
    live = await session.scalar(select(model).where(*conditions))
    id_field = f"{model.__tablename__}_id"
    refutes = refutation(verdict, evidence_level, live, id_field)
    await session.execute(update(model).where(*conditions).values(superseded_utc=now))
    return refutes


def _said(identification: Any, field: str) -> Any:
    return getattr(identification, field, None) if identification is not None else None


def stated_by(
    verdict: str, evidence_level: Optional[str], identification: Any
) -> Optional[Statement]:
    """What a verdict states, from the claim its body carries.

    :param verdict: confirmed | rejected | unsure.
    :param evidence_level: Its basis.
    :param identification: The body's claim fields
        (``claim_schemas.ClaimFields``), or None.
    :return: ``claims.claim_level``'s statement, or None.
    """
    return claim_level(
        verdict,
        evidence_level,
        _said(identification, "claimed_inchikey"),
        _said(identification, "evidence_detail"),
        _said(identification, "independent_line"),
    )


def claim_columns(
    identification: Any,
    stated: Optional[Statement],
    refutes: Optional[dict],
    context: Optional[dict] = None,
) -> dict:
    """The claim's columns of a verdict row: the structure or class named,
    ``level`` and ``level_letter``, a batch body's window, and ``context`` with
    the mapping's version, the statement and a refutation.

    :param identification: The body's claim fields, or None.
    :param stated: What the verdict states (:func:`stated_by`), or None.
    :param refutes: What a rejection refutes (:func:`supersede_live`).
    :param context: What else the row's context records.
    :return: The columns' values.
    """
    columns = {
        "claimed_inchikey": _said(identification, "claimed_inchikey"),
        "claimed_name": _said(identification, "claimed_name"),
        "level": stated.level if stated else None,
        "level_letter": stated.letter if stated else None,
        "context": mapping_context(
            context,
            stated=stated,
            evidence_detail=_said(identification, "evidence_detail"),
            independent_line=_said(identification, "independent_line"),
            refutes=refutes,
        ),
    }
    for field in ("window_start_utc", "window_end_utc"):
        if hasattr(identification, field):
            columns[field] = getattr(identification, field)
    return columns


def claim_fields(record: Any) -> dict:
    """What a borrowed row carries of a batch-level verdict's claim.

    :param record: A ``BatchPeakVerification``.
    :return: The structure or class named, the level, its letter and the window.
    """
    return {
        field: getattr(record, field)
        for field in (
            "claimed_inchikey",
            "claimed_name",
            "level",
            "level_letter",
            "window_start_utc",
            "window_end_utc",
        )
    }


def for_sample(record: dict, acquired_utc: Optional[datetime]) -> dict:
    """A batch-level verdict as it reaches one sample: the structure it names
    and its level where its window covers the sample's acquisition, else
    neither, with ``outside_window`` saying why, so the sample's row reads the
    engine's level and names no structure. The verdict on the formula, its
    evidence and the window still reach the row.

    :param record: The verdict's record.
    :param acquired_utc: The sample's acquisition time, or None.
    :return: A new dict.
    """
    if level_reaches(record, acquired_utc):
        return {**record, "outside_window": False}
    return {
        **record,
        "claimed_inchikey": None,
        "claimed_name": None,
        "level": None,
        "level_letter": None,
        "outside_window": True,
    }
