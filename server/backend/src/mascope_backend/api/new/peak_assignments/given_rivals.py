"""The rivals of a batch decision measured on a sample, as a run's (MAS-1045).

A run's formula search names, on each peak it commits, the formulas its grid
holds that the peak's evidence cannot separate from the reading
(``finder.CANDIDATE_RIVALS``), and on each list reading it kept, the grid's
closed-shell rivals of it (``finder.rivals_of_readings``). The run measures
them beside its own readings, at the model it caps at, and the one rival set
(``rival_set.measure_rival_sets``) gives the row its density, which the
tiering pass's density rule caps on, and the rivals its identification level
lists.

A batch decision measured on a sample's members (``pipeline.PROPAGATION``: the
batch search's propagation, *Detect series*, an import and the batch's own
judging again) was never searched there, so nothing named a rival against it,
and a crowded mass read *assigned* on every member while the sample the batch
search read held it at *candidate*. Here the grid is asked of each elected
reading, once, the question a run's search asks of a list's: which of the
formulas its element box holds for the peak's mass the evidence there cannot
separate from the reading, the reading scored beside them by the same fit
(:func:`given_readings_rivals`). Those are measured beside the readings, at
the sample's model, as a run's named rivals are (:func:`measure_given_rivals`),
and the round reads them into the same rival set. Nothing else is measured:
the counting is the set's.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

import pandas as pd

from mascope_backend.api.new.cheminfo.utils import to_custom_element_format
from mascope_backend.api.new.peak_assignments import (
    kept_measurement,
    rival_set,
    sample_inputs,
    sample_mechanisms,
)
from mascope_backend.api.new.peak_assignments.engine import (
    _float_or_none,
    record_grid_rivals,
)
from mascope_backend.api.new.peak_assignments.row_terms import ROLE_M0
from mascope_backend.runtime import runtime
from mascope_tools.composition.exceptions import CompositionFinderException
from mascope_tools.composition.finder import (
    ListReading,
    ReadingRivals,
    rivals_of_readings,
)
from mascope_tools.composition.mechanism_notation import MechanismNotationError


def given_readings_rivals(
    rows: list[dict],
    peaks: pd.DataFrame,
    columns: list[str],
    *,
    notation_by_id: Mapping[str, str],
    search_config,
    heuristics,
    scoring,
    listed: bool,
) -> list[tuple[dict, ReadingRivals]]:
    """The grid's rivals of given readings, as a run's search names a row's.

    Each committed monoisotopic row's reading is scored beside the grid's
    candidates for its peak (``finder.rivals_of_readings``): as a list's
    reading where ``listed``, its rivals closed-shell as a run's list
    election has them, and otherwise as the search's own, a radical among
    them as among the search's candidates.

    :param rows: The elected rows; their committed monoisotopic ones are read.
    :param peaks: The sample's peaks, positionally indexed, with
        ``sample_peak_id``: the whole spectrum, as the search scores against.
    :param columns: The columns of ``peaks`` the search reads.
    :param notation_by_id: The searched channels' notations, by id. A row
        through none of them is not read.
    :param search_config: The search a run of the sample runs through them.
    :param heuristics: The heuristic filter it applies.
    :param scoring: The sample's scoring at its model.
    :param listed: Whether the rows are the lists' rows a run makes of them.
    :return: Each row the grid read, with what it held against it.
    """
    mz_by_peak = dict(
        zip(
            peaks["sample_peak_id"].astype(str),
            peaks["mz"].astype(float),
            strict=True,
        )
    )
    asked: list[tuple[dict, ListReading]] = []
    for row in rows:
        notation = notation_by_id.get(str(row.get("ionization_mechanism_id")))
        mz = mz_by_peak.get(str(row.get("sample_peak_id")))
        if (
            row.get("role") != ROLE_M0
            or not row.get("assigned_formula")
            or notation is None
            or mz is None
        ):
            continue
        asked.append(
            (
                row,
                ListReading(
                    mz=mz,
                    formula=str(row["assigned_formula"]),
                    ionization_mechanism=notation,
                    mz_error_ppm=_float_or_none(row.get("mz_error_ppm")),
                ),
            )
        )
    if not asked:
        return []
    found = rivals_of_readings(
        peaks[columns],
        search_config,
        [reading for _, reading in asked],
        heuristics,
        scoring,
        closed_shell_only=listed,
    )
    return [
        (row, rivals)
        for (row, _), rivals in zip(asked, found, strict=True)
        if rivals is not None
    ]


async def _ask_grid(
    ctx,
    family: list[dict],
    mechanisms,
    notations,
    peaks: tuple[pd.DataFrame, list[str]],
    heuristics,
    listed: bool,
) -> list[tuple[dict, object]] | None:
    """Ask the grid of one ion family's given readings, off the event loop
    (CPU-bound, as the search is).

    :return: What ``given_readings_rivals`` returns; None when the grid cannot
        read the family's channel, whose readings are then measured without
        the grid's rivals, as a reading the search could not read is in a
        run, not the sample unmeasured.
    """
    frame, columns = peaks
    try:
        return await asyncio.to_thread(
            given_readings_rivals,
            family,
            frame,
            columns,
            notation_by_id=sample_mechanisms._notation_by_id(mechanisms),
            search_config=ctx.resolved_profile.search_config(notations),
            heuristics=heuristics,
            scoring=ctx.scoring,
            listed=listed,
        )
    except (CompositionFinderException, MechanismNotationError) as exc:
        runtime.logger.warning(
            f"Sample '{ctx.sample.sample_item_name}': the grid could not "
            f"be asked of {len(family)} given readings ({exc}); they are "
            "measured without its rivals"
        )
        return None


async def measure_given_rivals(ctx, scorer: kept_measurement.Scorer) -> None:
    """Measure the rivals a run's row on each given reading's peak gets.

    Under a run's rules, on the elect stage's rows: the lists' rows a run
    makes of the readings (``ctx.stage_a``) and the search's
    (``ctx.stage_b``), each read through the mechanisms its ion's family is
    read through (``sample_mechanisms.family_mechanisms``). A list's row
    records what the grid held against it, as a run's does
    (``engine.record_grid_rivals``), and hands on the grid formulas only its
    fit could not separate, recorded apart in its set
    (``rival_set.SeededFrame.fit_only``, MAS-975). The rivals are measured in one seeded
    pass at the sample's model, kept for the batch's pass as its readings are
    (``kept_measurement.score_kept``), each placed only on the peaks it was
    named for (``rival_set.SeededFrame.placed_for``); the judging reads them
    from ``ctx.rival_frame`` into each row's rival set.

    Only on a path that asks a run's rules (``pipeline.Rules.RUN``): a
    person's choice is asked the spectrum's rules alone, which weigh no
    rivals. Compared by the rules' value, so this module does not import the
    pipeline that calls it.

    :param ctx: The stage list's pass (``pipeline.RunContext``), its given
        readings elected.
    :param scorer: The seeded scorer (``seeded_scoring.score_seeds``).
    """
    if ctx.path.rules != "run":
        return
    started = time.perf_counter()
    frame = ctx.peaks_df.reset_index(drop=True)
    columns = [c for c in sample_inputs.SEARCH_PEAK_COLUMNS if c in frame]
    heuristics = ctx.resolved_profile.heuristics_config()
    named: dict[tuple[str, str], set[str]] = {}
    by_fit: dict[tuple[str, str], set[str]] = {}
    asked = 0
    for rows, listed in ((ctx.stage_a, True), (ctx.stage_b, False)):
        for family, mechanisms in sample_mechanisms.family_mechanisms(
            rows, ctx.searched_mechanisms, ctx.judged_mechanisms
        ):
            notations, by_notation = sample_mechanisms._untargeted_ionization_notations(
                mechanisms
            )
            if not notations:
                continue
            found = await _ask_grid(
                ctx, family, mechanisms, notations, (frame, columns), heuristics, listed
            )
            if found is None:
                continue
            asked += len(found)
            if listed:
                record_grid_rivals(
                    [row for row, _ in found], [rivals for _, rivals in found]
                )
            # A list's reading also hands on the grid formulas only its fit
            # could not separate, which its set records apart (MAS-975), as a
            # run's list election does; the search's own rows name none.
            for into, which in ((named, "rivals"), (by_fit, "fit_tied")):
                if which == "fit_tied" and not listed:
                    continue
                for seed, peaks in rival_set.named_rival_seeds(
                    pd.DataFrame(),
                    frame,
                    mechanism_id_by_notation=by_notation,
                    formula_formatter=to_custom_element_format,
                    list_rivals={
                        str(row["sample_peak_id"]): getattr(rivals, which)
                        for row, rivals in found
                    },
                ).items():
                    into.setdefault(seed, set()).update(peaks)
    # A rival the pass also gives is measured as the reading it gives, as a
    # run measures a named rival its search also commits.
    fit_only = {s: frozenset(p) for s, p in by_fit.items() if s not in ctx.seeds}
    placed = {s: frozenset(p) for s, p in named.items() if s not in ctx.seeds}
    for seed, peaks in fit_only.items():
        placed[seed] = placed.get(seed, frozenset()) | peaks
    if placed:
        ion_by_seed, _, _, scored = await kept_measurement.score_kept(
            ctx, scorer, (), rivals=placed
        )
        if not scored.empty:
            ctx.rival_frame = rival_set.SeededFrame(
                scored, ion_by_seed, placed, fit_only
            )
    runtime.logger.info(
        f"Sample '{ctx.sample.sample_item_name}' asked the grid of {asked} given "
        f"readings and measured {len(placed)} rivals beside them "
        f"({time.perf_counter() - started:.1f} s)"
    )
