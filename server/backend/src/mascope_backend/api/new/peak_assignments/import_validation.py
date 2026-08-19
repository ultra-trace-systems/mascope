"""
Payload rules for imported assignment runs, as pure functions.

An imported ledger is data a workspace editor asserts, not a computation this
server performed. The bar these checks hold it to: reject what would corrupt the
read model or forge a server judgement, accept what is merely the importer's
judgement. Everything here is decided from the payload alone - no database, no
peak file - so the rules are testable in isolation and the caller in
``import_service`` is left with the parts that genuinely need I/O.

Each function returns an error message (or a list of them) rather than raising,
so the caller decides the status code and can report several problems from one
pass over a chunk.
"""

import json
from typing import Any, Iterable, Sequence

from mascope_backend.api.new.peak_assignments.config import (
    MAX_IMPORT_JSON_BYTES,
    RESERVED_ENGINE_NAMES,
)
from mascope_backend.api.new.peak_assignments.engine import tier_for_score


# Provenance keys the server derives its own judgement from, stripped out of an
# imported row. The ledger renders a calibrated P(correct) with its provisional
# marker and an adduct corroboration count; ``_provenance_scalars`` reads
# exactly these keys to do it, and the batch consensus reads ``p_correct`` too.
# An importer's confidence belongs in ``fit_score`` and in the rest of the
# provenance blob the inspector shows - not in a column the UI presents as this
# server's calibrated probability, on a run that may have disclosed no
# calibration at all. Dropped rather than rejected: they are plausible keys in
# an external engine's own provenance, so a payload carrying them is not
# malformed, it is just not authoritative about them.
SERVER_OWNED_PROVENANCE_KEYS = ("p_correct", "calibration", "corroboration")


def normalize_engine(engine: str) -> tuple[str | None, str | None]:
    """Validate a client-supplied engine name.

    :param engine: The engine name from the payload.
    :return: ``(normalized_name, None)`` when acceptable, else ``(None, error)``.
    """
    name = (engine or "").strip()
    if not name:
        return None, "engine must be a non-empty name"
    if name.lower() in RESERVED_ENGINE_NAMES:
        return None, (
            f"engine '{name}' is reserved for runs this server computed; "
            "an imported run must name the engine that produced it"
        )
    return name, None


def json_size_error(field: str, value: Any) -> str | None:
    """Reject an opaque JSON field above the byte ceiling.

    :param field: Field name, for the message.
    :param value: The decoded JSON value.
    :return: An error message, or None when within the cap.
    """
    if value is None:
        return None
    size = len(json.dumps(value, default=str).encode("utf-8"))
    if size > MAX_IMPORT_JSON_BYTES:
        return (
            f"{field} is {size} bytes, above the {MAX_IMPORT_JSON_BYTES}-byte "
            f"limit; it is re-served on every run listing, so it is capped"
        )
    return None


def coherent_tiers(
    fit_score: float | None, identified: float, candidate: float
) -> set[str]:
    """The tiers a fit score may carry under the declared bands.

    Delegates to :func:`engine.tier_for_score`, the in-app engine's own
    thresholding, rather than restating it: an import that reproduces Mascope's
    tiering must not be refused for doing so. Restating it is what made an
    earlier version reject two shapes the in-app engine itself writes - a
    ``None`` fit score (``tier_for_score`` maps that to 'below_assignability',
    not 'unassigned') and a zero score under a zero ``candidate`` band (it is
    'below_assignability' there too, on the ``score <= 0`` guard).

    A missing fit score admits two tiers, because the in-app ledger uses both:
    a peak nothing was assigned to is written 'unassigned', while an assigned
    row whose score came back non-finite is written 'below_assignability'.
    Neither is a claim about confidence, so neither is worth refusing.

    :param fit_score: The row's fit score, or None when nothing was assigned.
    :param identified: Fit score at or above which a row is 'identified'.
    :param candidate: Fit score at or above which a row is 'candidate'.
    :return: The tier names coherent with this score.
    """
    if fit_score is None:
        return {"unassigned", "below_assignability"}
    return {
        tier_for_score(
            fit_score, possible_threshold=candidate, probable_threshold=identified
        )
    }


def tier_coherence_error(
    tier: str, fit_score: float | None, identified: float, candidate: float
) -> str | None:
    """Check one row's tier against its fit score under the run's bands.

    A shared fit-score scale does not make shared *bands* automatic: the in-app
    tiers come from thresholding a run-config pair, not from engine constants.
    Without this check an engine tiering at 0.6/0.3 publishes 'identified' rows
    at 0.62 that sort, filter and roll up beside in-app 'identified' rows
    meaning something considerably stricter - and outrank them in the
    cross-sample tier ranking.

    :param tier: The tier the row claims.
    :param fit_score: The row's fit score.
    :param identified: The run's identified threshold.
    :param candidate: The run's candidate threshold.
    :return: An error message, or None when coherent.
    """
    expected = coherent_tiers(fit_score, identified, candidate)
    if tier in expected:
        return None
    named = " or ".join(f"'{name}'" for name in sorted(expected))
    if fit_score is None:
        return f"tier '{tier}' with no fit_score: such a row is {named}"
    return (
        f"tier '{tier}' is incoherent with fit_score {fit_score} under the "
        f"declared tier_bands (identified >= {identified}, candidate >= "
        f"{candidate}), which put it at {named}"
    )


def strip_server_owned_provenance(provenance: dict | None) -> dict | None:
    """Drop the provenance keys the server presents as its own judgement.

    :param provenance: The row's provenance blob as supplied.
    :return: The blob without the server-owned keys, or None when nothing is
        left to store.
    """
    if not provenance:
        return None
    kept = {
        key: value
        for key, value in provenance.items()
        if key not in SERVER_OWNED_PROVENANCE_KEYS
    }
    return kept or None


def duplicate_peak_ids(sample_peak_ids: Iterable[str]) -> list[str]:
    """Find peaks claimed more than once within one chunk.

    At most one row per peak is the invariant every consumer of the ledger
    assumes, and the unique constraint on (run, peak) backstops it at insert.
    Catching it here reports which peak is at fault instead of surfacing a
    constraint name.

    :param sample_peak_ids: The chunk's peak ids in payload order.
    :return: The duplicated ids, in first-seen order.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for peak_id in sample_peak_ids:
        if peak_id in seen and peak_id not in duplicates:
            duplicates.append(peak_id)
        seen.add(peak_id)
    return duplicates


def unknown_peak_ids(
    sample_peak_ids: Iterable[str], known_peak_ids: set[str], limit: int = 5
) -> list[str]:
    """Find rows referring to peaks the sample does not have.

    :param sample_peak_ids: The chunk's peak ids.
    :param known_peak_ids: Every peak id the sample's peak file holds.
    :param limit: Most ids to collect, so an entirely wrong payload reports a
        readable message rather than every row.
    :return: Up to ``limit`` unknown ids, in first-seen order.
    """
    unknown: list[str] = []
    for peak_id in sample_peak_ids:
        if peak_id not in known_peak_ids and peak_id not in unknown:
            unknown.append(peak_id)
            if len(unknown) >= limit:
                break
    return unknown


def self_owner_errors(rows: Sequence[Any]) -> list[str]:
    """Reject rows that name themselves as their own owner.

    Owner resolution matches ``owner_sample_peak_id`` to another row's
    ``sample_peak_id`` within the run, so a self-reference would resolve
    happily and produce a row that owns itself.

    :param rows: Rows carrying ``sample_peak_id`` and ``owner_sample_peak_id``.
    :return: One message per offending row.
    """
    return [
        f"row for peak '{row.sample_peak_id}' names itself as its owner"
        for row in rows
        if row.owner_sample_peak_id is not None
        and row.owner_sample_peak_id == row.sample_peak_id
    ]


def chunk_offset_error(index: int, staged_rows: int, chunk_rows: int) -> str | None:
    """Check a chunk's offset against what the server has already staged.

    ``chunk.index`` is an offset in rows, not a sequence number, which is what
    makes a retry safe: the SDK's HTTP layer retries a POST on a timeout with no
    way to opt out, so a chunk the server already applied *will* arrive twice.
    Re-sending the chunk that produced the current row count is recognised as
    that replay and answered as a no-op; a gap or a deeper rewind is refused so
    the client resynchronises from the row count the server reports rather than
    silently uploading a ledger with a hole in it.

    :param index: The chunk's declared row offset.
    :param staged_rows: Rows the run already holds.
    :param chunk_rows: Rows in this chunk.
    :return: An error message when the offset cannot be honoured, else None.
        A replay is not an error - use :func:`is_chunk_replay` to detect it.
    """
    if index == staged_rows:
        return None
    if is_chunk_replay(index, staged_rows, chunk_rows):
        return None
    if index > staged_rows:
        return (
            f"chunk index {index} leaves a gap: the run holds {staged_rows} "
            f"row(s), so the next chunk starts at {staged_rows}"
        )
    return (
        f"chunk index {index} rewinds past applied rows: the run holds "
        f"{staged_rows} row(s), so the next chunk starts at {staged_rows}"
    )


def is_chunk_replay(index: int, staged_rows: int, chunk_rows: int) -> bool:
    """Whether this chunk is the one that produced the current row count.

    Only the immediately preceding chunk counts as a replay. A deeper rewind is
    a client that lost track of where it was, and answering it as a no-op would
    silently drop the rows it thinks it is sending.

    :param index: The chunk's declared row offset.
    :param staged_rows: Rows the run already holds.
    :param chunk_rows: Rows in this chunk.
    :return: True when the chunk has already been applied in full.
    """
    return index < staged_rows and index + chunk_rows == staged_rows


def unresolved_owner_error(unresolved: Sequence[str], limit: int = 5) -> str:
    """Message for owner references that match no row in the finished import.

    :param unresolved: The offending ``owner_sample_peak_id`` values.
    :param limit: Most values to name in the message.
    :return: The error message.
    """
    shown = ", ".join(f"'{value}'" for value in list(unresolved)[:limit])
    more = "" if len(unresolved) <= limit else f" (and {len(unresolved) - limit} more)"
    return (
        f"owner_sample_peak_id {shown}{more} matches no row in this import; "
        "an owner reference must name a peak the import itself assigns"
    )
