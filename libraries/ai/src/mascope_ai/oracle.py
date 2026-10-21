"""The oracle's answers: Mascope's scores for candidate formulas, condensed.

The model has no tool that computes a mass, a score or an isotope ratio, so
it cannot invent an assignment: candidates come from the mass query
(:class:`Candidate`) or from the caller, Mascope scores them, and the model
narrates. A formula the model suggests from chemical context goes through
the same scoring, and the answer keeps what Mascope scored apart from what
it could not (:class:`Unscored`).

Mascope answers a match with a compound -> ion -> isotopologue tree, tens of
thousands of rows for a sample's worth of candidates. That tree never
reaches a model: it is flattened into a :class:`~mascope_ai.frame.Frame`
(one row per isotopologue, :func:`tree_rows`) and condensed inside Python
to one :class:`Verdict` per candidate (:func:`condense`).
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .frame import Frame, _clip, jsonable
from .refs import Ref


#: Where a candidate came from: the caller's list, Mascope's mass query, or
#: the model's own suggestion.
ORIGINS = ("given", "mass_query", "model")

#: What Mascope's match categories say of a candidate or an ion.
STATUS = {2: "probable", 1: "possible", 0: "unmatched"}

#: Verdicts the repr lists; the rest are counted.
REPR_VERDICTS = 20


def formula_key(formula: str) -> str:
    """A formula as Mascope keys it: inner runs of white space made one
    space, the ends stripped (the server's ``norm``)."""
    return " ".join(str(formula).split())


@dataclass(frozen=True)
class Candidate:
    """A formula proposed for an m/z: a candidate, not yet a verdict.

    :ivar formula: The neutral compound's formula.
    :ivar mechanism: The ionization mechanism that makes the ion, by name.
    :ivar mechanism_id: Its ID.
    :ivar mz: The m/z of the ion's line the candidate was read at.
    :ivar error_ppm: How far the queried m/z is from it, in ppm.
    :ivar query_mz: The m/z asked about.
    :ivar unsaturation: The formula's rings plus double bonds.
    :ivar origin: Where it came from: ``mass_query`` for Mascope's.
    """

    formula: str
    mechanism: str | None
    mechanism_id: str | None
    mz: float | None
    error_ppm: float | None
    query_mz: float | None
    unsaturation: float | None = None
    origin: str = "mass_query"

    def __str__(self) -> str:
        return (
            f"{self.formula} {self.mechanism or ''} at m/z {self.mz:.5f} "
            f"({self.error_ppm:+.2f} ppm)"
            if self.mz is not None and self.error_ppm is not None
            else self.formula
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula": self.formula,
            "mechanism": self.mechanism,
            "mechanism_id": self.mechanism_id,
            "mz": self.mz,
            "error_ppm": self.error_ppm,
            "query_mz": self.query_mz,
            "unsaturation": self.unsaturation,
            "origin": self.origin,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Candidate":
        """The candidate of one record of Mascope's mass query."""
        mechanism = record.get("ionization_mechanism") or {}
        if not isinstance(mechanism, dict):
            mechanism = {"ionization_mechanism": mechanism}
        return cls(
            formula=str(record["target_compound_formula"]),
            mechanism=mechanism.get("ionization_mechanism"),
            mechanism_id=mechanism.get("ionization_mechanism_id"),
            mz=jsonable(record.get("target_isotope_mz")),
            error_ppm=jsonable(record.get("target_isotope_mz_error_ppm")),
            query_mz=jsonable(record.get("sample_peak_mz")),
            unsaturation=jsonable(record.get("target_compound_unsaturation")),
        )


class Candidates(list):
    """What ``query_by_mz`` returns: candidates, the closest first.

    A plain list of :class:`Candidate` - with how many the mass query found
    in all, which may be more than were returned.

    :ivar mz: The m/z asked about.
    :ivar tolerance_ppm: The window searched.
    :ivar total: How many candidates Mascope found.
    """

    def __init__(self, candidates, *, mz: float, tolerance_ppm: float, total: int):
        super().__init__(candidates)
        self.mz = mz
        self.tolerance_ppm = tolerance_ppm
        self.total = total

    def __repr__(self) -> str:
        head = f"{self.total} candidate(s) within {self.tolerance_ppm:g} ppm of m/z {self.mz:g}"
        if self.total > len(self):
            head += f", the closest {len(self)} shown"
        return f"Candidates({head}: {list.__repr__(self)})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mz": self.mz,
            "tolerance_ppm": self.tolerance_ppm,
            "total": self.total,
            "candidates": [candidate.to_dict() for candidate in self],
        }


@dataclass(frozen=True)
class IonVerdict:
    """Mascope's score for one ion of a candidate.

    :ivar formula: The ion's formula.
    :ivar mechanism: The ionization mechanism that makes it, by name.
    :ivar mechanism_id: Its ID.
    :ivar score: Mascope's ion score.
    :ivar status: ``probable``, ``possible`` or ``unmatched``.
    :ivar n_isotopes: The isotopologues Mascope looked for.
    :ivar peak_ids: The sample's peaks its isotopologues were found at
        (score above 0), the most abundant isotopologue first.
    """

    formula: str
    mechanism: str | None
    mechanism_id: str | None
    score: float
    status: str
    n_isotopes: int
    peak_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula": self.formula,
            "mechanism": self.mechanism,
            "mechanism_id": self.mechanism_id,
            "score": self.score,
            "status": self.status,
            "n_isotopes": self.n_isotopes,
            "peak_ids": list(self.peak_ids),
        }


@dataclass(frozen=True)
class Verdict:
    """Mascope's verdict on one candidate formula in one sample.

    Every number here is Mascope's: the toolkit picks and orders, it does
    not compute a score.

    :ivar formula: The formula, as it was asked about.
    :ivar origin: ``given``, ``mass_query`` or ``model``.
    :ivar score: Mascope's compound score: its best ion's.
    :ivar status: ``probable``, ``possible`` or ``unmatched``, from
        Mascope's match category.
    :ivar ions: Each ion Mascope scored, the best first.
    :ivar peak_ids: The peaks attributed to the candidate: those of its
        ions that matched (``possible`` or better). Empty when none did.
    :ivar intensity: The summed intensity of the peaks its ions matched.
    """

    formula: str
    origin: str
    score: float
    status: str
    ions: tuple[IonVerdict, ...]
    peak_ids: tuple[str, ...]
    intensity: float | None = None

    @property
    def best_ion(self) -> IonVerdict | None:
        return self.ions[0] if self.ions else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula": self.formula,
            "origin": self.origin,
            "score": self.score,
            "status": self.status,
            "peak_ids": list(self.peak_ids),
            "intensity": self.intensity,
            "ions": [ion.to_dict() for ion in self.ions],
        }

    def line(self) -> str:
        """The verdict on one line, as the repr lists it."""
        ion = self.best_ion
        best = f"best ion {_clip(ion.formula)} {ion.mechanism or ''}" if ion else ""
        return (
            f"{self.status:<9} {self.score:6.3f}  {_clip(self.formula)!r} "
            f"({self.origin}) {best}, {len(self.peak_ids)} peak(s)"
        )


@dataclass(frozen=True)
class Unscored:
    """A candidate Mascope gave no score: speculation, not a verdict.

    :ivar formula: The formula, as it was asked about.
    :ivar origin: ``given``, ``mass_query`` or ``model``.
    :ivar reason: Why Mascope did not score it.
    """

    formula: str
    origin: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"formula": self.formula, "origin": self.origin, "reason": self.reason}


#: Why a candidate that went in has no compound in Mascope's answer.
NOT_IN_ANSWER = (
    "Mascope returned no score for it in this sample: no ion of it could be "
    "made with the sample's ionization mechanisms, or every isotopologue was "
    "filtered out by the match parameters."
)


@dataclass(frozen=True, eq=False, repr=False)
class MatchResult:
    """Mascope's scores for candidate formulas in one sample, condensed.

    Its repr and :meth:`to_dict` are bounded: one line per verdict, never
    the tree. The tree - one row per isotopologue Mascope looked for - is
    :attr:`tree`, a Frame whose rows stay in Python.

    :ivar sample: The sample matched in.
    :ivar verdicts: One per candidate Mascope scored, the best first.
    :ivar unscored: Each candidate Mascope gave no score, and why.
    :ivar tree: The whole tree, flattened, as a Frame.
    :ivar params: The match parameters sent; None for the instrument's
        defaults, as the app uses them.
    """

    sample: Ref
    verdicts: tuple[Verdict, ...]
    unscored: tuple[Unscored, ...]
    tree: Frame
    params: dict[str, Any] | None = None

    @property
    def provenance(self) -> dict[str, Any] | None:
        """The server's provenance block, as the tree carries it."""
        return self.tree.provenance

    def verdict(self, formula: str) -> Verdict | None:
        """The verdict on *formula*, None if Mascope did not score it."""
        key = formula_key(formula)
        for verdict in self.verdicts:
            if formula_key(verdict.formula) == key:
                return verdict
        return None

    def summary(self) -> str:
        lines = [
            f"MatchResult in {self.sample}: {len(self.verdicts)} candidate(s) "
            f"scored by Mascope, {len(self.unscored)} unscored"
        ]
        lines += ["  " + verdict.line() for verdict in self.verdicts[:REPR_VERDICTS]]
        more = len(self.verdicts) - REPR_VERDICTS
        if more > 0:
            lines.append(f"  ... and {more} more (all in .verdicts)")
        for unscored in self.unscored[:REPR_VERDICTS]:
            lines.append(
                f"  UNSCORED  {_clip(unscored.formula)!r} ({unscored.origin}): "
                f"{_clip(unscored.reason, 120)}"
            )
        more = len(self.unscored) - REPR_VERDICTS
        if more > 0:
            lines.append(f"  ... and {more} more unscored (all in .unscored)")
        lines.append(
            f"  The scores are Mascope's; an unscored candidate is speculation. "
            f"The tree is Frame {self.tree.id} ({self.tree.n_rows:,} rows): "
            ".tree.df in Python."
        )
        return "\n".join(lines)

    def __repr__(self) -> str:
        return self.summary()

    __str__ = __repr__

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample": self.sample.to_dict(),
            "params": self.params,
            "verdicts": [verdict.to_dict() for verdict in self.verdicts],
            "unscored": [unscored.to_dict() for unscored in self.unscored],
            "tree": self.tree.to_dict(),
        }


# --- the tree ----------------------------------------------------------------

#: What a compound's, an ion's and an isotopologue's own keys are called in
#: the tree's rows where the levels would collide.
_LEVEL_NAMES = {
    "compound": {
        "match_score": "match_score_compound",
        "match_category": "match_category_compound",
        "sample_peak_intensity_sum": "intensity_compound",
    },
    "ion": {
        "match_score": "match_score_ion",
        "match_category": "match_category_ion",
        "sample_peak_intensity_sum": "intensity_ion",
    },
    "isotope": {
        "match_score": "match_score_isotope",
        "match_category": "match_category_isotope",
    },
}

#: Keys of the tree left out of its rows: per-ion settings, not results.
_DROPPED = {"filter_params"}


def _level(record: dict[str, Any], level: str) -> dict[str, Any]:
    names = _LEVEL_NAMES[level]
    return {
        names.get(key, key): value
        for key, value in record.items()
        if key != "children" and key not in _DROPPED
    }


def tree_rows(
    compounds: list[dict[str, Any]],
    origins: dict[str, tuple[str, str]],
    mechanisms: dict[str, str],
) -> pd.DataFrame:
    """Mascope's match tree as one row per isotopologue.

    Each row carries its compound's and its ion's columns beside its own,
    their scores and categories suffixed by level (``match_score_compound``,
    ``match_score_ion``, ``match_score_isotope``), plus the candidate as it
    was asked about (``candidate``), its ``origin``, and the ion's
    ``ionization_mechanism`` by name.

    :param compounds: The compound records of Mascope's answer.
    :param origins: The candidate's formula key -> (formula as asked, origin).
    :param mechanisms: Ionization mechanism ID -> name.
    """
    rows = []
    for compound in compounds:
        key = formula_key(compound.get("target_compound_formula", ""))
        asked, origin = origins.get(
            key, (compound.get("target_compound_formula"), "given")
        )
        top = {"candidate": asked, "origin": origin, **_level(compound, "compound")}
        for ion in compound.get("children") or [{}]:
            middle = {**top, **_level(ion, "ion")}
            middle["ionization_mechanism"] = mechanisms.get(
                middle.get("ionization_mechanism_id")
            )
            for isotope in ion.get("children") or [{}]:
                rows.append({**middle, **_level(isotope, "isotope")})
    return pd.DataFrame(rows)


# --- the verdicts ------------------------------------------------------------


def _status(category: Any) -> str:
    try:
        return STATUS.get(int(category), "unmatched")
    except (TypeError, ValueError):
        return "unmatched"


def _float(value: Any) -> float:
    value = jsonable(value)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _found(isotopes: pd.DataFrame) -> tuple[str, ...]:
    """The peaks the isotopologues were found at: score above 0, a peak
    named - Mascope's own "found peak", the rows it keeps when it saves
    matches. The most abundant isotopologue first."""
    if isotopes.empty or "sample_peak_id" not in isotopes:
        return ()
    found = isotopes[
        (pd.to_numeric(isotopes.get("match_score_isotope"), errors="coerce") > 0)
        & isotopes["sample_peak_id"].notna()
        & (isotopes["sample_peak_id"].astype(str) != "")
    ]
    if "relative_abundance" in found:
        found = found.sort_values(
            "relative_abundance", ascending=False, kind="mergesort"
        )
    peaks: list[str] = []
    for peak in found["sample_peak_id"].astype(str):
        if peak not in peaks:
            peaks.append(peak)
    return tuple(peaks)


def condense(
    tree: pd.DataFrame, asked: list[tuple[str, str]]
) -> tuple[tuple[Verdict, ...], tuple[str, ...]]:
    """One verdict per candidate in *tree*, and the formula keys it lacks.

    :param tree: :func:`tree_rows`.
    :param asked: Each candidate as ``(formula as asked, origin)``.
    :return: The verdicts, the best first (by category, then score, then
        formula), and the keys of the candidates *tree* has no rows for.
    """
    verdicts = []
    seen: set[str] = set()
    if not tree.empty:
        for candidate, rows in tree.groupby("candidate", sort=False):
            first = rows.iloc[0]
            ions = []
            for _, ion_rows in rows.groupby("target_ion_id", sort=False, dropna=False):
                ion = ion_rows.iloc[0]
                ions.append(
                    IonVerdict(
                        formula=str(ion.get("target_ion_formula") or ""),
                        mechanism=jsonable(ion.get("ionization_mechanism")),
                        mechanism_id=jsonable(ion.get("ionization_mechanism_id")),
                        score=_float(ion.get("match_score_ion")),
                        status=_status(ion.get("match_category_ion")),
                        n_isotopes=int(ion_rows["target_isotope_id"].notna().sum())
                        if "target_isotope_id" in ion_rows
                        else 0,
                        peak_ids=_found(ion_rows),
                    )
                )
            ions.sort(key=lambda ion: (-_rank(ion.status), -ion.score, ion.formula))
            attributed: list[str] = []
            for ion in ions:
                if ion.status != "unmatched":
                    attributed += [p for p in ion.peak_ids if p not in attributed]
            verdicts.append(
                Verdict(
                    formula=str(candidate),
                    origin=str(first.get("origin")),
                    score=_float(first.get("match_score_compound")),
                    status=_status(first.get("match_category_compound")),
                    ions=tuple(ions),
                    peak_ids=tuple(attributed),
                    intensity=jsonable(first.get("intensity_compound")),
                )
            )
            seen.add(formula_key(str(candidate)))
    verdicts.sort(key=lambda v: (-_rank(v.status), -v.score, v.formula))
    missing = tuple(
        formula_key(formula) for formula, _ in asked if formula_key(formula) not in seen
    )
    return tuple(verdicts), missing


def _rank(status: str) -> int:
    return {"probable": 2, "possible": 1}.get(status, 0)
