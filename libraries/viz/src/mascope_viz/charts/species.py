"""The species a chart draws: the ``species`` parameter every species chart
takes, resolved to the store's series keys.

Each item names a species as the store does, tried in this order:

- an exact series key (``ion:<target_ion_id>``,
  ``formula:<formula>|<mechanism>``), or ``tic``;
- else a literal, case-sensitive match on a species' name, its neutral
  formula or its ion formula;
- or ``top:N``: the N species with the highest median over the chart's own
  window, under its own normalisation (:func:`mascope_viz.data.views.top`),
  one per ion formula, and none that reads one of the normaliser's ions.

A formula held both as a target ion and as an assigned formula under the
same mechanism resolves to the target ion's ``ion:`` key: the collection's
identity, which survives a change of engine run, where the formula's key
does not. A formula read under several mechanisms resolves to every one of
them, a series each, the mechanism named in its legend
(``docs/dev/time_scopes.md``, decision 3). An item that matches nothing is
an error that lists the store's nearest names.

Two target ions of different neutrals can read one ion: an adduct of one
neutral is the protonated ion of another (C10H16O2 [M+NH4]+ and C10H19NO2
[M+H]+ are both C10H20NO2+), and both series are that one peak's values.
``top:N`` draws such an ion once, as the species ranked first of those that
read it (a target ion before a formula on a tie), and names the others in
:attr:`Resolved.same_ion`, which the summary and a note give. A species
named outright is drawn as asked.

Each species has the polarity it is read in: the charge its mechanism
ends in (``[M+I]-``, ``[M+H]+``), else its ion formula's. The store reads a
species in one polarity only, and a chart whose species are of both says
so, since a positive and a negative ion are two chemistries' readings.

A recipe's named list of species is such a list of items. The resolved
list goes into each chart's summary, so that a caption can cite it and a
rerun can pin it.
"""

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from ..errors import InvalidChart, UnknownSpecies


#: ``top:N``.
_TOP = re.compile(r"^top:(\d+)$")

#: How many near names an unknown species' error offers.
_NEAR = 8


@dataclass(frozen=True)
class Species:
    """One series a chart draws.

    :ivar key: Its series key.
    :ivar name: What its legend, title and hover text say: the species'
        name, with its mechanism where another species of the list shares
        its name or formula.
    :ivar formula: Its neutral formula; None for the TIC or a species the
        store gives none.
    :ivar mechanism: The mechanism it is read under; None where the store
        names none.
    :ivar polarity: ``+`` or ``-``, the charge its mechanism ends in, else
        its ion formula's; None for the TIC or where neither ends in one.
    """

    key: str
    name: str
    formula: str | None = None
    mechanism: str | None = None
    polarity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The species as a summary gives it: ``key``, ``name``,
        ``formula`` and ``polarity``."""
        return {
            "key": self.key,
            "name": self.name,
            "formula": self.formula,
            "polarity": self.polarity,
        }


def polarity(mechanism: str | None, ion_formula: str | None) -> str | None:
    """The polarity a species is read in: the charge its mechanism ends
    in, else its ion formula's (a reagent's mechanism names none); None
    where neither ends in one."""
    for text in (mechanism, ion_formula):
        if text and text.rstrip()[-1:] in ("+", "-"):
            return text.rstrip()[-1]
    return None


@dataclass(frozen=True)
class SameIon:
    """An ion ``top:N`` drew once, and the species it left out that read
    the same ion.

    :ivar key: The species drawn for it.
    :ivar ion_formula: The ion they all read.
    :ivar left_out: The others, ``{key, name, formula}`` each.
    """

    key: str
    ion_formula: str
    left_out: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "ion_formula": self.ion_formula,
            "left_out": [dict(s) for s in self.left_out],
        }


@dataclass(frozen=True)
class Resolved:
    """The species a ``species`` parameter names, in order, each once.

    :ivar species: The species, in the order the items name them.
    :ivar given: The parameter as given, as plain JSON: a text or a list
        of them.
    :ivar same_ion: The ions ``top:N`` drew once though several species
        read them, with those it left out; none drawn as asked.
    """

    species: tuple[Species, ...]
    given: Any
    same_ion: tuple[SameIon, ...] = ()

    @property
    def keys(self) -> list[str]:
        return [s.key for s in self.species]

    def name(self, key: str) -> str:
        return next(s.name for s in self.species if s.key == key)


def _items(species: Any) -> list[str]:
    """The parameter as a list of texts."""
    if isinstance(species, str):
        items = [species]
    elif isinstance(species, Sequence):
        items = list(species)
    else:
        raise InvalidChart(
            f"species={species!r} is no species: give a key, a name, a "
            "formula, top:N, or a list of them"
        )
    if not items:
        raise InvalidChart("species is an empty list: name at least one species")
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise InvalidChart(
                f"species holds {item!r}, which is no key, name or formula"
            )
    return [item.strip() for item in items]


def _table(store: CampaignStore) -> pd.DataFrame:
    """The store's species as text columns, ``None`` where it names none:
    object columns, since a text column of pandas 3 reads ``None`` as NaN,
    which is no falsy name. Blank text is none too: a store synced before
    blank names were dropped holds the server's ``""`` for a target ion
    without a compound name."""
    species = store.table("species")
    out = pd.DataFrame(index=range(len(species)))
    for column in ("series_key", "name", "formula", "ion_formula", "mechanism"):
        values = species[column].astype(object).to_numpy()
        out[column] = pd.Series([_text(v) for v in values], dtype=object)
    return out


def _text(value: Any) -> str | None:
    """*value* as text; None when missing or blank."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value)
    return text if text.strip() else None


def _identities(table: pd.DataFrame) -> dict[str, list[str]]:
    """Each formula key held by a target ion of the same formula and
    mechanism, to those ions' keys."""
    ions = table[table["series_key"].str.startswith("ion:")]
    held: dict[tuple[str, str], list[str]] = {}
    for row in ions.itertuples():
        if row.formula and row.mechanism:
            held.setdefault((row.formula, row.mechanism), []).append(row.series_key)
    formulas = table[table["series_key"].str.startswith("formula:")]
    return {
        row.series_key: held[(row.formula, row.mechanism)]
        for row in formulas.itertuples()
        if (row.formula, row.mechanism) in held
    }


def _matched(table: pd.DataFrame, item: str, identities: dict[str, list[str]]):
    """The keys *item* names by name or formula, a formula a target ion
    holds as that ion's key."""
    hit = (
        (table["name"] == item)
        | (table["formula"] == item)
        | (table["ion_formula"] == item)
    )
    keys: list[str] = []
    for key in table.loc[hit, "series_key"]:
        for resolved in identities.get(key, [key]):
            if resolved not in keys:
                keys.append(resolved)
    by_key = table.set_index("series_key")
    # Ions first, then by mechanism, so that a rerun gives the same order
    return sorted(
        keys,
        key=lambda k: (
            not k.startswith("ion:"),
            by_key.loc[k, "mechanism"] or "",
            k,
        ),
    )


def _near(table: pd.DataFrame, item: str) -> list[str]:
    """The store's keys, names and formulas closest to *item*: those equal
    to it but for case first, then the closest spellings."""
    pool = list(
        dict.fromkeys(
            str(v)
            for column in ("name", "formula", "ion_formula", "series_key")
            for v in table[column]
            if v
        )
    )
    folded = [v for v in pool if v.casefold() == item.casefold()]
    close = difflib.get_close_matches(item, pool, n=_NEAR, cutoff=0.6)
    return list(dict.fromkeys(folded + close))[:_NEAR]


def _top(
    store: CampaignStore,
    n: int,
    table: pd.DataFrame,
    identities: dict[str, list[str]],
    *,
    normalise: Any,
    tz: str | None,
    start: Any,
    end: Any,
) -> tuple[list[str], dict[str, list[str]]]:
    """The *n* species of the highest median, each once: a formula a target
    ion holds counts as that ion, the species that read one ion formula
    count once, and those that read the normaliser's own ions, which it
    divides to one or to a part of one, are left out: its members, the
    target ions that hold a member formula, and every species of a
    member's ion formula.

    :return: The keys, best first, and for each that reads an ion other
        candidates read too, those others' keys.
    """
    ion_of = dict(zip(table["series_key"], table["ion_formula"]))
    members = set(views._members(views._check_normalise(normalise)))
    own = members | {ion for m in members for ion in identities.get(m, [])}
    own_ions = {ion_of[k] for k in own if ion_of.get(k)}
    candidates = [
        key
        for key in table["series_key"]
        if key not in identities and key not in own and ion_of.get(key) not in own_ions
    ]
    if not candidates:
        raise InvalidChart(
            "top:N has no species to rank: the store holds none besides those "
            "that read the normaliser's ions"
        )
    # Every candidate ranked, since a place a second reading of an ion
    # would take goes to the next ion
    ranked = views.top(
        store,
        len(candidates),
        "median",
        keys=candidates,
        normalise=normalise,
        tz=tz,
        start=start,
        end=end,
    )
    ranked = ranked.assign(
        formula_first=~ranked["key"].astype(str).str.startswith("ion:")
    ).sort_values(["value", "formula_first", "key"], ascending=[False, True, True])
    reading: dict[str, list[str]] = {}
    for key in candidates:
        if ion_of.get(key):
            reading.setdefault(ion_of[key], []).append(key)
    kept: list[str] = []
    seen: set[str] = set()
    for key in map(str, ranked["key"]):
        ion = ion_of.get(key)
        if ion in seen:
            continue
        if ion:
            seen.add(ion)
        kept.append(key)
        if len(kept) == n:
            break
    shared = {
        key: [k for k in reading[ion_of[key]] if k != key]
        for key in kept
        if ion_of.get(key) and len(reading[ion_of[key]]) > 1
    }
    return kept, shared


def resolve(
    store: CampaignStore,
    species: Any,
    *,
    normalise: Any = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Resolved:
    """The series keys *species* names (see the module).

    :param species: A key, a name, a formula or ``top:N``, or a list of
        them.
    :param normalise: The chart's normalisation, which ``top:N`` ranks
        under.
    :param tz: The chart's zone,
    :param start: its window's start,
    :param end: and its end, which ``top:N`` ranks over.
    :raises UnknownSpecies: An item matches no species of the store.
    :raises InvalidChart: The parameter is no list of species, or
        ``top:N`` finds none with a value in the window.
    """
    items = _items(species)
    table = _table(store)
    identities = _identities(table)
    held = set(table["series_key"])
    keys: list[str] = []
    shared: dict[str, list[str]] = {}
    for item in items:
        top = _TOP.match(item)
        if top:
            n = int(top.group(1))
            if n < 1:
                raise InvalidChart(f"{item!r}: top:N takes N of at least 1")
            found, also = _top(
                store,
                n,
                table,
                identities,
                normalise=normalise,
                tz=tz,
                start=start,
                end=end,
            )
            if not found:
                raise InvalidChart(
                    f"{item!r}: no species has a value in the window to rank"
                )
            for key, others in also.items():
                shared.setdefault(key, [])
                shared[key] += [k for k in others if k not in shared[key]]
        elif item == views.TIC or item in held:
            found = [item]
        else:
            found = _matched(table, item, identities)
            if not found:
                raise UnknownSpecies(item, _near(table, item))
        keys.extend(k for k in found if k not in keys)
    given = species if isinstance(species, str) else list(items)
    return Resolved(_named(table, keys), given, _same_ion(table, keys, shared))


def _same_ion(
    table: pd.DataFrame, keys: list[str], shared: dict[str, list[str]]
) -> tuple[SameIon, ...]:
    """The ions ``top:N`` drew once, in the order drawn, each with the
    species it left out that read it: those drawn after all, named outright,
    are not left out."""
    by_key = table.drop_duplicates("series_key").set_index("series_key")
    out = []
    for key in keys:
        left = [k for k in shared.get(key, []) if k not in keys]
        if not left:
            continue
        out.append(
            SameIon(
                key,
                by_key.at[key, "ion_formula"],
                tuple(
                    {
                        "key": k,
                        "name": _label(by_key.loc[k]),
                        "formula": by_key.at[k, "formula"],
                    }
                    for k in left
                ),
            )
        )
    return tuple(out)


def _label(row: pd.Series) -> str:
    """A species outside the drawn list as a note names it: its name, else
    its formula, else its key, and its mechanism."""
    name = row["name"] or row["formula"] or row.name
    return f"{name} {row['mechanism']}" if row["mechanism"] else name


def _named(table: pd.DataFrame, keys: list[str]) -> tuple[Species, ...]:
    """The species of *keys*, each named: by its name, the mechanism added
    where another of them shares its name or formula, the key where that
    does not tell them apart."""
    by_key = table.drop_duplicates("series_key").set_index("series_key")
    rows = []
    for key in keys:
        if key == views.TIC:
            rows.append(Species(key, "TIC"))
            continue
        row = by_key.loc[key]
        rows.append(
            Species(
                key,
                row["name"] or row["formula"] or key,
                row["formula"],
                row["mechanism"],
                polarity(row["mechanism"], row["ion_formula"]),
            )
        )

    def mechanisms(i: int) -> set[str | None]:
        """The mechanisms of the species that share species *i*'s name or
        formula, its own included."""
        mine = rows[i]
        return {
            s.mechanism
            for s in rows
            if s.name == mine.name
            or (mine.formula is not None and s.formula == mine.formula)
        }

    out = []
    for i, s in enumerate(rows):
        name = s.name
        if s.mechanism and len(mechanisms(i)) > 1:
            name = f"{name} {s.mechanism}"
        out.append(Species(s.key, name, s.formula, s.mechanism, s.polarity))
    seen = [s.name for s in out]
    return tuple(
        Species(s.key, f"{s.name} ({s.key})", s.formula, s.mechanism, s.polarity)
        if seen.count(s.name) > 1
        else s
        for s in out
    )
