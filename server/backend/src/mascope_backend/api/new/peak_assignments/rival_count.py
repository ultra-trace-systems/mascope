"""A count of the rivals a row's set leaves out, behind an option off by default.

A committed row's rival set (:mod:`rival_set`) measures its members at the
model the run's caps read, but which candidates are members is the formula
search's to say, at the model it ranked at: it names the ones its ranking
could not separate from the reading (``finder.CANDIDATE_RIVALS``,
``finder.ReadingRivals.rivals``). Where the run takes its refitted model and
that moves the centre or the width, a candidate the search separated could
tie the reading there, and no density, confidence or level would see it: the
set can lose a rival at the refitted model, never gain one (#3497).

With the run option ``count_scored_rivals`` on, the search hands over every
candidate it scored for a committed reading (``finder.SCORED_RIVALS``,
``finder.ReadingRivals.scored``), each with how far it had the reading in tie
gaps (``arbitration.tie_gaps``). Once the run is judged, :func:`count` measures
the ones the run did not seed already, in a pass of its own at the model the
frames were first scored at, and reads them, as the set reads its members, at
the model the caps read (``rival_set.RivalFrames``). It counts, for the whole
run, the candidates outside today's membership that tie the reading there by
``arbitration``'s one tie rule, and what they would do to the density. The
pass feeds nothing: the rows are read, never written, and only the run's
search scope records the count (:data:`SCORED_RIVALS_KEY`). No candidate is
added to a set.
"""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.peak_assignments.engine import formula_identity
from mascope_backend.api.new.peak_assignments.rival_set import (
    RIVAL_SET,
    RivalFrames,
    SeededFrame,
    _fit_tied,
    _own_reading,
    _rivals,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_M0,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
)
from mascope_backend.api.new.peak_assignments.seeded_scoring import score_seeds
from mascope_backend.api.new.peak_assignments.tiering import (
    DENSITY_LIMIT,
    density_reason,
)
from mascope_backend.api.new.peak_assignments.untargeted_rows import (
    _resolve_peak_positions,
    _seed_of,
    untargeted_seeds,
)
from mascope_backend.runtime import runtime
from mascope_tools.composition.arbitration import CANDIDATE_DENSITY, unseparated
from mascope_tools.composition.finder import SCORED_RIVALS
from mascope_tools.composition.measurement_model import MeasurementModel


#: The search scope's record of the count.
SCORED_RIVALS_KEY = "scored_rivals"

#: A committed reading a list proposed, whose grid candidates the search
#: scored beside it (``finder.ReadingRivals.scored``).
KIND_LIST = "list"

#: A committed reading the formula search found (``finder.SCORED_RIVALS``).
KIND_SEARCH = "search"

#: How far the search had a candidate from the reading, in tie gaps, as the
#: count bins it: ``(name, from, to)``, from exclusive, to inclusive.
#: ``within_1`` is a candidate the search could not separate either, which
#: today's set does not hold all the same: its line was not measured on the
#: peak at the run's model.
GAP_BANDS: tuple[tuple[str, float, float], ...] = (
    ("within_1", -math.inf, 1.0),
    ("1_to_2", 1.0, 2.0),
    ("2_to_3", 2.0, 3.0),
    ("3_to_5", 3.0, 5.0),
    ("over_5", 5.0, math.inf),
)


def gap_band(gaps: float) -> str:
    """The band of :data:`GAP_BANDS` a distance in tie gaps falls in."""
    return next(name for name, low, high in GAP_BANDS if low < gaps <= high)


@dataclass(frozen=True)
class Handed:
    """A candidate the search scored for a committed reading.

    :param seed: Its ``(formula, mechanism id)``, as the run seeds a reading.
    :param key: Its neutral's identity (``engine.formula_identity``).
    :param gaps: How far the search had it from the reading, in tie gaps.
    """

    seed: tuple[str, str]
    key: str
    gaps: float


def handed_by_peak(
    matches: pd.DataFrame | None,
    frame: pd.DataFrame,
    *,
    mechanism_id_by_notation: Mapping[str, str],
    formula_formatter=None,
    list_scored: Mapping[str, Iterable[dict]] | None = None,
) -> dict[tuple[str, str], list[Handed]]:
    """The candidates the search handed over, by the reading they were scored for.

    :param matches: The search's result, its committed readings carrying
        ``finder.SCORED_RIVALS``.
    :param frame: The frame the search was handed, with ``sample_peak_id``.
    :param mechanism_id_by_notation: The searched channels, by notation.
    :param formula_formatter: How a formula is stored.
    :param list_scored: Each kept list reading's scored grid candidates, by
        its peak (``engine.ListElection.scored_by_peak``).
    :return: ``(peak id, kind)`` -> the candidates, :data:`KIND_SEARCH` for a
        formula the search found and :data:`KIND_LIST` for a list's reading.
    """
    format_formula = formula_formatter or (lambda formula: formula)
    mechanisms = dict(mechanism_id_by_notation)
    handed: dict[tuple[str, str], list[Handed]] = {}

    def hand(peak: str, kind: str, scored: Iterable[dict]) -> None:
        out = handed.setdefault((peak, kind), [])
        for candidate in scored:
            seed = _seed_of(candidate, mechanisms, format_formula)
            if seed is not None:
                out.append(
                    Handed(
                        seed, formula_identity(seed[0]), float(candidate["tie_gaps"])
                    )
                )

    if matches is not None and not matches.empty and SCORED_RIVALS in matches.columns:
        rows = matches[
            matches[SCORED_RIVALS].map(lambda value: isinstance(value, list))
        ]
        positions = _resolve_peak_positions(frame, rows["mz"].astype(float).tolist())
        peak_ids = frame["sample_peak_id"].astype(str).to_numpy()
        for scored, position in zip(rows[SCORED_RIVALS], positions, strict=True):
            if position is not None:
                hand(str(peak_ids[position]), KIND_SEARCH, scored)
    for peak, scored in (list_scored or {}).items():
        hand(str(peak), KIND_LIST, scored)
    return handed


def _empty_kind() -> dict:
    return {
        "readings": 0,
        "scored": 0,
        "held": 0,
        "outside": 0,
        "not_placed": 0,
        "ties": {name: 0 for name, _, _ in GAP_BANDS},
    }


def _outside(
    candidates: list[Handed],
    held_keys: set[str],
    held_ions: set,
    placed: list,
    counts: dict,
) -> dict[Any, Handed]:
    """A reading's handed candidates today's set does not hold, with their
    lines as the extra pass placed them on the peak, counting each candidate
    ``held`` (its neutral or its ion is in the set), ``outside`` or
    ``not_placed`` into ``counts``.

    :return: Each placed reading of an outside candidate -> the candidate it
        was handed over as: the one of its channel, else the first of its
        neutral.
    """
    outside: dict[Any, Handed] = {}
    for candidate in candidates:
        # The extra pass holds a candidate's own line alone, on the peaks it
        # was handed over for (``rival_set.SeededFrame.placed_for``).
        readings = [r for r in placed if r.key == candidate.key]
        if candidate.key in held_keys or any(r.ion in held_ions for r in readings):
            # The set holds its neutral, or its ion read another way.
            counts["held"] += 1
            continue
        if not readings:
            counts["not_placed"] += 1
            continue
        counts["outside"] += 1
        for reading in readings:
            if reading not in outside or reading.mechanism_id == candidate.seed[1]:
                outside[reading] = candidate
    return outside


def search_config(ctx: Any, notations):
    """The profile's search for a run (``pipeline._propose_by_search``),
    handing over every candidate it scores where the run counts them."""
    config = ctx.resolved_profile.search_config(notations)
    if not ctx.config.count_scored_rivals:
        return config
    return replace(config, scored_rivals=True)


def tally(
    rows: list[dict],
    handed: Mapping[tuple[str, str], list[Handed]],
    frames: RivalFrames,
    extra: RivalFrames,
    model: MeasurementModel,
) -> dict:
    """Count, over a run's committed rows, what the handed candidates would do
    to their sets at the caps' model. Reads the rows and writes none.

    A handed candidate is ``held`` where today's set holds its neutral (a
    rival, a fit-tied one, or the row's own reading) or its ion, ``outside``
    where the extra pass placed its monoisotopic line on the peak, and
    ``not_placed`` where it did not. The outside ones join today's set as a
    seeded set would take them, each ion once at its best evidence
    (``rival_set._rivals``), and are arbitrated with it by ``arbitration``'s
    one tie rule; a neutral counted there ties the reading once, binned by how
    far the search had it (:data:`GAP_BANDS`).

    :param rows: The run's judged rows.
    :param handed: The candidates handed over (:func:`handed_by_peak`).
    :param frames: The frames the run measured its sets in.
    :param extra: The extra pass's frame, read the same way.
    :param model: The model the caps read.
    :return: The record, less the seconds and the models.
    """
    today = frames.readings_at(model)
    placed = extra.readings_at(model)
    by_kind = {KIND_LIST: _empty_kind(), KIND_SEARCH: _empty_kind()}
    rises = 0
    reach: dict[str, int] = {}
    would_cap = 0
    for row in rows:
        kind = {SOURCE_DATABASE: KIND_LIST, SOURCE_UNTARGETED: KIND_SEARCH}.get(
            row.get("source")
        )
        provenance = row.get("provenance") or {}
        if row.get("role") != ROLE_M0 or kind is None or RIVAL_SET not in provenance:
            continue
        peak = str(row.get("sample_peak_id"))
        candidates = handed.get((peak, kind))
        if not candidates:
            continue
        named = today.get(peak, [])
        on_peak = [reading for reading in named if not reading.by_fit]
        own = _own_reading(row, on_peak)
        if own is None:
            continue
        rivals = _rivals(own, on_peak)
        held_keys = (
            {own.key}
            | {r.key for r in rivals}
            | {r.key for r in _fit_tied(own, rivals, named)}
        )
        held_ions = {own.ion} | {r.ion for r in rivals}
        counts = by_kind[kind]
        counts["readings"] += 1
        counts["scored"] += len(candidates)
        outside = _outside(
            candidates, held_keys, held_ions, placed.get(peak, []), counts
        )
        if not outside:
            continue
        # The set they would make, read as the set reads its own: each ion
        # once, at its best evidence (``rival_set._rivals``).
        widened = _rivals(own, on_peak + list(outside))
        field = [(own.key, own.fit)] + [(r.key, r.fit) for r in widened]
        counted = set(unseparated(field, around=own.key))
        tied: set[str] = set()
        for reading in widened:
            # One tie per neutral, as the arbitration reads a formula once.
            if reading in outside and reading.key in counted - tied:
                tied.add(reading.key)
                counts["ties"][gap_band(outside[reading].gaps)] += 1
        density = provenance.get(CANDIDATE_DENSITY)
        today_density = density if isinstance(density, int) else 1
        new_density = max(1, len(counted))
        if new_density <= today_density:
            continue
        rises += 1
        if today_density < DENSITY_LIMIT <= new_density:
            tier = str(row.get("tier"))
            reach[tier] = reach.get(tier, 0) + 1
            moved = copy.copy(row)
            moved["provenance"] = {**provenance, CANDIDATE_DENSITY: new_density}
            would_cap += density_reason(moved) is not None
    return {
        "list": by_kind[KIND_LIST],
        "search": by_kind[KIND_SEARCH],
        "density_rises": rises,
        "reach_limit": dict(sorted(reach.items())),
        "would_cap": would_cap,
        "density_limit": DENSITY_LIMIT,
    }


async def count(ctx: Any) -> None:
    """Count, on a judged run with ``count_scored_rivals`` on, the candidates
    its search scored that tie a reading at the caps' model outside its set,
    into its search scope (:data:`SCORED_RIVALS_KEY`). Nothing else changes.

    :param ctx: The run's pass (``pipeline.RunContext``), its rows judged.
    """
    search = ctx.search
    judging = ctx.judging
    if (
        not ctx.config.count_scored_rivals
        or search is None
        or not search.ran
        or judging is None
        or judging.rival_frames is None
        or ctx.judged is None
        or ctx.judged.model is None
    ):
        return
    started = time.perf_counter()
    model = ctx.judged.model
    handed = handed_by_peak(
        search.matches,
        search.frame,
        mechanism_id_by_notation=search.mechanism_id_by_notation,
        formula_formatter=to_custom_element_format,
        list_scored=getattr(search.election, "scored_by_peak", None),
    )
    # A reading the run committed is a contender wherever a gated line of it
    # sits, so the set already holds it there: only the rest are measured.
    committed = untargeted_seeds(
        search.matches, search.mechanism_id_by_notation, to_custom_element_format
    )
    placed_for: dict[tuple[str, str], set[str]] = {}
    for (peak, _kind), candidates in handed.items():
        for candidate in candidates:
            if candidate.seed not in committed:
                placed_for.setdefault(candidate.seed, set()).add(peak)
    frames = []
    if placed_for:
        # Scored as the run scored its seeds, at the model the frames were;
        # read at the caps' model, as the set reads its members.
        ion_by_seed, _fits, _errors, scored = await score_seeds(
            ctx.sample,
            set(placed_for),
            ctx.match_params,
            model=ctx.model,
            resolution=ctx.resolution,
        )
        if not scored.empty:
            frames.append(
                SeededFrame(
                    scored,
                    ion_by_seed,
                    {seed: frozenset(peaks) for seed, peaks in placed_for.items()},
                )
            )
    record = tally(
        ctx.judged.rows,
        handed,
        judging.rival_frames,
        RivalFrames(None, frames, base=ctx.model),
        model,
    )
    record.update(
        refit_taken=model.version != ctx.model.version,
        named_at_version=ctx.model.version,
        measured_at_version=model.version,
        seeds_measured=len(placed_for),
        seconds=round(time.perf_counter() - started, 3),
        seeded_seconds=getattr(ctx.seeded_frame, "seconds", None),
    )
    search.scope[SCORED_RIVALS_KEY] = record
    ties = sum(sum(record[kind]["ties"].values()) for kind in (KIND_LIST, KIND_SEARCH))
    runtime.logger.info(
        f"Sample '{ctx.sample.sample_item_name}' scored rivals at the "
        f"v{model.version} model: {ties} outside the sets tie their reading, "
        f"{record['density_rises']} densities would rise, "
        f"{sum(record['reach_limit'].values())} to the limit "
        f"({record['seconds']:.1f} s beside the seeded pass's "
        f"{record['seeded_seconds'] or 0.0:.1f} s)"
    )
