"""``top_species``: what was brightest, what changed most, what is new?
(``docs/dev/visual_digest.md``, section 3.)

Three tables in one figure: the species by median intensity, by the ratio of
the last period to the first, and those present in the last period and
absent before it. The ratio compares two calendar periods only, so the
subtitle names them. A species held as a target ion and as an assigned
formula of the same formula and mechanism is listed once, as the ion. When
the engine run changes in the last period, a formula new in it may be an
earlier peak read differently: its row is marked.
"""

from collections.abc import Sequence
from typing import Any

import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from . import registry
from .common import (
    BELOW_DETECTION,
    NORMALISE_PARAM,
    WINDOW_PARAMS,
    face,
    moment,
    normalise_notes,
    run_notes,
)
from .registry import ChartKind, Param
from .spec import Context, Figure, Panel, subtitle


#: The three lists, in the order the figure shows them, and their titles.
LISTS = {
    "median": "Brightest: by median",
    "ratio": "Biggest changers: last period over the first",
    "new": "New: in the last period, absent before",
}

#: What a marked row means.
REREAD = "may be an earlier peak read differently: the engine run changes"

#: The columns each list's table shows.
_COLUMNS = {
    "median": ["rank", "name", "formula", "ion_formula", "median", "presence"],
    "ratio": [
        "rank",
        "name",
        "formula",
        "ion_formula",
        "first",
        "last",
        "ratio",
        "presence",
    ],
    "new": ["rank", "name", "formula", "ion_formula", "last", "presence"],
}


def distinct_keys(store: CampaignStore, keys: Sequence[str] | None) -> list[str]:
    """*keys* (every key of the store when None) with a formula key left
    out where a target ion of the same formula and mechanism is among them:
    one species, listed once, as the ion."""
    species = store.table("species")
    if keys is None:
        keys = sorted(species["series_key"].astype(str))
    keys = [keys] if isinstance(keys, str) else list(keys)
    rows = species.set_index(species["series_key"].astype(str))
    ions = {
        (rows.at[key, "formula"], rows.at[key, "mechanism"])
        for key in keys
        if key.startswith("ion:") and key in rows.index
    }
    return [
        key
        for key in keys
        if not (
            key.startswith("formula:")
            and key in rows.index
            and (rows.at[key, "formula"], rows.at[key, "mechanism"]) in ions
        )
    ]


#: A calendar period's length, on the wall clock: a week across a clock
#: change is not 168 hours.
_PERIOD_LENGTH = {
    "day": pd.DateOffset(days=1),
    "week": pd.DateOffset(weeks=1),
    "month": pd.DateOffset(months=1),
}


def _period_end(start: pd.Timestamp, period: str) -> pd.Timestamp:
    """The end of the calendar *period* that starts at *start*."""
    return start + _PERIOD_LENGTH[period]


def top_species(
    store: CampaignStore,
    *,
    n: int = 10,
    period: str = "week",
    keys: Sequence[str] | None = None,
    normalise: Any = "none",
    min_present: float = 0.5,
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Figure:
    """The brightest species, the biggest changers and the new ones.

    :param n: How many in each list.
    :param period: The calendar periods the ratio and the new list compare:
        ``day``, ``week`` or ``month``.
    :param keys: The series keys to rank; every key of the store when None.
    :param normalise: What the values are divided by (the views'
        ``normalise``).
    :param min_present: Rank only the keys present in at least this share
        of the samples compared (:func:`~mascope_viz.data.views.top`).
    :param tz: The zone the periods are in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    """
    keys = distinct_keys(store, keys)
    frames = {
        by: views.top(
            store,
            n,
            by,
            keys=keys,
            period=period,
            normalise=normalise,
            min_present=min_present,
            tz=tz,
            start=start,
            end=end,
        )
        for by in LISTS
    }
    attrs = dict(frames["ratio"].attrs)
    zone = attrs["tz"]
    first = moment(attrs.get("first_period"), zone)
    last = moment(attrs.get("last_period"), zone)
    # A run change in the last period: a formula first seen in it may be
    # an earlier peak the new run reads as another formula
    changed = [
        change
        for change in attrs.get("run_changes") or []
        if last is not None
        and last <= moment(change["at"], zone) < _period_end(last, period)
    ]
    parts = []
    for by, frame in frames.items():
        frame = frame.assign(list=by, key=frame["key"].astype(str))
        frame["reread"] = (bool(changed) and by == "new") & frame["key"].str.startswith(
            "formula:"
        ).to_numpy(dtype=bool)
        parts.append(frame)
    data = pd.concat(parts, ignore_index=True)
    for column in ("name", "formula", "ion_formula"):
        data[column] = data[column].astype(object).where(data[column].notna(), None)
    data["rank"] = data["rank"].astype("int64")
    data["reread"] = data["reread"].astype(bool)

    headers = {
        "rank": "#",
        "name": "Name",
        "formula": "Formula",
        "ion_formula": "Ion",
        "median": "Median",
        "first": "First period",
        "last": "Last period",
        "ratio": "Last / first",
        "presence": "Present",
    }
    formats = {
        "median": ".3g",
        "first": ".3g",
        "last": ".3g",
        "ratio": ".3g",
        "presence": ".0%",
    }
    panels = [
        Panel(
            "table",
            encodings={"columns": _COLUMNS[by], "mark": "reread"},
            options={
                "headers": {c: headers[c] for c in _COLUMNS[by]},
                "formats": {c: f for c, f in formats.items() if c in _COLUMNS[by]},
                "mark_label": REREAD,
            },
            title=title,
            where={"list": by},
        )
        for by, title in LISTS.items()
    ]

    def rows(by: str) -> list[dict[str, Any]]:
        frame = data[data["list"] == by]
        fields = ["rank", "key", "name", "formula", "ion_formula", "value"]
        fields += ["median", "first", "last", "ratio", "n", "n_samples", "presence"]
        out = []
        for record in frame[[*fields, "reread"]].to_dict("records"):
            if by != "new":
                record.pop("reread")
            out.append(record)
        return out

    summary = {
        # Without a window, the span of the periods compared
        **face(
            attrs,
            zone,
            start=first,
            end=None if last is None else _period_end(last, period),
        ),
        "period": period,
        "first_period": first,
        "last_period": last,
        "min_present": min_present,
        "median": rows("median"),
        "ratio": rows("ratio"),
        "new": rows("new"),
    }
    extra = []
    if first is not None and last is not None:
        extra.append(
            f"one {period} only, from {first:%Y-%m-%d}: no ratio, nothing new"
            if first == last
            else f"the {period} from {last:%Y-%m-%d} over the {period} from "
            f"{first:%Y-%m-%d}"
        )
    extra.append(f"ranked if present in {min_present:.0%} of the samples compared")
    notes = []
    if changed:
        notes.append(
            "The engine run changes in the last period: a formula new in it "
            "may be a peak read before as another formula. Its row is marked."
        )
    notes += [*normalise_notes(attrs), *run_notes(attrs, zone), BELOW_DETECTION]
    context = Context.from_attrs(attrs)
    return Figure(
        "top_species",
        "Top species",
        subtitle(context, extra=extra),
        panels=panels,
        summary=summary,
        data=data[
            [
                "list",
                "rank",
                "key",
                "name",
                "formula",
                "ion_formula",
                "value",
                "median",
                "first",
                "last",
                "ratio",
                "n",
                "n_samples",
                "presence",
                "reread",
            ]
        ],
        notes=notes,
        context=context,
    )


KIND = registry.register(
    ChartKind(
        id="top_species",
        question="What was brightest, what changed most, what is new?",
        tier=1,
        series=(
            "ion: and formula: keys; a target ion and a formula of the same "
            "species once, as the ion"
        ),
        params=(
            Param("n", "int", 10, help="how many species in each list"),
            Param(
                "period",
                "text",
                "week",
                choices=tuple(views.PERIODS),
                help="the calendar periods the ratio and the new list compare",
            ),
            Param("keys", "keys", None, help="the keys to rank; every key when none"),
            NORMALISE_PARAM,
            Param(
                "min_present",
                "number",
                0.5,
                help="rank only the keys present in at least this share of the "
                "samples compared",
            ),
            *WINDOW_PARAMS,
        ),
        summary=(
            "median",
            "ratio",
            "new",
            "period",
            "first_period",
            "last_period",
            "min_present",
            "normalise",
            "normalise_name",
            "cadence",
            "tz",
            "start",
            "end",
            "mixed_runs",
            "run_changes",
        ),
        build=top_species,
    )
)
