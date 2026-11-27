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
from dataclasses import dataclass, field

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
from mascope_backend.api.new.peak_assignments.row_terms import ROLE_ISO_CHILD, ROLE_M0
from mascope_backend.api.new.peak_assignments.tiering import density_escaped
from mascope_backend.api.new.peak_assignments.tiers import TIER_ASSIGNED
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
    memo: dict | None = None,
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
    :param memo: What the grid held against readings asked before at the
        same peaks and model (``kept_measurement.kept_grid``), read again
        here and filled with what is asked now; None asks every one.
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
    memo = {} if memo is None else memo
    asking = (repr(search_config), repr(heuristics), listed)
    keys = [(asking, reading) for _, reading in asked]
    missing = list(dict.fromkeys(key for key in keys if key not in memo))
    if missing:
        found = rivals_of_readings(
            peaks[columns],
            search_config,
            [reading for _, reading in missing],
            heuristics,
            scoring,
            closed_shell_only=listed,
        )
        memo.update(zip(missing, found, strict=True))
    return [
        (row, memo[key])
        for (row, _), key in zip(asked, keys, strict=True)
        if memo[key] is not None
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
    (CPU-bound, as the search is), through the pass's kept answers.

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
            memo=kept_measurement.kept_grid(ctx),
        )
    except (CompositionFinderException, MechanismNotationError) as exc:
        runtime.logger.warning(
            f"Sample '{ctx.sample.sample_item_name}': the grid could not "
            f"be asked of {len(family)} given readings ({exc}); they are "
            "measured without its rivals"
        )
        return None


@dataclass
class HeldRivals:
    """The search's rows' readings, held from the elect stage until the cap
    stage says on which peaks their rivals can move a tier
    (:func:`measure_held_rivals`).

    :param families: Each ion family's rows, as the elect stage left them,
        with its mechanisms, its channels' notations and their ids.
    :param seeds: The pass's own readings, which a rival is measured as.
    :param asked: The peaks the grid was asked of so far.
    """

    families: list[tuple[list[dict], list, list, dict]]
    seeds: frozenset
    asked: set[str] = field(default_factory=set)


def _search_inputs(ctx) -> tuple[pd.DataFrame, list[str], object]:
    """The sample's peaks as the search reads them, its columns, and the
    heuristic filter it applies."""
    frame = ctx.peaks_df.reset_index(drop=True)
    columns = [c for c in sample_inputs.SEARCH_PEAK_COLUMNS if c in frame]
    return frame, columns, ctx.resolved_profile.heuristics_config()


async def _named_seeds(
    ctx, families, listed: bool
) -> tuple[dict[tuple[str, str], set[str]], dict[tuple[str, str], set[str]], int]:
    """Ask the grid of the families' readings and name the rivals it held as
    seeds, each with the peaks it was named for: the rivals, and a list's
    reading's formulas only its fit could not separate (MAS-975).

    :return: The rivals, the fit-tied ones, and how many readings the grid
        read.
    """
    frame, columns, heuristics = _search_inputs(ctx)
    named: dict[tuple[str, str], set[str]] = {}
    by_fit: dict[tuple[str, str], set[str]] = {}
    asked = 0
    for family, mechanisms, notations, by_notation in families:
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
    return named, by_fit, asked


async def _measured(
    ctx, scorer, named, by_fit, seeds
) -> tuple[rival_set.SeededFrame | None, int]:
    """Measure named rivals in one seeded pass at the sample's model, each
    placed only on the peaks it was named for.

    :return: The frame they were measured in, None where none was, and how
        many were measured.
    """
    # A rival the pass also gives is measured as the reading it gives, as a
    # run measures a named rival its search also commits.
    fit_only = {s: frozenset(p) for s, p in by_fit.items() if s not in seeds}
    placed = {s: frozenset(p) for s, p in named.items() if s not in seeds}
    for seed, peaks in fit_only.items():
        placed[seed] = placed.get(seed, frozenset()) | peaks
    if not placed:
        return None, 0
    ion_by_seed, _, _, scored = await kept_measurement.score_kept(
        ctx, scorer, (), rivals=placed
    )
    if scored.empty:
        return None, len(placed)
    return rival_set.SeededFrame(scored, ion_by_seed, placed, fit_only), len(placed)


def _families(ctx, rows: list[dict]) -> list[tuple[list[dict], list, list, dict]]:
    """The rows by ion family, each with its mechanisms and the notations of
    the channels the search reads them through; a family with none left
    out."""
    families = []
    for family, mechanisms in sample_mechanisms.family_mechanisms(
        rows, ctx.searched_mechanisms, ctx.judged_mechanisms
    ):
        notations, by_notation = sample_mechanisms._untargeted_ionization_notations(
            mechanisms
        )
        if notations:
            families.append((family, mechanisms, notations, by_notation))
    return families


async def measure_given_rivals(ctx, scorer: kept_measurement.Scorer) -> None:
    """Measure the rivals a run's row on each given reading's peak gets.

    On the elect stage's rows, the lists' (``ctx.stage_a``) and the search's
    (``ctx.stage_b``), each through its ion family's mechanisms
    (``sample_mechanisms.family_mechanisms``). A list's row records what the
    grid held against it (``engine.record_grid_rivals``) and hands on the
    formulas only its fit could not separate (``SeededFrame.fit_only``,
    MAS-975), as a run's does. The rivals are measured in one seeded pass at
    the sample's model, placed only on the peaks they were named for; the
    grid's answers and that pass are kept through the batch's pass
    (``kept_measurement.kept_grid``, ``score_kept``), and the judging reads
    ``ctx.rival_frame`` into each row's rival set.

    The lists' rows are asked here. The search's are held
    (``ctx.held_rivals``) until the cap stage, which asks only those whose
    rivals can move a tier (:func:`measure_held_rivals`, MAS-1346).

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
    named, by_fit, asked = await _named_seeds(ctx, _families(ctx, ctx.stage_a), True)
    ctx.rival_frame, measured = await _measured(ctx, scorer, named, by_fit, ctx.seeds)
    # The judging reads copies of these rows. A row the elect stage goes on
    # to read as an isotope line (``batch_decisions.read_strips``) is then
    # asked nothing, as it would have been: no rival set is measured on such
    # a line, so its peak is none the caps ask for.
    ctx.held_rivals = HeldRivals(_families(ctx, ctx.stage_b), frozenset(ctx.seeds))
    runtime.logger.info(
        f"Sample '{ctx.sample.sample_item_name}' asked the grid of {asked} given "
        f"list readings and measured {measured} rivals beside them "
        f"({time.perf_counter() - started:.1f} s)"
    )


def peaks_rivals_can_move(rows: list[dict]) -> set[str]:
    """The peaks on which the rivals a reading is measured beside can move a
    tier, as the caps are about to read them (``judging_loop.Judging.cap``).

    The rivals reach a tier through the density rule alone
    (``tiering.density_reason``), which only takes an *assigned* row's tier
    (``tiering._cap``), and through an isotope line, which follows its
    monoisotopic row down whichever rule the tiering pass named on it, the
    density rule included, whatever that row's own tier. The caps only lower
    a tier, and the rule spares a row a second channel corroborates or a
    batch series anchors, both read before the caps
    (``tiering.density_escaped``). So the peaks are those of the committed
    monoisotopic rows the rule does not spare that read *assigned* before the
    caps, or own an isotope line that does.

    :param rows: The round's rows, as the caps are about to read them.
    :return: Their peaks' ids.
    """
    m0 = {
        str(row.get("peak_assignment_id")): row
        for row in rows
        if row.get("role") == ROLE_M0 and row.get("assigned_formula")
    }
    capped = {key: row for key, row in m0.items() if not density_escaped(row)}
    peaks = {
        str(row.get("sample_peak_id"))
        for row in capped.values()
        if row.get("tier") == TIER_ASSIGNED
    }
    for row in rows:
        if (
            row.get("role") == ROLE_ISO_CHILD
            and row.get("assigned_formula")
            and row.get("tier") == TIER_ASSIGNED
        ):
            owner = capped.get(str(row.get("owner_peak_assignment_id") or ""))
            if owner is not None:
                peaks.add(str(owner.get("sample_peak_id")))
    return peaks


async def measure_held_rivals(ctx, scorer: kept_measurement.Scorer) -> None:
    """Ask the grid of the search's rows the elect stage held
    (``ctx.held_rivals``), on the peaks where the rivals can move a tier
    (:func:`peaks_rivals_can_move`), and measure them beside the readings.

    A run's search names its rivals on every row it commits, and nothing of a
    given reading's propagation keeps more of its judged row than the tier
    (``batch_untargeted._write_measured_members``,
    ``batch_rejudge.rejudge_sample``). A row whose rivals cannot move its tier,
    nor its isotope line's, is not asked (MAS-1346): its tier is the one the
    rivals would leave it at. Each round asks only the peaks no earlier round
    asked, and the frame it measures joins the judging's
    (``rival_set.RivalFrames.add``): the readings a peak's rivals are read
    from are the ones a single ask of every row gives, since each rival is
    measured alone and placed only on the peaks it was named for.

    :param ctx: The stage list's pass, its round judged and about to be
        capped.
    :param scorer: The seeded scorer (``seeded_scoring.score_seeds``).
    """
    held: HeldRivals | None = ctx.held_rivals
    if held is None or ctx.judging.rival_frames is None:
        return
    frames = ctx.judging.rival_frames
    started = time.perf_counter()
    wanted = peaks_rivals_can_move(ctx.judging.judged) - held.asked
    families = [
        (rows, mechanisms, notations, by_notation)
        for family, mechanisms, notations, by_notation in held.families
        if (rows := [row for row in family if str(row.get("sample_peak_id")) in wanted])
    ]
    if not families:
        return
    held.asked |= wanted
    named, _, asked = await _named_seeds(ctx, families, False)
    frame, measured = await _measured(ctx, scorer, named, {}, held.seeds)
    if frame is not None:
        frames.add(frame)
    waiting = sum(len(family) for family, *_ in held.families)
    runtime.logger.info(
        f"Sample '{ctx.sample.sample_item_name}' asked the grid of {asked} of "
        f"{waiting} given search readings, those whose rivals can move a tier, "
        f"and measured {measured} rivals beside them "
        f"({time.perf_counter() - started:.1f} s)"
    )
