"""Spectral-neighbourhood corroboration (assignment-confidence Phase 3, P3).

The fit score and arbitration judge each peak (and its isotope envelope) on its own. But
a real compound rarely appears as a single ion: it shows up through several **adducts**
(e.g. ``[M+H]+``, ``[M+NH4]+``, ``[M+Na]+``; ``[M-H]-``, ``[M+Br]-`` …) that co-occur in the
same spectrum. Their **co-occurrence is independent, corroborating evidence** beyond any one
peak's fit — the basis of CAMERA ([Kuhl et al. 2012][camera]) and IPA
([Del Carratore et al. 2019][ipa]). This module is the first P3 increment: for each assigned
compound, count the distinct adduct channels that independently support it and turn that into
a bounded corroboration signal the confidence layer can fold in.

Design (consistent with the rest of the confidence layer):
- **Cross-peak, competitor-blind within a compound.** It reads only *which* adducts of a
  compound were assigned, not the fit score — so it stays a separate, independently
  inspectable layer (it does not re-import the measurement).
- **Bounded and saturating.** ``corroboration = 1 - 2**-(n_adducts - 1)``: 0 for a lone
  adduct, 0.5 for two, 0.75 for three … — extra adducts help with diminishing returns.
- **Conservative.** Only *accepted* assignments (per the caller's ``accept`` predicate, e.g.
  identified/candidate tier) count; a lone adduct yields 0 (no boost, no penalty).

Intensity-consistency across adducts and in-source-fragment grouping are later P3 refinements;
this increment is the adduct-count signal, which is the largest untapped piece.

[camera]: https://pubs.acs.org/doi/10.1021/ac202450g
[ipa]: https://pubs.acs.org/doi/10.1021/acs.analchem.9b02354
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class AdductCorroboration:
    """How independently a compound is supported by co-occurring adducts."""

    compound: str
    adducts: tuple[str, ...]  # the distinct adduct channels it was assigned via
    n_adducts: int
    corroboration: float  # [0, 1): 0 for a lone adduct, rising with more


def _corroboration_score(n_adducts: int) -> float:
    """Bounded, saturating boost from the number of co-occurring adducts:
    0 (1 adduct), 0.5 (2), 0.75 (3), 0.875 (4) …"""
    if n_adducts <= 1:
        return 0.0
    return 1.0 - 2.0 ** (-(n_adducts - 1))


def adduct_corroboration(
    assignments: Iterable[Any],
    *,
    compound_key: str = "target_compound_id",
    adduct_key: str = "ionization_mechanism_id",
    accept: Callable[[Any], bool] | None = None,
) -> dict[str, AdductCorroboration]:
    """Per-compound adduct co-occurrence corroboration over a run's assignments.

    :param assignments: assignment records (mappings or objects) carrying a compound id
        and an adduct/ionization id.
    :param compound_key: field naming the compound (records with a falsy value are skipped —
        e.g. untargeted/unassigned rows have no ``target_compound_id``).
    :param adduct_key: field naming the adduct / ionization channel.
    :param accept: optional predicate to keep only confident assignments (e.g. tier in
        {identified, candidate}); default keeps all.
    :returns: ``{compound_id: AdductCorroboration}`` for every compound with >= 1 accepted
        assignment. ``corroboration`` is 0 for a compound seen via a single adduct.
    """

    def get(rec: Any, key: str):
        return rec.get(key) if isinstance(rec, dict) else getattr(rec, key, None)

    by_compound: dict[str, set[str]] = {}
    for rec in assignments:
        if accept is not None and not accept(rec):
            continue
        compound = get(rec, compound_key)
        if not compound:
            continue
        adduct = get(rec, adduct_key)
        if adduct is None:
            continue
        by_compound.setdefault(str(compound), set()).add(str(adduct))

    out: dict[str, AdductCorroboration] = {}
    for compound, adducts in by_compound.items():
        ordered = tuple(sorted(adducts))
        out[compound] = AdductCorroboration(
            compound=compound,
            adducts=ordered,
            n_adducts=len(ordered),
            corroboration=_corroboration_score(len(ordered)),
        )
    return out
