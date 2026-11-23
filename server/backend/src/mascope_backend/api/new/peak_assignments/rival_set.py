"""One rival set per peak, measured once at the run's measurement model.

A committed row's rivals are the other ions the run measured on its peak.
Each reader of them used to take its rivals from a measurement of its own
(MAS-974, finding 4): Stage A's density came from its own arbitration at
Stage A's fit, the formula search's grid rivals were counted into it at the
finder's fit, a search row's density came from the finder's fit while the row
was tiered on the seeded re-score, the 4.15 shortlist share was measured at
the re-score's width beside rivals stored at the finder's, and the
identification level read the grid's and the shortlist's own numbers. A run
that took its v2 model kept all of them as Stage A and the finder read them
at v1 (#3366).

Here they are one set, measured once, in the judging round, at the model the
round's rows were scored and are capped at (``judging_loop.Judging``): its v2
where the refit took it, its v1 otherwise, a decoy's control's or a
sample's recorded one. The set is read out of the frames the run measured
its readings in, each scored at that model (``engine.score_frame_at``):

- **Stage A's frame**: every ion of the known set one of whose gated lines
  sits on the peak, as Stage A's arbitration groups them
  (``engine.invert_matches_to_peak_assignments``);
- **the seeded frames**: the search's committed readings, every gated line of
  them; and the readings of the peak's own mass the search named against a
  reading (``finder.CANDIDATE_RIVALS``, a list reading's grid rivals,
  ``finder.ReadingRivals``) and the 4.15 shortlist the run measured, each read
  only on the peaks it was named for and only as its ion's monoisotopic line
  paired to the peak, as the inspector places a shortlist formula.

The readers then read the set and none measures its own:

- the arbitration's ``confidence``, ``n_candidates`` and ``is_tie``, and the
  ``candidate_density`` the tiering pass's density rule and its
  ``no_close_rival`` read, through ``arbitration``'s one tie rule;
- the 4.15 share, which is that same confidence and tie once the shortlist's
  readings are among the rivals (``engine.MEASURED_SHORTLIST`` records what
  each of its formulas measured at);
- the identification level's exclusions (``identification_level.shortlist``),
  which read the rivals the set names, at the set's fit.

Which candidates a reading competes against is still the search's to say: it
names a rival where its own ranking could not separate the two, at the v1
model it ranks at (MAS-982 keeps the ranking at v1). What each of them
measures, and so whether the run's evidence separates it, is the set's.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import pandas as pd

from mascope_backend.api.new.peak_assignments.engine import (
    MEASURED_SHORTLIST,
    _float_or_none,
    _reference_copies,
    _score_or_none,
    _str_or_none,
    formula_identity,
    ion_identity,
    score_frame_at,
)
from mascope_backend.api.new.peak_assignments.monoisotopic import monoisotopic_row
from mascope_backend.api.new.peak_assignments.row_terms import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
)
from mascope_backend.api.new.peak_assignments.untargeted_rows import (
    _resolve_peak_positions,
    _seed_of,
)
from mascope_tools.composition.arbitration import (
    CANDIDATE_DENSITY,
    arbitrate_candidates,
    unseparated,
)
from mascope_tools.composition.finder import CANDIDATE_RIVALS
from mascope_tools.composition.heuristic_filter import formula_plausibility
from mascope_tools.composition.measurement_model import MeasurementModel


#: The provenance block a committed monoisotopic row carries: the rivals the
#: run measured on its peak, at the model its caps read.
RIVAL_SET = "rival_set"

#: How many rivals the block names, best evidence first. The count it read
#: off all of them is ``candidate_density``; the ones past this are counted
#: under ``rivals_unlisted``.
RIVALS_KEPT = 10

#: Where a rival was measured: Stage A's frame, the known set's ions.
FRAME_KNOWN = "known_set"

#: Where a rival was measured: a seeded frame, the search's readings and the
#: readings it named.
FRAME_SEARCH = "search"


@dataclass(frozen=True)
class SeededFrame:
    """A seeded frame: readings measured as Stage A measures an ion.

    :param scored: The gated, fit-scored frame (``seeded_scoring.score_seeds``).
    :param ion_by_seed: Each seed's synthetic ion id in it.
    :param placed_for: The seeds measured as readings of named peaks' own
        mass, each with the peaks it was named for. Such a reading is a rival
        on those peaks alone, and only where its monoisotopic line paired to
        the peak. A seed not here is a reading the run commits, every gated
        line of which is a contender on its peak.
    """

    scored: pd.DataFrame
    ion_by_seed: dict[tuple[str, str], str]
    placed_for: Mapping[tuple[str, str], frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Reading:
    """One ion measured on one peak, at one model.

    :param peak: The peak's id.
    :param formula: The neutral, as stored.
    :param key: The neutral's identity (``utils.formula_identity``).
    :param ion_formula: The ion.
    :param ion: The ion's identity (``utils.ion_identity``), its neutral's
        where it names none.
    :param mechanism_id: The channel.
    :param fit: Its fit at the model.
    :param mz_error_ppm: The line's mass error.
    :param monoisotopic: Whether the line on the peak is the ion's own.
    :param frame: :data:`FRAME_KNOWN` or :data:`FRAME_SEARCH`.
    """

    peak: str
    formula: str
    key: str
    ion_formula: str | None
    ion: tuple
    mechanism_id: str | None
    fit: float
    mz_error_ppm: float | None
    monoisotopic: bool
    frame: str


def m0_by_ion(scored_df: pd.DataFrame) -> dict[str, pd.Series]:
    """The monoisotopic row of each ion in a scored frame
    (``monoisotopic.monoisotopic_row``): the line a reading of the peak's own mass
    reads the peak as.

    :param scored_df: A gated, fit-scored frame.
    :return: Ion id -> its monoisotopic row.
    """
    if scored_df.empty or "target_ion_id" not in scored_df.columns:
        return {}
    return {
        str(ion_id): monoisotopic_row(group)
        for ion_id, group in scored_df.groupby("target_ion_id", sort=False)
    }


def paired_to(row: pd.Series | None, sample_peak_id: str) -> bool:
    """Whether a scored line paired to this observed peak."""
    if row is None:
        return False
    peak_id = row.get("sample_peak_id")
    if peak_id is None or (isinstance(peak_id, float) and pd.isna(peak_id)):
        return False
    return str(peak_id) == str(sample_peak_id)


@dataclass(frozen=True)
class _Lines:
    """The lines of one frame that are contenders on their peaks, and what
    each reads as, before any model scores them."""

    frame: pd.DataFrame
    index: pd.Index
    readings: list[tuple]
    name: str


def _gated_on_a_peak(frame: pd.DataFrame) -> pd.Series:
    """Lines the gate kept that paired to a peak (Stage A's ownership guard)."""
    peak = frame["sample_peak_id"]
    if "sample_peak_intensity" not in frame.columns:
        return pd.Series(False, index=frame.index)
    return (
        peak.notna()
        & (peak.astype(str) != "")
        & (pd.to_numeric(frame["sample_peak_intensity"], errors="coerce").fillna(0) > 0)
    )


def _main_isotopes(frame: pd.DataFrame) -> set:
    return {
        row["target_isotope_id"]
        for row in (
            monoisotopic_row(group)
            for _, group in frame.groupby("target_ion_id", sort=False)
        )
    }


def _known_lines(frame: pd.DataFrame | None) -> _Lines | None:
    """Stage A's contenders: every gated line on a peak, less a reference
    list's copy of a library row's reading there, as Stage A arbitrates."""
    needed = {
        "sample_peak_id",
        "sample_peak_intensity",
        "target_compound_formula",
        "target_ion_id",
        "target_isotope_id",
        "mz",
    }
    if frame is None or frame.empty or not needed.issubset(frame.columns):
        return None
    # Positional: a gated, scored frame can carry a duplicated index.
    frame = frame.reset_index(drop=True)
    matched = frame[_gated_on_a_peak(frame)].copy()
    matched["_formula_key"] = (
        matched["target_compound_formula"].astype(str).map(formula_identity)
    )
    matched = matched[~_reference_copies(matched)]
    mains = _main_isotopes(frame)
    readings = []
    for _, row in matched.iterrows():
        key = str(row["_formula_key"])
        ion_formula = row.get("target_ion_formula")
        ion_formula = ion_formula if isinstance(ion_formula, str) else None
        readings.append(
            (
                str(row["sample_peak_id"]),
                str(row["target_compound_formula"]),
                key,
                ion_formula,
                ion_identity(ion_formula) or ("", key),
                _str_or_none(row.get("ionization_mechanism_id")),
                _float_or_none(row.get("match_mz_error")),
                row["target_isotope_id"] in mains,
            )
        )
    return _Lines(frame, matched.index, readings, FRAME_KNOWN)


def _seeded_lines(seeded: SeededFrame) -> _Lines | None:
    """A seeded frame's contenders: every gated line of a reading the run
    commits, and a named reading's monoisotopic line on a peak it was named
    for."""
    frame = seeded.scored
    if frame is None or frame.empty or "sample_peak_id" not in frame.columns:
        return None
    frame = frame.reset_index(drop=True)
    seed_of_ion = {str(ion): seed for seed, ion in seeded.ion_by_seed.items()}
    own_lines = {int(row.name) for row in m0_by_ion(frame).values()}
    gated = _gated_on_a_peak(frame)
    kept = []
    readings = []
    for position, row in frame.iterrows():
        seed = seed_of_ion.get(str(row.get("target_ion_id")))
        if seed is None:
            continue
        own_line = position in own_lines
        named_for = seeded.placed_for.get(seed)
        if named_for is not None:
            # A reading of a named peak's own mass, placed as the inspector
            # places a shortlist formula (``alternatives_scoring.placements``):
            # its monoisotopic line paired to the peak.
            if not own_line or not paired_to(row, str(row.get("sample_peak_id"))):
                continue
            if str(row["sample_peak_id"]) not in named_for:
                continue
        elif not gated.iloc[position]:
            continue
        ion_formula = row.get("target_ion_formula")
        ion_formula = ion_formula if isinstance(ion_formula, str) else None
        key = formula_identity(seed[0])
        kept.append(position)
        readings.append(
            (
                str(row["sample_peak_id"]),
                str(seed[0]),
                key,
                ion_formula,
                ion_identity(ion_formula) or ("", key),
                str(seed[1]),
                _float_or_none(row.get("match_mz_error")),
                own_line,
            )
        )
    return _Lines(frame, pd.Index(kept), readings, FRAME_SEARCH)


class RivalFrames:
    """The frames a run measured its readings in, read at any model.

    :param stage_a: Stage A's gated, scored frame, or None.
    :param seeded: The seeded frames (:class:`SeededFrame`).
    :param base: The model the frames were scored at: read there, a line's
        fit is the one its frame holds.
    :param shortlists: The peaks whose 4.15 shortlist the run measured, each
        with the formulas it measured; empty where the pass did not run.
    """

    def __init__(
        self,
        stage_a: pd.DataFrame | None,
        seeded: Iterable[SeededFrame | None],
        *,
        base: MeasurementModel | None,
        shortlists: Mapping[str, tuple[str, ...]] | None = None,
    ):
        lines = [_known_lines(stage_a)] + [
            _seeded_lines(frame) for frame in seeded if frame is not None
        ]
        self._lines = [found for found in lines if found is not None]
        self._base = base
        self._at: list[tuple[MeasurementModel, dict[str, list[Reading]]]] = []
        self.shortlists = dict(shortlists or {})

    def readings_at(self, model: MeasurementModel) -> dict[str, list[Reading]]:
        """Every contender of every peak, its fit at ``model``.

        :param model: The model.
        :return: Peak id -> the ions measured on it.
        """
        for held, readings in self._at:
            if held is model:
                return readings
        by_peak: dict[str, list[Reading]] = {}
        for lines in self._lines:
            if not lines.readings:
                continue
            fits = (
                lines.frame["match_score"]
                if model is self._base and "match_score" in lines.frame.columns
                else score_frame_at(lines.frame, model)
            ).reindex(lines.index)
            for reading, fit in zip(lines.readings, fits, strict=True):
                peak, formula, key, ion_formula, ion, mechanism, error, own = reading
                by_peak.setdefault(peak, []).append(
                    Reading(
                        peak,
                        formula,
                        key,
                        ion_formula,
                        ion,
                        None if mechanism is None else str(mechanism),
                        _score_or_none(fit) or 0.0,
                        error,
                        bool(own),
                        lines.name,
                    )
                )
        self._at.append((model, by_peak))
        return by_peak


def _evidence(reading: Reading) -> float:
    return reading.fit * float(formula_plausibility(reading.key))


def _own_reading(row: dict, on_peak: list[Reading]) -> Reading | None:
    """The row's own reading among its peak's: its neutral through its
    channel, on its own line where a frame holds that."""
    key = formula_identity(row.get("assigned_formula"))
    mechanism = str(row.get("ionization_mechanism_id"))
    own = [
        reading
        for reading in on_peak
        if reading.key == key and reading.mechanism_id == mechanism
    ]
    return max(own, key=lambda r: (r.monoisotopic, r.fit), default=None)


def _rivals(own: Reading, on_peak: list[Reading]) -> list[Reading]:
    """The other ions on the peak, each read once: the reading with the best
    evidence stands for its ion, and a reading of the row's own ion is the
    row's measurement split another way, no rival
    (``engine.invert_matches_to_peak_assignments``)."""
    best: dict[tuple, Reading] = {}
    for reading in on_peak:
        # The row's own neutral through another channel is one hypothesis
        # arriving twice, which the arbitration collapses, no rival.
        if reading.ion == own.ion or reading.key == own.key:
            continue
        held = best.get(reading.ion)
        if held is None or (_evidence(reading), reading.fit) > (
            _evidence(held),
            held.fit,
        ):
            best[reading.ion] = reading
    return sorted(best.values(), key=lambda r: (-_evidence(r), -r.fit, r.key))


def _entry(reading: Reading, counted: set[str]) -> dict:
    """A rival as the set records it."""
    return {
        "formula": reading.formula,
        "ion_formula": reading.ion_formula,
        "ionization_mechanism_id": reading.mechanism_id,
        "fit_score": round(reading.fit, 4),
        "mz_error_ppm": (
            None if reading.mz_error_ppm is None else round(reading.mz_error_ppm, 4)
        ),
        "monoisotopic": reading.monoisotopic,
        "frame": reading.frame,
        "unseparated": reading.key in counted,
    }


def _shortlist_record(formulas: tuple[str, ...], on_peak: list[Reading]) -> list[dict]:
    """What each formula of a winner's 4.15 shortlist measured at on its peak
    (``engine.MEASURED_SHORTLIST``): its best reading there as its ion's own
    line, the winner's own ion included, or ``placed: False`` where no channel
    put it on the peak."""
    record = []
    for formula in formulas:
        key = formula_identity(formula)
        placed = [r for r in on_peak if r.key == key and r.monoisotopic]
        if not placed:
            record.append({"formula": formula, "placed": False})
            continue
        best = max(placed, key=lambda r: (_evidence(r), r.fit))
        record.append(
            {
                "formula": formula,
                "ion_formula": best.ion_formula,
                "fit_score": round(best.fit, 4),
                "mz_error_ppm": (
                    None if best.mz_error_ppm is None else round(best.mz_error_ppm, 4)
                ),
            }
        )
    return record


def measure_rival_sets(
    rows: list[dict], frames: RivalFrames, model: MeasurementModel
) -> dict:
    """Measure each committed row's rival set at a model, in place.

    For every committed monoisotopic row whose own reading a frame measured on
    its peak: the rivals on that peak (:class:`RivalFrames`), each ion once,
    arbitrated with the row's reading by ``arbitration``'s one tie rule. The
    row's ``candidate_density`` is read off that arbitration and
    :data:`RIVAL_SET` records the set. Its ``confidence``, ``n_candidates``
    and ``is_tie`` are read off it too where the set is the peak's whole
    field: on a list's row, and on a search winner the 4.15 pass reached,
    which also records its shortlist (``engine.MEASURED_SHORTLIST``). A Stage A isotopologue row
    takes its peak's confidence and tie as Stage A gave it one; it carries no
    density. A row no frame measured keeps what its stage recorded.

    :param rows: The round's rows, modified in place.
    :param frames: The frames the run measured its readings in.
    :param model: The model the round's caps read.
    :return: What the run records: the rows measured, those whose reading no
        frame measured, the model's version, and what the 4.15 shortlist
        placed.
    """
    by_peak = frames.readings_at(model)
    measured = unmeasured = placed = ties = 0
    for row in rows:
        role, source = row.get("role"), row.get("source")
        if not row.get("assigned_formula") or source not in (
            SOURCE_DATABASE,
            SOURCE_UNTARGETED,
        ):
            continue
        if role != ROLE_M0 and not (
            role == ROLE_ISO_CHILD and source == SOURCE_DATABASE
        ):
            continue
        peak = str(row.get("sample_peak_id"))
        on_peak = by_peak.get(peak, [])
        own = _own_reading(row, on_peak)
        if own is None:
            unmeasured += role == ROLE_M0
            continue
        rivals = _rivals(own, on_peak)
        candidates = [(own.key, own.fit)] + [(r.key, r.fit) for r in rivals]
        arbitrated = arbitrate_candidates(candidates)
        mine = next(c for c in arbitrated if c.formula == own.key)
        provenance = row.setdefault("provenance", {})
        formulas = frames.shortlists.get(peak)
        if source == SOURCE_DATABASE or formulas is not None:
            # A share of the whole field: the known set's frame holds every
            # candidate it competed, and a search winner's field is whole once
            # the 4.15 pass measured its shortlist. Of part of the field it
            # would read as a share of all of it.
            provenance["confidence"] = round(mine.confidence, 4)
            provenance["n_candidates"] = len(arbitrated)
            provenance["is_tie"] = mine.is_tie
        if role != ROLE_M0:
            continue
        measured += 1
        counted = set(unseparated(candidates, around=own.key))
        provenance[CANDIDATE_DENSITY] = max(1, len(counted))
        provenance[RIVAL_SET] = {
            "model_version": model.version,
            "fit_score": round(own.fit, 4),
            "rivals": [_entry(r, counted) for r in rivals[:RIVALS_KEPT]],
            "rivals_unlisted": max(0, len(rivals) - RIVALS_KEPT),
        }
        if source == SOURCE_UNTARGETED and formulas is not None:
            record = _shortlist_record(formulas, on_peak)
            provenance[MEASURED_SHORTLIST] = record
            placed += sum(1 for entry in record if entry.get("placed") is not False)
            ties += bool(mine.is_tie)
    return {
        "model_version": model.version,
        "measured": measured,
        "unmeasured": unmeasured,
        "shortlist_placed": placed,
        "shortlist_ties": ties,
    }


def named_rival_seeds(
    matches_df: pd.DataFrame,
    frame: pd.DataFrame,
    *,
    mechanism_id_by_notation: Mapping[str, str],
    formula_formatter=None,
    list_rivals: Mapping[str, Iterable[dict]] | None = None,
) -> dict[tuple[str, str], frozenset[str]]:
    """The readings the search named against a peak's reading, as seeds.

    Each committed monoisotopic reading of the search names the candidates
    its ranking could not separate from it (``finder.CANDIDATE_RIVALS``), and
    each list reading that kept its peak the grid's (``finder.ReadingRivals``,
    ``engine.ListElection.rivals_by_peak``). Seeded beside the search's own
    readings, they are measured in the frame those are, at the run's model.

    :param matches_df: The search's result.
    :param frame: The frame the search was handed, with ``sample_peak_id``,
        which a result's m/z is joined back to.
    :param mechanism_id_by_notation: The searched channels, by notation.
    :param formula_formatter: How a formula is stored.
    :param list_rivals: The grid's rivals of each kept list reading, by its
        peak.
    :return: Seed -> the peaks it was named for.
    """
    format_formula = formula_formatter or (lambda formula: formula)
    named: dict[tuple[str, str], set[str]] = {}

    def name(peak: str, rivals: Iterable[dict]) -> None:
        for rival in rivals:
            seed = _seed_of(rival, dict(mechanism_id_by_notation), format_formula)
            if seed is not None:
                named.setdefault(seed, set()).add(peak)

    if not matches_df.empty and CANDIDATE_RIVALS in matches_df.columns:
        rows = matches_df[
            matches_df[CANDIDATE_RIVALS].map(lambda value: isinstance(value, list))
        ]
        positions = _resolve_peak_positions(frame, rows["mz"].astype(float).tolist())
        peak_ids = frame["sample_peak_id"].astype(str).to_numpy()
        for rivals, position in zip(rows[CANDIDATE_RIVALS], positions, strict=True):
            if position is not None:
                name(str(peak_ids[position]), rivals)
    for peak, rivals in (list_rivals or {}).items():
        name(str(peak), rivals)
    return {seed: frozenset(peaks) for seed, peaks in named.items()}
