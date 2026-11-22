"""``diurnal``: what is the daily cycle.

The hour-of-day median of each species with its interquartile band, a
panel per species, as openair's ``timeVariation`` made the diurnal plot
standard among atmospheric scientists (Carslaw and Ropkins 2012). With
``by`` (``month``, ``season`` or ``weekday``) each panel holds a line and a
band per group, not more panels. The values are the
:func:`~mascope_viz.data.views.diurnal` view's.

- **Clock hours.** The hours are the display zone's clock hours, clock
  changes included, and the axis says the zone. The package knows no
  site's coordinates, so nothing here is solar time, and a note says so.
- **An hour is a span.** Hour ``h`` holds the samples from ``h:00`` to
  ``h:59``, so its value is drawn at ``h + 0.5``, the middle of the span,
  not at ``h``.
- **A linear y** by default: a cycle's amplitude reads on a linear axis.

The summary, per species: ``hour_max`` and ``hour_min`` (the hours of the
largest and smallest hourly median), ``amplitude`` (the largest hourly
median over the smallest), ``day_night_ratio`` (the median of the hourly
medians over the day hours divided by that over the night hours, the hours
given as clock spans echoed in the summary), ``min_n`` (the fewest values
of any hour) and ``sparse_hours`` (the hours holding under half the values
of the median hour, which a note names: an hour sampled on a few days is
not a cycle); with ``by``, the same per group under ``groups``.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from ..errors import InvalidChart
from . import common, registry
from .registry import ChartKind, Param
from .spec import Axes, Axis, Figure, Panel, subtitle
from .species import Resolved, resolve


#: What the cycle splits by; ``none`` for no split.
BY = ("none", *views.DIURNAL_BY)

#: The y scales the chart takes.
SCALES = ("linear", "log")

#: The day's and the night's hours, as clock spans ``[start, end)``: 10:00
#: to 16:00, and 22:00 to 04:00.
DAY_HOURS = (10, 16)
NIGHT_HOURS = (22, 4)

#: The note every diurnal figure prints.
CLOCK_NOTE = (
    "Hours are clock hours of {zone}, clock changes included; with no site "
    "coordinates, nothing here is solar time."
)


def hours(span: Any, what: str) -> list[int]:
    """The clock hours of a span ``(start, end)``: from ``start:00`` up to
    ``end:00``, across midnight when ``end`` is before ``start``."""
    try:
        first, last = (int(h) for h in span)
    except (TypeError, ValueError) as e:
        raise InvalidChart(f"{what}={span!r} is no pair of hours") from e
    if not (0 <= first <= 23 and 0 <= last <= 24) or first == last % 24:
        raise InvalidChart(
            f"{what}={span!r}: give two different hours, from 0 to 24, as a "
            "clock span [start, end)"
        )
    return [(first + i) % 24 for i in range((last - first) % 24)]


def _median(values: np.ndarray) -> float | None:
    """The median of the values that are finite; None without one."""
    found = values[np.isfinite(values)]
    return float(np.median(found)) if len(found) else None


def cycle(rows: pd.DataFrame, day: Sequence[int], night: Sequence[int]) -> dict:
    """A cycle's numbers, from its 24 rows of the view's frame, which are in
    hour order."""
    median = rows["value"].to_numpy("float64")
    n = rows["n"].to_numpy("int64")
    out: dict[str, Any] = {
        "hour_max": None,
        "hour_min": None,
        "amplitude": None,
        "day_night_ratio": None,
        "min_n": int(n.min()),
        "sparse_hours": [],
        "presence": common.presence(rows["n"], rows["n_samples"]),
    }
    if not np.isfinite(median).any():
        return out
    high = int(np.nanargmax(median))
    low = int(np.nanargmin(median))
    typical = float(np.median(n))
    out.update(
        hour_max=high,
        hour_min=low,
        amplitude=common.ratio(median[high], median[low]),
        day_night_ratio=common.ratio(
            _median(median[list(day)]), _median(median[list(night)])
        ),
        sparse_hours=[int(h) for h in np.flatnonzero(n < typical / 2)],
    )
    return out


def diurnal(
    store: CampaignStore,
    species: Any = "top:5",
    *,
    by: str = "none",
    normalise: Any = "none",
    y: str = "linear",
    day_hours: Any = DAY_HOURS,
    night_hours: Any = NIGHT_HOURS,
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Figure:
    """The daily cycle of each species (see the module).

    :param species: Keys, names, formulas or ``top:N``
        (:mod:`~mascope_viz.charts.species`).
    :param by: ``none``, ``month``, ``season`` or ``weekday``.
    :param normalise: ``none``, ``tic``, a series key, or a list of keys
        whose per-sample sum divides; passed to the view as given.
    :param y: ``linear`` or ``log``.
    :param day_hours: The day's clock span ``(start, end)``,
    :param night_hours: and the night's, of ``day_night_ratio``.
    :param tz: The zone whose clock hours they are.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    :raises InvalidChart: A parameter it cannot draw, or nothing to draw in
        the window.
    """
    if by not in BY:
        raise InvalidChart(f"by={by!r} is none of {', '.join(BY)}")
    if y not in SCALES:
        raise InvalidChart(f"y={y!r} is none of {', '.join(SCALES)}")
    day = hours(day_hours, "day_hours")
    night = hours(night_hours, "night_hours")
    if set(day) & set(night):
        raise InvalidChart(
            f"day_hours={list(day_hours)!r} and night_hours="
            f"{list(night_hours)!r} share an hour"
        )
    read = {"normalise": normalise, "tz": tz, "start": start, "end": end}
    resolved = resolve(store, species, **read)
    if len(resolved.species) > common.MAX_PANELS:
        raise InvalidChart(
            f"{len(resolved.species)} species are more than "
            f"{common.MAX_PANELS} panels: name fewer"
        )
    whole = views.diurnal(store, resolved.keys, **read)
    if not whole["n"].sum():
        raise InvalidChart(
            "No values to draw: no sample in the window holds a value of "
            + ", ".join(s.name for s in resolved.species)
        )
    split = by if by != "none" else None
    frame = views.diurnal(store, resolved.keys, by=split, **read) if split else whole
    return _figure(
        store, frame, whole, resolved, split, y, day, night, day_hours, night_hours
    )


def _figure(
    store: CampaignStore,
    frame: pd.DataFrame,
    whole: pd.DataFrame,
    resolved: Resolved,
    by: str | None,
    y: str,
    day: list[int],
    night: list[int],
    day_hours: Any,
    night_hours: Any,
) -> Figure:
    attrs = dict(frame.attrs)
    zone = attrs["tz"]
    data = common.named(frame, resolved)
    data.insert(data.columns.get_loc("hour") + 1, "x", data["hour"] + 0.5)

    entries = []
    sparse = []
    for s in resolved.species:
        entry = {**s.to_dict(), **cycle(whole[whole["key"] == s.key], day, night)}
        if by:
            mine = frame[frame["key"] == s.key]
            entry["groups"] = [
                {"group": str(group), **cycle(rows, day, night)}
                for group, rows in mine.groupby("group", sort=False)
            ]
        if entry["sparse_hours"]:
            sparse.append(f"{s.name} (hours {_listed(entry['sparse_hours'])})")
        entries.append(entry)

    label = common.y_label(store, attrs)
    x_axis = Axis(f"hour of day ({zone})", range=(0.0, 24.0))
    if by:
        encodings = {"x": "x", "y": "value", "group": "group", "lo": "lo", "hi": "hi"}
    else:
        encodings = {
            "x": "x",
            "y": "value",
            "group": "key",
            "name": "name",
            "lo": "lo",
            "hi": "hi",
        }
    panels = [
        Panel(
            "series",
            axes=Axes(x=x_axis, y=Axis(label, y)),
            encodings=encodings,
            title=s.name,
            where={"key": s.key},
        )
        for s in resolved.species
    ]
    notes = [CLOCK_NOTE.format(zone=zone), *common.notes(attrs, entries)]
    if sparse:
        notes.append(
            "Under half the values of the median hour, so no cycle to read "
            "there: " + "; ".join(sparse) + "."
        )
    extra = [f"by {by}"] if by else []
    return Figure(
        "diurnal",
        "Daily cycle",
        subtitle=subtitle(common.context(attrs), extra=extra),
        panels=panels,
        summary=common.summary(
            store,
            attrs,
            resolved,
            entries,
            by=by,
            stat=attrs.get("stat"),
            band=list(attrs["band"]) if attrs.get("band") else None,
            day_hours=list(day_hours),
            night_hours=list(night_hours),
            day=day,
            night=night,
        ),
        data=data,
        notes=notes,
        columns=min(3, len(panels)),
        share_x=True,
        context=common.context(attrs),
    )


def _listed(hours: Sequence[int]) -> str:
    return ", ".join(str(h) for h in hours)


KIND = registry.register(
    ChartKind(
        id="diurnal",
        question="What is the daily cycle?",
        tier=1,
        series=(
            "each species named: an ion: or formula: key, a formula under "
            "several mechanisms one series each"
        ),
        params=(
            Param(
                "species",
                "species",
                "top:5",
                help="keys, names, neutral or ion formulas, or top:N",
            ),
            Param(
                "by",
                "text",
                "none",
                BY,
                help="a line per month, season, or weekday and weekend",
            ),
            Param(
                "normalise",
                "normaliser",
                "none",
                help="none, tic, a series key, or a list of keys whose sum divides",
            ),
            Param("y", "text", "linear", SCALES, help="the y scale"),
            Param(
                "day_hours",
                "hours",
                list(DAY_HOURS),
                help="the day's clock span [start, end) of day_night_ratio",
            ),
            Param(
                "night_hours",
                "hours",
                list(NIGHT_HOURS),
                help="the night's clock span [start, end) of day_night_ratio",
            ),
            Param("tz", "text", None, help="the zone: an IANA name or instrument"),
            Param("start", "time", None, help="the window's start"),
            Param("end", "time", None, help="the window's end, excluded"),
        ),
        summary=(
            "species",
            "species[].hour_max",
            "species[].hour_min",
            "species[].amplitude",
            "species[].day_night_ratio",
            "species[].min_n",
            "species[].sparse_hours",
            "species[].presence",
            "species[].groups",
            "requested",
            "by",
            "day_hours",
            "night_hours",
            "cadence",
            "normalise",
            "normalise_name",
            "normalise_members",
            "normalise_complete",
            "tz",
            "start",
            "end",
            "mixed_runs",
            "run_changes",
        ),
        build=diurnal,
    )
)
