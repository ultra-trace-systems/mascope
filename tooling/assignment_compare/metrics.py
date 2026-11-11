"""The gate card's metrics, as code (assignment quality plan, step 6.4).

Every metric of the measurement log's table (``docs/dev/assignment_quality_plan_log.md``,
"Metrics and targets") and of the plan's stage 3 text (G9 to G14, defined under
"Baselines on the chamber dataset" in the log) is one function here, its
docstring opening with the log's own definition. The functions read the frame
``compare_runs.join_ledgers`` builds, one row per peak, pooled over a set's
samples, with engine A's reading of the peak in the ``a_*`` columns and engine
B's, the reference's, in the ``b_*`` ones. The card adds:

- ``reference``: whether the row's sample carries a reference run. A sample
  without one is in the frame with ``b_role`` ``missing`` on every row, so the
  intrinsic metrics read it; every metric that reads the reference leaves it
  out.
- ``a_unsearched``: the peaks engine A's untargeted stage was never offered
  (``compare_runs.unsearched_peaks``), for G5.
- ``sulfur_free``: whether the set's chemistry holds no sulfur, for G9.
- What the detail read of engine A's committed rows said
  (:func:`provenance_columns`): ``a_reasons``, ``a_mass_gate_corroborated``,
  ``a_absent_lines`` and ``a_unseparated``, for the reason coverage, G7, G13
  and G14; and ``a_detail_failed``, the rows whose read failed.

A metric returns a reading, ``{"value": ..., "n": ...}``: the number the card
prints and the count it is a share of (or counted over). The value is None
where the metric cannot be read on the frame - no reference run for a metric
that reads one, no row to take a share of, a record the run predates - which
the card prints as such rather than as a zero.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

from mascope_tools.composition.reagents import reagent_library


ROLE_MAIN = "M0"
ASSIGNED = "assigned"
CANDIDATE = "candidate"
#: The roles the reference commits something on. A peak it gives none of these
#: is one it says nothing about (G1's third part).
REFERENCE_SPEAKS = ("M0", "iso_child", "reagent", "artifact")
#: The roles that name a source's own ion (G4, G10).
SOURCE_ROLES = ("reagent", "artifact")
#: The verdict of a peak both engines read as the same ion split into another
#: neutral and adduct (``compare_runs.verdict``).
SAME_ION_OTHER_SPLIT = "same_ion_other_split"
SAME_FORMULA = "same_formula"
#: The standard notations G11 reads (step 3.3b).
DEPROTONATION = "[M-H]-"
FORMATE = "[M+HCOO]-"
#: The carbon number of the acid G11 reads as C(n+1): the C11 acid whose
#: formate reading is a C10 product of the chamber's monoterpene, as the plan
#: owner's passes count it ("deprotonated C11 CHO readings"). Read at any n, a
#: CHO acid and a neutral lighter by CH2O2 are both committed on a quarter of
#: a bromide set's assigned intensity, which says nothing about formate.
FORMATE_ACID_CARBONS = 11
#: A list's tag for a source's own background, the contaminant family G10
#: counts as named when a list names it (step 3.3f).
BACKGROUND_TAG = "background"
#: A reagent ion counts against G10 only where its peak reaches this share of
#: the sample's base peak: the reagent pre-pass's own floor
#: (``mascope_tools.composition.reagents.DEFAULT_REAGENT_MIN_RELATIVE_INTENSITY``),
#: so a trace at the noise floor on a reagent's mass is not an ion the run
#: failed to name.
SOURCE_ION_MIN_RELATIVE = 1e-4

_ELEMENT = re.compile(r"([A-Z][a-z]?)(\d*)")


def reading(value=None, n=None) -> dict:
    """A metric's reading: its value, and the count it is a share of."""
    return {
        "value": None if value is None else _plain(value),
        "n": None if n is None else int(n),
    }


def _true(value) -> bool:
    """Whether a cell holds True: a frame cell may be a numpy bool, or NaN."""
    return isinstance(value, (bool, np.bool_)) and bool(value)


def _plain(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    return float(value)


def pct(numerator, denominator) -> float | None:
    """A share in percent to one decimal, as the log quotes it; None of nothing."""
    return (
        round(100.0 * float(numerator) / float(denominator), 1) if denominator else None
    )


def _share(mask: pd.Series) -> dict:
    """The share of rows ``mask`` holds, over all of its rows."""
    return reading(pct(int(mask.sum()), len(mask)), len(mask))


def element_counts(formula) -> dict[str, int]:
    """Element counts of a formula; an isotope's mass number is dropped."""
    if not isinstance(formula, str) or not formula:
        return {}
    counts: dict[str, int] = {}
    for element, n in _ELEMENT.findall(re.sub(r"\[\d+|\]|\^", "", formula)):
        counts[element] = counts.get(element, 0) + (int(n) if n else 1)
    return counts


def double_bond_equivalents(counts: Mapping[str, int]) -> float:
    """Rings plus double bonds, with the halogens monovalent and Si tetravalent."""
    tetravalent = counts.get("C", 0) + counts.get("Si", 0)
    monovalent = sum(counts.get(e, 0) for e in ("H", "F", "Cl", "Br", "I"))
    trivalent = counts.get("N", 0) + counts.get("P", 0)
    return tetravalent - monovalent / 2 + trivalent / 2 + 1


def _column(frame: pd.DataFrame, name: str, default=None) -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series(default, index=frame.index, dtype=object)


def with_reference(frame: pd.DataFrame) -> pd.DataFrame:
    """The rows of the samples that carry a reference run."""
    if "reference" not in frame.columns:
        return frame
    return frame[frame["reference"].fillna(False).astype(bool)]


def _a_main(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["a_role"] == ROLE_MAIN]


def _a_assigned_main(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[(frame["a_role"] == ROLE_MAIN) & (frame["a_tier"] == ASSIGNED)]


def _a_committed(frame: pd.DataFrame) -> pd.DataFrame:
    """Engine A's committed rows: monoisotopic and isotopologue rows with a
    formula, the ones whose provenance the card reads."""
    return frame[
        frame["a_role"].isin([ROLE_MAIN, "iso_child"]) & frame["a_formula"].notna()
    ]


def _b_engine_assigned(frame: pd.DataFrame) -> pd.Series:
    """The rows the reference commits an M0 on at its own Assigned tier."""
    tier = _column(frame, "b_engine_tier").astype(str).str.lower()
    return (frame["b_role"] == ROLE_MAIN) & (tier == ASSIGNED)


# --- G1 to G8, the mass error and the reasons: the log's table -------------------


def g1_unconfirmed(frame: pd.DataFrame) -> dict:
    """G1: "assigned" rows the reference does not confirm.

    The share of engine A's assigned monoisotopic rows on which the reference
    does not commit the same formula through the same adduct, over the samples
    that carry a reference run. A reading from stage 3 on (decision 27).
    """
    ref = with_reference(frame)
    if ref.empty:
        return reading()
    mine = _a_assigned_main(ref)
    return _share(mine["verdict"] != SAME_FORMULA)


def g1_parts(frame: pd.DataFrame) -> dict[str, dict]:
    """G1's three parts, which sum to it: contradicts, same ion other split, silent.

    Over engine A's assigned monoisotopic rows: the reference reads the peak as
    another ion, as another compound's isotopologue, or as reagent or artifact
    (contradicts); it reads the same ion as another neutral and adduct, which no
    spectrum can separate (the split); or it commits nothing on the peak at all.
    """
    ref = with_reference(frame)
    if ref.empty:
        return {key: reading() for key in ("contradicts", "split", "silent")}
    mine = _a_assigned_main(ref)
    silent = ~mine["b_role"].isin(REFERENCE_SPEAKS)
    split = mine["verdict"] == SAME_ION_OTHER_SPLIT
    agree = mine["verdict"] == SAME_FORMULA
    return {
        "contradicts": _share(~silent & ~split & ~agree),
        "split": _share(split),
        "silent": _share(silent),
    }


def g1_conditioned(frame: pd.DataFrame) -> dict:
    """G1 conditioned: G1 over the assigned rows the reference commits an M0 on.

    Decision 14: the unconfirmed share of engine A's assigned monoisotopic rows
    on the peaks where the reference commits an analyte, so its silence is not
    counted as disagreement; the same-ion splits stay in the count.
    """
    ref = with_reference(frame)
    if ref.empty:
        return reading()
    mine = _a_assigned_main(ref)
    mine = mine[mine["b_role"] == ROLE_MAIN]
    return _share(mine["verdict"] != SAME_FORMULA)


def g2_recovered(frame: pd.DataFrame) -> dict[str, dict]:
    """G2: reference Assigned peaks recovered: same formula / same ion.

    Of the peaks the reference commits an M0 on at its own Assigned tier, the
    share engine A commits the same formula on, and the share it reads as the
    same ion (the same formula, or the same ion split into another neutral and
    adduct).
    """
    ref = with_reference(frame)
    if ref.empty:
        return {"formula": reading(), "ion": reading()}
    theirs = ref[_b_engine_assigned(ref)]
    return {
        "formula": _share(theirs["verdict"] == SAME_FORMULA),
        "ion": _share(theirs["verdict"].isin([SAME_FORMULA, SAME_ION_OTHER_SPLIT])),
    }


def g3_nitrogen_rich(frame: pd.DataFrame) -> dict:
    """G3: committed formulas with N >= 5.

    The share of engine A's committed monoisotopic formulas that carry carbon
    and five or more nitrogens.
    """
    main = _a_main(frame)
    organic = main[main["a_C"] > 0]
    return _share(organic["a_N"] >= 5)


def g3_carbon_free(frame: pd.DataFrame, *, untargeted_only: bool = False) -> dict:
    """G3: carbon-free formulas ("0 off the allowlist").

    The count of engine A's committed monoisotopic formulas without carbon. The
    curated lists are the allowlist, so the target reads the ones the
    untargeted stage wrote (``untargeted_only``); the count over every source is
    reported beside it.
    """
    main = _a_main(frame)
    carbon_free = (main["a_C"] == 0) & main["a_formula"].notna()
    if untargeted_only:
        carbon_free &= _column(main, "a_source") == "untargeted"
    return reading(int(carbon_free.sum()), len(main))


def g4_reagent_agreement(frame: pd.DataFrame, *, naming_an_ion: bool = False) -> dict:
    """G4: reference reagent peaks labelled reagent or artifact.

    Of the peaks the reference calls reagent, the share engine A parks as
    reagent or artifact. G4a (``naming_an_ion``): of those, the ones whose
    reference row names an ion.
    """
    ref = with_reference(frame)
    if ref.empty:
        return reading()
    theirs = ref[ref["b_role"] == "reagent"]
    if naming_an_ion:
        ion = _column(theirs, "b_ion")
        theirs = theirs[ion.notna() & (ion.astype(str).str.strip() != "")]
    return _share(theirs["a_role"].isin(SOURCE_ROLES))


def g5_unsearched(frame: pd.DataFrame) -> dict:
    """G5: reference Assigned peaks never searched.

    The count of peaks the reference commits an M0 on at its own Assigned tier
    that engine A's untargeted stage was never offered: past its peak cap, or
    under its intensity threshold. A peak a pre-pass or the curated stage
    explained was looked at, and is not one.
    """
    ref = with_reference(frame)
    if ref.empty:
        return reading()
    theirs = _b_engine_assigned(ref)
    unsearched = _column(ref, "a_unsearched", False).fillna(False).astype(bool)
    return reading(int((theirs & unsearched).sum()), int(theirs.sum()))


def g6_main_on_isotopologue(
    frame: pd.DataFrame, *, assigned_only: bool = False
) -> dict:
    """G6: main peaks on reference isotopologues.

    The count of peaks engine A commits a monoisotopic row on that the
    reference reads as another compound's isotopologue; at the assigned tier
    only with ``assigned_only``, the reading the stage 2 target is set on.
    """
    ref = with_reference(frame)
    if ref.empty:
        return reading()
    mine = _a_assigned_main(ref) if assigned_only else _a_main(ref)
    return reading(int((mine["b_role"] == "iso_child").sum()), len(mine))


def g7_uncorroborated_beyond_3(frame: pd.DataFrame) -> dict:
    """G7: uncorroborated commits beyond 3 sigma.

    The count of engine A's assigned monoisotopic rows whose mass error sits
    more than three of the run's fitted widths from its fitted centre
    (``mass_z``) with nothing but the mass fit behind them: the mass gate names
    no corroboration (``provenance.mass_gate.corroborated_by``). A row whose
    provenance was not read counts as uncorroborated.
    """
    mine = _a_assigned_main(frame)
    if "a_mass_gate_corroborated" not in mine.columns:
        return reading()
    z = pd.to_numeric(_column(mine, "a_mass_z"), errors="coerce").abs()
    beyond = z > 3
    corroborated = mine["a_mass_gate_corroborated"].map(_true)
    return reading(int((beyond & ~corroborated).sum()), len(mine))


def g8_ownerless(frame: pd.DataFrame) -> dict:
    """G8: untargeted isotopologue rows without an owner.

    The count of engine A's isotopologue rows from the untargeted stage that
    name no monoisotopic row they belong to (step 1.5's coherence count).
    """
    ownerless = _column(frame, "a_ownerless", False).map(_true)
    iso = frame["a_role"] == "iso_child"
    untargeted = _column(frame, "a_source") == "untargeted"
    return reading(int((ownerless & untargeted).sum()), int((iso & untargeted).sum()))


def mass_error_mad(frame: pd.DataFrame) -> dict:
    """Mass error of committed peaks, MAD.

    The median absolute deviation from their median of the mass errors of
    engine A's committed monoisotopic rows, in ppm, to three decimals.
    """
    errors = pd.to_numeric(_a_main(frame)["a_ppm"], errors="coerce").dropna()
    if errors.empty:
        return reading(None, 0)
    mad = (errors - errors.median()).abs().median()
    return reading(round(float(mad), 3), len(errors))


def reason_coverage(frame: pd.DataFrame) -> dict:
    """Every committed row carries tier reasons.

    The share of engine A's committed rows, monoisotopic and isotopologue rows
    with a formula, whose provenance carries at least one tier reason.
    """
    if "a_reasons" not in frame.columns:
        return reading()
    committed = _a_committed(frame)
    read = committed[committed["a_reasons"].notna()]
    if read.empty:
        return reading(None, 0)
    return _share(read["a_reasons"].map(_true))


# --- G9 to G14: the plan's stage 3 ------------------------------------------------


def implausible(counts: Mapping[str, int], *, sulfur_free: bool) -> bool:
    """Whether a neutral is one G9 counts: N >= 2 with O < 2N, sulfur in a
    sulfur-free system, H/C above 2.4, or a negative double-bond equivalent."""
    nitrogen, oxygen = counts.get("N", 0), counts.get("O", 0)
    carbon = counts.get("C", 0)
    return bool(
        (nitrogen >= 2 and oxygen < 2 * nitrogen)
        or (sulfur_free and counts.get("S", 0) > 0)
        or (carbon > 0 and counts.get("H", 0) / carbon > 2.4)
        or double_bond_equivalents(counts) < 0
    )


def g9_implausible(frame: pd.DataFrame) -> dict:
    """G9: implausible assigned neutrals.

    The share of assigned M0 neutrals that carry sulfur in a sulfur-free
    system, or two or more nitrogens with fewer than two oxygens per nitrogen,
    or H/C above 2.4, or a negative double-bond equivalent. Whether a set's
    system is sulfur-free is the set's (``sulfur_free``).
    """
    mine = _a_assigned_main(frame)
    mine = mine[mine["a_formula"].notna()]
    sulfur_free = _column(mine, "sulfur_free", False).map(_true)
    flags = [
        implausible(element_counts(formula), sulfur_free=free)
        for formula, free in zip(mine["a_formula"], sulfur_free)
    ]
    return _share(pd.Series(flags, index=mine.index, dtype=bool))


def source_ion_peaks(
    peaks: pd.DataFrame, ions: Iterable[float], ppm: float
) -> pd.DataFrame:
    """The peaks a source's ions sit on: the brightest peak within ``ppm`` of
    each ion's m/z that reaches :data:`SOURCE_ION_MIN_RELATIVE` of the base peak.

    :param peaks: One sample's rows, with ``mz`` and ``intensity``.
    :param ions: The ions' m/z.
    :param ppm: The run's match window.
    :return: The rows those ions sit on, each once.
    """
    if peaks.empty:
        return peaks
    intensity = peaks["intensity"].astype(float)
    bright = peaks[intensity >= intensity.max() * SOURCE_ION_MIN_RELATIVE]
    mz = bright["mz"].astype(float).to_numpy()
    taken: list = []
    for ion in ions:
        near = np.flatnonzero(np.abs(mz - ion) <= ion * ppm * 1e-6)
        if near.size:
            best = bright.iloc[near]["intensity"].astype(float).idxmax()
            if best not in taken:
                taken.append(best)
    return peaks.loc[taken]


def profile_source_ions(config: Mapping) -> tuple[list[float], float | None]:
    """The m/z of the source ions step 3.3's list holds for the profile a run
    resolved (``mascope_tools.composition.reagents.reagent_library``), every
    family of it, and the run's match window in ppm.

    :param config: The run's stored config.
    """
    profile = config.get("resolved_profile") or {}
    name = profile.get("profile")
    ions = [cluster.mz for cluster in reagent_library(name)] if name else []
    return ions, profile.get("mz_precision_ppm")


def _named_by_a_list(row) -> bool:
    listing = row.get("a_listing")
    return (
        row.get("a_role") == ROLE_MAIN
        and isinstance(listing, Mapping)
        and BACKGROUND_TAG in (listing.get("tags") or [])
    )


def g10_source_ions_named(
    frame: pd.DataFrame, source_ions: Mapping[str, tuple[Iterable[float], float]]
) -> dict:
    """G10: source ions named.

    The share of the summed intensity on the source and reagent ions of step
    3.3's list that a run names: parked as reagent or artifact, or, for the
    contaminant family, assigned from a list (3.3f). The list is the reagent
    library of the profile each run resolved, every family of it, each ion read
    on the brightest peak within the run's match window that reaches the
    pre-pass's own floor; a row a list tagged as background names its peak.

    :param frame: The set's frame.
    :param source_ions: Per sample, the m/z of its profile's source ions and
        its run's match window in ppm. A sample absent from it, or with no
        ions, is not read.
    """
    on_ions = []
    for sample, peaks in frame.groupby("sample_item_id", sort=False):
        ions, ppm = source_ions.get(sample, ((), None))
        ions = list(ions)
        if not ions or not ppm:
            continue
        on_ions.append(source_ion_peaks(peaks, ions, float(ppm)))
    if not on_ions:
        return reading()
    rows = pd.concat(on_ions)
    if rows.empty:
        return reading(None, 0)
    named = rows["a_role"].isin(SOURCE_ROLES) | rows.apply(_named_by_a_list, axis=1)
    intensity = rows["intensity"].astype(float)
    return reading(pct(intensity[named].sum(), intensity.sum()), len(rows))


def _formate_partner(counts: Mapping[str, int]) -> str | None:
    """The neutral a C(n+1) acid's ion reads as through formate: the acid less
    formic acid, CH2O2, in the canonical spelling; None where none is left."""
    rest = dict(counts)
    for element, n in (("C", 1), ("H", 2), ("O", 2)):
        rest[element] = rest.get(element, 0) - n
    if rest.get("C", 0) < 1 or any(n < 0 for n in rest.values()):
        return None
    return "".join(f"{e}{rest[e]}" for e in sorted(rest) if rest[e] > 0)


def g11_formate_pseudo_acids(frame: pd.DataFrame, *, committed: bool = False) -> dict:
    """G11: formate pseudo-acids.

    The share of the assigned M0 intensity on assigned rows read as a C(n+1)
    acid, a CHO neutral deprotonated, whose C(n) formate reading - the same
    ion read as the acid less formic acid through ``[M+HCOO]-`` - has a partner
    assigned in the sample: that lighter neutral committed through another
    channel at the assigned tier. C(n+1) is C11 (:data:`FORMATE_ACID_CARBONS`).
    The target is 0 at assigned; with ``committed`` the share is read over the
    assigned-plus-candidate rows and their intensity, the reading the log
    quotes beside it.
    """
    tiers = [ASSIGNED, CANDIDATE] if committed else [ASSIGNED]
    main = frame[(frame["a_role"] == ROLE_MAIN) & frame["a_tier"].isin(tiers)]
    if main.empty:
        return reading(None, 0)
    partners = main[(main["a_tier"] == ASSIGNED) & (main["a_adduct"] != FORMATE)]
    held = set(zip(partners["sample_item_id"], partners["a_formula"]))
    acids = main[main["a_adduct"] == DEPROTONATION]
    pseudo = []
    for index, sample, formula in zip(
        acids.index, acids["sample_item_id"], acids["a_formula"]
    ):
        counts = element_counts(formula)
        if set(counts) != {"C", "H", "O"} or counts["C"] != FORMATE_ACID_CARBONS:
            continue
        lighter = _formate_partner(counts)
        if lighter is not None and (sample, lighter) in held:
            pseudo.append(index)
    intensity = main["intensity"].astype(float)
    return reading(pct(intensity.loc[pseudo].sum(), intensity.sum()), len(main))


def g12_lone_peaks(frame: pd.DataFrame) -> dict:
    """G12: lone peaks.

    The count of assigned M0 rows from the search with no committed
    isotopologue row, no second channel committing the neutral and no
    reference list naming it.
    """
    mine = _a_assigned_main(frame)
    mine = mine[_column(mine, "a_source") == "untargeted"]
    if mine.empty:
        return reading(0, 0)
    iso = frame[(frame["a_role"] == "iso_child") & frame["a_formula"].notna()]
    owners = set(zip(iso["sample_item_id"], _column(iso, "a_owner_id").astype(str)))
    owned = [
        (sample, str(row_id)) in owners
        for sample, row_id in zip(
            mine["sample_item_id"], _column(mine, "a_assignment_id")
        )
    ]
    channels = pd.to_numeric(_column(mine, "a_channels"), errors="coerce").fillna(1)
    listed = _column(mine, "a_listing").map(
        lambda v: isinstance(v, Mapping) and bool(v)
    )
    lone = ~pd.Series(owned, index=mine.index) & (channels < 2) & ~listed
    return reading(int(lone.sum()), len(mine))


def _recorded_count(frame: pd.DataFrame, column: str, hit) -> dict:
    """How many of engine A's assigned M0 rows a record of theirs flags, over
    the rows that carry the record; None where none does (the run predates it)."""
    mine = _a_assigned_main(frame)
    if column not in mine.columns:
        return reading()
    recorded = mine[mine[column].notna()]
    if recorded.empty:
        return reading(None, 0)
    return reading(int(recorded[column].map(hit).sum()), len(recorded))


def g13_absent_lines(frame: pd.DataFrame) -> dict:
    """G13: absent lines.

    The count of assigned M0 rows whose predicted envelope holds a line the
    detectability gate says the spectrum should have shown and the spectrum
    does not hold (step 3.4e), read off each row's ``predicted_envelope``.
    """
    return _recorded_count(frame, "a_absent_lines", lambda n: n > 0)


def g14_unseparated(frame: pd.DataFrame) -> dict:
    """G14: unseparated peaks.

    The count of assigned M0 rows whose peak the instrument did not separate
    from a neighbour and whose neutral no separated partner channel commits
    (step 3.4f), read off each row's ``peak_separation`` and
    ``cross_channel.separated_through``.
    """
    return _recorded_count(frame, "a_unseparated", _true)


# --- What the detail read gives each row -----------------------------------------


def detail_failed(frame: pd.DataFrame) -> dict:
    """Committed rows whose detail read failed.

    The count of engine A's committed rows whose provenance the card could not
    read, over every committed row. Where it is above 0, a reading of
    :data:`DETAIL_METRICS` that meets its target is not read: the rows it left
    out could hold the miss.
    """
    if "a_detail_failed" not in frame.columns:
        return reading()
    committed = _a_committed(frame)
    return reading(int(committed["a_detail_failed"].map(_true).sum()), len(committed))


def provenance_columns(provenance: Mapping | None) -> dict:
    """The card's columns from one committed row's provenance.

    :param provenance: The row's ``provenance`` (the detail read), or None.
    :return: ``a_reasons`` (whether it carries a tier reason),
        ``a_mass_gate_corroborated`` (whether the mass gate names a
        corroboration), ``a_absent_lines`` (how many asked lines of its
        predicted envelope are absent, None without the record) and
        ``a_unseparated`` (whether its peak is unseparated with no separated
        partner, None without the record).
    """
    if not isinstance(provenance, Mapping):
        return {
            "a_reasons": None,
            "a_mass_gate_corroborated": None,
            "a_absent_lines": None,
            "a_unseparated": None,
        }
    reasons = provenance.get("tier_reasons")
    gate = provenance.get("mass_gate") or {}
    envelope = provenance.get("predicted_envelope")
    separation = provenance.get("peak_separation")
    through = (provenance.get("cross_channel") or {}).get("separated_through")
    return {
        "a_reasons": isinstance(reasons, list) and len(reasons) > 0,
        "a_mass_gate_corroborated": bool(gate.get("corroborated_by")),
        "a_absent_lines": (
            sum(
                1
                for line in envelope.get("lines") or []
                if line.get("state") == "absent"
            )
            if isinstance(envelope, Mapping)
            else None
        ),
        "a_unseparated": (
            separation.get("separated") is False and not through
            if isinstance(separation, Mapping)
            else None
        ),
    }


# --- The decoy arms ----------------------------------------------------------------


#: What the card reads of each arm's pooled total (``compare_runs.decoy_rates``).
DECOY_READINGS = (
    ("held", "held_pct", "rows"),
    ("held_lt_350", "held_lt_350_pct", "rows_lt_350"),
    ("held_ge_350", "held_ge_350_pct", "rows_ge_350"),
    ("held_intensity", "held_intensity_pct", "rows"),
    ("new_ion", "new_ion_pct", "rows"),
)


def decoy_arm_readings(arm_total: Mapping) -> dict[str, dict]:
    """The decoy arm's readings: the share of its control's assigned rows (below
    and above m/z 350) and of their intensity the arm still holds at assigned,
    the rows it assigns on the control's reagent peaks, and on the adduct arm
    the share that are new ions. No target yet (decision 31).

    :param arm_total: One arm's total over the set (``compare_runs.decoy_report``'s
        ``pooled`` entry).
    """
    control = arm_total.get("control") or {}
    decoy = arm_total.get("decoy") or {}
    out = {}
    for name, rate, base in DECOY_READINGS:
        if rate in arm_total:
            out[name] = reading(arm_total[rate], control.get(base))
    out["on_control_reagent"] = reading(
        decoy.get("on_control_reagent", 0), decoy.get("rows")
    )
    return out


# --- Every metric of the card ------------------------------------------------------


#: The card's rows in order: key, label, unit. The decoy arms follow, one group
#: per arm the set reads.
METRICS = (
    ("samples", "samples read", "count"),
    ("samples_skipped", "samples skipped: no ledger of the first engine", "count"),
    ("reference_samples", "of them with a reference run", "count"),
    ("peaks", "peaks", "count"),
    ("assigned", "assigned monoisotopic rows", "count"),
    ("detail_failed", "committed rows whose detail read failed", "count"),
    ("G1", "G1 assigned rows the reference does not confirm", "%"),
    ("G1_contradicts", "  G1: the reference reads another ion or no analyte", "%"),
    ("G1_split", "  G1: the same ion, another neutral and adduct", "%"),
    ("G1_silent", "  G1: the reference commits nothing", "%"),
    ("G1_conditioned", "G1 over the rows the reference commits an M0 on", "%"),
    ("G2_formula", "G2 reference Assigned peaks recovered, same formula", "%"),
    ("G2_ion", "G2 reference Assigned peaks recovered, same ion", "%"),
    ("G3_N5", "G3 committed formulas with N >= 5", "%"),
    ("G3_carbon_free", "G3 carbon-free formulas", "count"),
    ("G3_carbon_free_untargeted", "G3 carbon-free formulas off the lists", "count"),
    ("G4", "G4 reference reagent peaks labelled reagent or artifact", "%"),
    ("G4a", "G4a of those, the ones that name an ion", "%"),
    ("G5", "G5 reference Assigned peaks never searched", "count"),
    ("G6", "G6 main peaks on reference isotopologues", "count"),
    ("G6_assigned", "G6 of them at assigned", "count"),
    ("G7", "G7 uncorroborated commits beyond 3 sigma", "count"),
    ("G8", "G8 untargeted isotopologue rows without an owner", "count"),
    ("MAD", "mass error of committed peaks, MAD", "ppm"),
    ("reasons", "committed rows carrying tier reasons", "%"),
    ("G9", "G9 implausible assigned neutrals", "%"),
    ("G10", "G10 source ions named", "%"),
    ("G11", "G11 formate pseudo-acids at assigned", "%"),
    ("G11_committed", "G11 at assigned plus candidate", "%"),
    ("G12", "G12 lone peaks", "count"),
    ("G13", "G13 absent lines", "count"),
    ("G14", "G14 unseparated peaks", "count"),
)

#: The readings that read the reference run: a set with none is not marked on
#: them, where a set whose runs predate a record is marked "not read".
REFERENCE_METRICS = frozenset(
    {
        "G1",
        "G1_contradicts",
        "G1_split",
        "G1_silent",
        "G1_conditioned",
        "G2_formula",
        "G2_ion",
        "G4",
        "G4a",
        "G5",
        "G6",
        "G6_assigned",
    }
)

#: The readings that leave out a row whose detail read failed, so that a failed
#: read can hide a miss on them. G7 is not one: it counts such a row against
#: itself.
DETAIL_METRICS = frozenset({"reasons", "G13", "G14"})

#: The labels of an arm's readings, after the arm's name.
DECOY_LABELS = {
    "held": "held at assigned",
    "held_lt_350": "held below m/z 350",
    "held_ge_350": "held from m/z 350",
    "held_intensity": "held intensity",
    "new_ion": "new ions",
    "on_control_reagent": "rows on the control's reagent peaks",
}


def decoy_key(arm: str, name: str) -> str:
    """A decoy reading's card key: ``decoy <arm> <reading>``."""
    return f"decoy {arm} {name}"


def unread_readings(skipped: int) -> dict[str, dict]:
    """The readings of a column on which no sample was read: none but the
    samples it read, none, and the ones it skipped."""
    out = {key: reading() for key, _, _ in METRICS}
    out["samples"] = reading(0)
    out["samples_skipped"] = reading(skipped)
    return out


def card_readings(
    frame: pd.DataFrame,
    *,
    skipped: int = 0,
    source_ions: Mapping[str, tuple[Iterable[float], float]] | None = None,
    decoy_arms: Mapping[str, Mapping] | None = None,
) -> dict[str, dict]:
    """Every reading of one card column, in :data:`METRICS` order, then the arms.

    :param frame: The column's frame (one set, or every set pooled).
    :param skipped: How many of the column's samples were not read: no
        completed run of engine A, or an empty ledger.
    :param source_ions: Per sample, its profile's source ions and match window
        (:func:`g10_source_ions_named`).
    :param decoy_arms: The arms' totals over the column, by arm.
    """
    samples = frame["sample_item_id"].nunique() if len(frame) else 0
    reference = (
        frame.loc[
            frame["reference"].fillna(False).astype(bool), "sample_item_id"
        ].nunique()
        if "reference" in frame.columns and len(frame)
        else 0
    )
    parts = g1_parts(frame)
    recovered = g2_recovered(frame)
    out = {
        "samples": reading(samples),
        "samples_skipped": reading(skipped),
        "reference_samples": reading(reference),
        "peaks": reading(len(frame)),
        "assigned": reading(len(_a_assigned_main(frame))),
        "detail_failed": detail_failed(frame),
        "G1": g1_unconfirmed(frame),
        "G1_contradicts": parts["contradicts"],
        "G1_split": parts["split"],
        "G1_silent": parts["silent"],
        "G1_conditioned": g1_conditioned(frame),
        "G2_formula": recovered["formula"],
        "G2_ion": recovered["ion"],
        "G3_N5": g3_nitrogen_rich(frame),
        "G3_carbon_free": g3_carbon_free(frame),
        "G3_carbon_free_untargeted": g3_carbon_free(frame, untargeted_only=True),
        "G4": g4_reagent_agreement(frame),
        "G4a": g4_reagent_agreement(frame, naming_an_ion=True),
        "G5": g5_unsearched(frame),
        "G6": g6_main_on_isotopologue(frame),
        "G6_assigned": g6_main_on_isotopologue(frame, assigned_only=True),
        "G7": g7_uncorroborated_beyond_3(frame),
        "G8": g8_ownerless(frame),
        "MAD": mass_error_mad(frame),
        "reasons": reason_coverage(frame),
        "G9": g9_implausible(frame),
        "G10": g10_source_ions_named(frame, source_ions or {}),
        "G11": g11_formate_pseudo_acids(frame),
        "G11_committed": g11_formate_pseudo_acids(frame, committed=True),
        "G12": g12_lone_peaks(frame),
        "G13": g13_absent_lines(frame),
        "G14": g14_unseparated(frame),
    }
    for arm, total in sorted((decoy_arms or {}).items()):
        for name, one in decoy_arm_readings(total).items():
            out[decoy_key(arm, name)] = one
    return out
