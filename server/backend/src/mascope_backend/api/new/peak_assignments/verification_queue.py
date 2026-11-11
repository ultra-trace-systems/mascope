"""The verification queue: the assignments of a run worth a person's verdict next.

Verdicts are the labels the confidence calibration is refit from
(``docs/dev/verification_calibration_loop.md``), and the curve is least
constrained where the tier bands cut it: a row far above the upper band or far
below the lower one is a verdict anybody could predict, and one more label
there teaches the fit nothing. Guardrail 3 of that note asks the app to put
the rows near the band edges in front of the user instead, with random spot
checks for the blind spots a ranking cannot see (MAS-329). The queue is that
list, for one run:

- **stale** rows first: a live verdict this run read differently. The verdict
  was recorded before the run, and the run either commits another formula or
  mechanism on the judged peak (``reassigned``) or the same one at another
  evidence (``rescored``). The verdict is not invalidated - it is pinned to
  the score the user judged, and stays a label at that score until the row is
  judged again - and the row is offered for a verdict on what the run reads
  now. A new verdict on a rescored row is on the same identity, so it
  supersedes the old one; on a reassigned row it judges another identity, and
  the old verdict stays;
- **band-edge** rows: no live verdict, evidence within :data:`EDGE_WINDOW` of
  a band edge, ranked by how close they sit to it and by how few labels the
  instrument and profile hold near that evidence already;
- **spot checks**: a seeded random draw from the rows outside the window,
  every :data:`SPOT_CHECK_EVERY`-th place. Each row's place in the draw is a
  hash of the run and the row, so a verdict on one spot check leaves the
  others where they were.

The queue never carries ``p_correct`` (guardrail 5): a verdict is a judgment
of the data, and the calibrated number beside it would be its anchor. Nor the
evidence: it is ``p_correct``'s input, and the tier the ledger shows says
enough. Each entry says why it is there instead.

Pure: no database, no I/O. ``service.get_verification_queue`` gathers the
rows, the live verdicts and the label pool and calls :func:`rank_queue`.
"""

from __future__ import annotations

import bisect
import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ARTIFACT,
    ROLE_ISO_CHILD,
    ROLE_REAGENT,
    ROLE_UNASSIGNED,
)
from mascope_tools.composition.calibration import MIN_CALIBRATION_CLASS_LABELS


#: How far from a band edge, in evidence, a row is still "near" it. The bands
#: sit at 0.45 and 0.75 by default, so the window covers 0.35-0.55 and
#: 0.65-0.85: the evidence where a Platt curve fit on the instrument's labels
#: moves most between neighbouring fits.
EDGE_WINDOW = 0.1

#: How far either side of a row's evidence a label counts as "near" it.
LABEL_BANDWIDTH = 0.05

#: The number of nearby labels that halves a row's rank. The fit's own
#: per-class floor: past it a region is held by labels, short of it not yet.
LABEL_SATURATION = MIN_CALIBRATION_CLASS_LABELS

#: Every this-many-th place of the band-edge part is a spot check.
SPOT_CHECK_EVERY = 5

#: The fewest spot checks a queue offers when it has rows to draw them from,
#: so a run with few band-edge rows still carries some.
MIN_SPOT_CHECKS = 3

#: Evidence that moved by less than this is the same evidence: a re-run of
#: the same engine on the same peaks writes the same float, and a snapshot
#: makes one round trip through the database.
RESCORE_TOLERANCE = 1e-6

REASON_STALE = "stale"
REASON_BAND_EDGE = "band_edge"
REASON_SPOT_CHECK = "spot_check"

STALE_REASSIGNED = "reassigned"
STALE_RESCORED = "rescored"

#: Rows that are not an assignment of a formula of the sample: a line of
#: another row's pattern, a source ion, a ringing lobe, a peak nothing
#: explained. None of them takes a verdict in the ledger.
_NOT_QUEUEABLE_ROLES = frozenset(
    {ROLE_ISO_CHILD, ROLE_REAGENT, ROLE_ARTIFACT, ROLE_UNASSIGNED}
)


@dataclass(frozen=True)
class QueueEntry:
    """One row of the queue.

    :param peak_assignment_id: The run's row.
    :param sample_peak_id: Its peak.
    :param reason: ``stale``, ``band_edge`` or ``spot_check``.
    :param near_band: The tier whose band edge the row sits nearest to
        (``assigned`` or ``candidate``), or None where the run states no bands.
    :param labels_near: The labels the instrument and profile hold within
        :data:`LABEL_BANDWIDTH` of the row's evidence.
    :param stale: For a stale row, the verdict the run read differently:
        ``kind`` (``reassigned`` or ``rescored``), ``verdict``, the judged
        ``assigned_formula`` and ``ionization_mechanism_id``,
        ``assignment_verification_id`` and ``verified_utc``. None otherwise.
    """

    peak_assignment_id: str
    sample_peak_id: str
    reason: str
    near_band: str | None
    labels_near: int
    stale: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def queueable(row: Mapping[str, Any]) -> bool:
    """Whether a ledger row can take a place in the queue.

    An analyte row with a formula and a finite evidence: what a verdict judges
    and what the calibration reads as its score.
    """
    evidence = row.get("evidence")
    return (
        bool(row.get("assigned_formula"))
        and row.get("role") not in _NOT_QUEUEABLE_ROLES
        and isinstance(evidence, (int, float))
        and math.isfinite(evidence)
    )


def nearest_edge(
    evidence: float, bands: Mapping[str, float] | None
) -> tuple[str, float] | None:
    """The band edge nearest a row's evidence, and how far it is.

    :param evidence: The row's evidence.
    :param bands: The run's ``tier_bands``: tier -> the evidence at or above
        which a row earns it. Entries that are not numbers are skipped.
    :return: ``(tier, distance)``, the higher band on a tie, or None where the
        run states no bands.
    """
    edges = sorted(
        (
            (float(threshold), tier)
            for tier, threshold in (bands or {}).items()
            if isinstance(threshold, (int, float)) and math.isfinite(threshold)
        ),
        reverse=True,
    )
    if not edges:
        return None
    threshold, tier = min(edges, key=lambda edge: abs(evidence - edge[0]))
    return tier, abs(evidence - threshold)


def labels_near(
    evidence: float, label_evidence: Sequence[float], bandwidth: float
) -> int:
    """How many labels sit within ``bandwidth`` of ``evidence``.

    :param evidence: The row's evidence.
    :param label_evidence: The label pool's evidence snapshots, sorted.
    :param bandwidth: Half the width of the window.
    """
    return bisect.bisect_right(
        label_evidence, evidence + bandwidth
    ) - bisect.bisect_left(label_evidence, evidence - bandwidth)


def edge_priority(distance: float, n_near: int, window: float = EDGE_WINDOW) -> float:
    """A band-edge row's rank: higher is asked first.

    ``(1 - distance / window) / (1 + n_near / LABEL_SATURATION)``: a row on the
    edge itself with no label near it ranks 1; moving to the window's rim, or
    gathering :data:`LABEL_SATURATION` labels nearby, each halve-or-more it.
    """
    closeness = max(0.0, 1.0 - distance / window)
    return closeness / (1.0 + n_near / LABEL_SATURATION)


def _identity(record: Mapping[str, Any]) -> tuple:
    return (
        str(record.get("sample_peak_id")),
        record.get("assigned_formula"),
        record.get("ionization_mechanism_id"),
    )


def _before(verified_utc: datetime | None, run_created: datetime | None) -> bool:
    """Whether a verdict was recorded before the run was made: only then can
    the run have changed what the verdict judged."""
    if verified_utc is None or run_created is None:
        return False
    return verified_utc < run_created


def stale_mark(
    row: Mapping[str, Any],
    verdicts_on_peak: Iterable[Mapping[str, Any]],
    *,
    run_created: datetime | None,
) -> dict | None | bool:
    """What the live verdicts on a row's peak say about the row.

    :param row: A queueable row of the run.
    :param verdicts_on_peak: The sample's live verdicts on the row's peak.
    :param run_created: When the row's run was made. A verdict recorded since
        judged what the run reads - a verdict on one of its rows always is -
        so only one recorded before it can be stale.
    :return: ``True`` when a live verdict judges the row as the run reads it
        (the row needs no verdict), a stale mark (a dict, :class:`QueueEntry`'s
        ``stale``) when a verdict made before the run judged something the run
        changed, or ``None`` when no verdict reaches the row.
    """
    identity = _identity(row)
    earlier: list[Mapping[str, Any]] = []
    for verdict in verdicts_on_peak:
        current = not _before(verdict.get("verified_utc"), run_created)
        if _identity(verdict) == identity:
            snapshot = verdict.get("evidence")
            rescored = (
                not current
                and isinstance(snapshot, (int, float))
                and abs(float(snapshot) - float(row["evidence"])) > RESCORE_TOLERANCE
            )
            if not rescored:
                return True
            earlier.append({**verdict, "_kind": STALE_RESCORED})
        elif not current:
            earlier.append({**verdict, "_kind": STALE_REASSIGNED})
    if not earlier:
        return None
    # A verdict on the row's own identity outranks one on a formula the run no
    # longer commits; among equals, the newest.
    latest = max(
        earlier,
        key=lambda verdict: (
            verdict["_kind"] == STALE_RESCORED,
            verdict.get("verified_utc") or datetime.min,
        ),
    )
    return {
        "kind": latest["_kind"],
        "verdict": latest.get("verdict"),
        "assigned_formula": latest.get("assigned_formula"),
        "ionization_mechanism_id": latest.get("ionization_mechanism_id"),
        "assignment_verification_id": latest.get("assignment_verification_id"),
        "verified_utc": latest.get("verified_utc"),
    }


def spot_check_key(seed: str, peak_assignment_id: str) -> str:
    """A row's place in the spot checks' draw: stable for the row within one
    seed, whatever else the draw holds."""
    return hashlib.sha256(f"{seed}:{peak_assignment_id}".encode()).hexdigest()


def _interleave(edge: list, spots: list, every: int) -> list:
    """The band-edge rows with a spot check at every ``every``-th place, and
    whatever is left of either after the other runs out."""
    out: list = []
    edge_iter, spot_iter = iter(edge), iter(spots)
    remaining_edge, remaining_spots = len(edge), len(spots)
    while remaining_edge or remaining_spots:
        if remaining_spots and (not remaining_edge or (len(out) + 1) % every == 0):
            out.append(next(spot_iter))
            remaining_spots -= 1
        else:
            out.append(next(edge_iter))
            remaining_edge -= 1
    return out


def rank_queue(
    rows: Iterable[Mapping[str, Any]],
    *,
    bands: Mapping[str, float] | None,
    label_evidence: Iterable[float],
    verdicts: Iterable[Mapping[str, Any]],
    run_created: datetime | None,
    seed: str,
    window: float = EDGE_WINDOW,
    bandwidth: float = LABEL_BANDWIDTH,
    spot_check_every: int = SPOT_CHECK_EVERY,
    min_spot_checks: int = MIN_SPOT_CHECKS,
) -> list[QueueEntry]:
    """The run's verification queue, in the order it is offered.

    :param rows: The run's ledger rows: ``peak_assignment_id``,
        ``sample_peak_id``, ``assigned_formula``, ``ionization_mechanism_id``,
        ``role`` and ``evidence``. Rows that are not :func:`queueable` are
        skipped.
    :param bands: The run's ``tier_bands``.
    :param label_evidence: The evidence snapshots of the labels the instrument
        and profile hold (the calibration's pool).
    :param verdicts: The sample's live verdicts, as recorded:
        ``sample_peak_id``, ``assigned_formula``, ``ionization_mechanism_id``,
        ``verdict``, ``evidence``, ``assignment_verification_id`` and
        ``verified_utc``.
    :param run_created: When the run was made.
    :param seed: Seeds the spot checks' draw, so one run's queue reads the same
        on every load (the run id). The draw takes the rows outside the window
        with the lowest :func:`spot_check_key`: a verdict removes only its own
        row, and a change in the number drawn moves only the end of it.
    :return: Stale rows first, then the band-edge rows with the spot checks
        interleaved.
    """
    pool = sorted(
        float(e) for e in label_evidence if e is not None and math.isfinite(e)
    )
    by_peak: dict[str, list[Mapping[str, Any]]] = {}
    for verdict in verdicts:
        by_peak.setdefault(str(verdict.get("sample_peak_id")), []).append(verdict)

    stale: list[tuple[float, str, QueueEntry]] = []
    edge: list[tuple[float, float, str, QueueEntry]] = []
    outside: list[QueueEntry] = []
    for row in rows:
        if not queueable(row):
            continue
        evidence = float(row["evidence"])
        mark = stale_mark(
            row,
            by_peak.get(str(row.get("sample_peak_id")), ()),
            run_created=run_created,
        )
        if mark is True:
            continue
        nearest = nearest_edge(evidence, bands)
        tier, distance = nearest if nearest else (None, math.inf)
        n_near = labels_near(evidence, pool, bandwidth)
        peak = str(row.get("sample_peak_id"))

        def entry(reason: str, stale_record: dict | None = None) -> QueueEntry:
            return QueueEntry(
                peak_assignment_id=str(row["peak_assignment_id"]),
                sample_peak_id=peak,
                reason=reason,
                near_band=tier,
                labels_near=n_near,
                stale=stale_record,
            )

        if mark is not None:
            stale.append((distance, peak, entry(REASON_STALE, mark)))
        elif distance <= window:
            priority = edge_priority(distance, n_near, window)
            edge.append((-priority, distance, peak, entry(REASON_BAND_EDGE)))
        else:
            outside.append(entry(REASON_SPOT_CHECK))

    stale.sort(key=lambda item: item[:2])
    edge.sort(key=lambda item: item[:3])
    outside.sort(key=lambda item: spot_check_key(seed, item.peak_assignment_id))
    n_spots = min(
        len(outside),
        max(min_spot_checks, math.ceil(len(edge) / max(1, spot_check_every - 1))),
    )
    spots = outside[:n_spots]
    return [item[-1] for item in stale] + _interleave(
        [item[-1] for item in edge], spots, spot_check_every
    )
