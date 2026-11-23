"""``period_boxes``: how the distribution shifted, day to day, week to
week, month to month.

A box per calendar period per species, a panel per species, from the
:func:`~mascope_viz.data.views.periods` view: the box from the 25th to the
75th percentile with the median across it, the whiskers from the 5th to the
95th, and no outlier marks (the data frame keeps each period's minimum and
maximum). Box conventions differ, and a reader assumes Tukey's whiskers at
1.5 times the interquartile range, so the subtitle says which these are.

- **Periods** are the display zone's days, weeks from Monday to Sunday, or
  months.
- **A thin period is hollow**: one with under half the samples of the
  species' fullest one, such as the window's first and last or a week
  with a gap, or with no sample of it at all, is drawn without its fill
  and flagged in the summary. So a species no batch of the window reads
  is thin in every period. Each species is judged on its own samples,
  those of the batches that read it, so what is drawn beside it changes
  the judgement of no period it holds samples in; it can widen the window
  by periods it holds none of, which are thin.

The summary, per species: its period medians, each with the period's
start, its ``n`` (values), ``n_samples`` (the samples that could have held
one) and whether it is ``thin``, and ``highest_median``, the period of the
largest median among those that are not thin (a thin day's median is of
the hours it holds, not of the day).
"""

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


#: The calendar periods.
PERIODS = tuple(views.PERIODS)

#: The y scales the chart takes.
SCALES = ("linear", "log")

#: How each period's box is labelled, by its start's local date.
_LABELS = {"day": "%Y-%m-%d", "week": "%Y-%m-%d", "month": "%Y-%m"}

#: The x axis' label, by period.
_AXES = {"day": "Day", "week": "Week from Monday", "month": "Month"}

#: What a hollow box means, in its legend entry.
THIN = "under half its fullest period's samples"

#: What the boxes are, for the subtitle.
BOXES = "boxes p25-p75 with the median, whiskers p5-p95, no outlier marks"


def period_boxes(
    store: CampaignStore,
    species: Any = "top:5",
    *,
    period: str = "week",
    normalise: Any = "none",
    y: str = "linear",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Figure:
    """A box per calendar period per species (see the module).

    :param species: Keys, names, formulas or ``top:N``
        (:mod:`~mascope_viz.charts.species`).
    :param period: ``day``, ``week`` or ``month``.
    :param normalise: ``none``, ``tic``, a series key, or a list of keys
        whose per-sample sum divides; passed to the view as given.
    :param y: ``linear`` or ``log``.
    :param tz: The zone whose calendar the periods are.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    :raises InvalidChart: A parameter it cannot draw, or nothing to draw in
        the window.
    """
    if period not in PERIODS:
        raise InvalidChart(f"period={period!r} is none of {', '.join(PERIODS)}")
    if y not in SCALES:
        raise InvalidChart(f"y={y!r} is none of {', '.join(SCALES)}")
    read = {"normalise": normalise, "tz": tz, "start": start, "end": end}
    with common.refused():
        resolved = resolve(store, species, **read)
    if len(resolved.species) > common.MAX_PANELS:
        raise InvalidChart(
            f"{len(resolved.species)} species are more than "
            f"{common.MAX_PANELS} panels: name fewer"
        )
    with common.refused():
        frame = views.periods(store, resolved.keys, period, **read)
    if not frame["n"].sum():
        raise InvalidChart(
            "No values to draw: no sample in the window holds a value of "
            + ", ".join(s.name for s in resolved.species)
        )
    return _figure(store, frame, resolved, period, y)


def _figure(
    store: CampaignStore,
    frame: pd.DataFrame,
    resolved: Resolved,
    period: str,
    y: str,
) -> Figure:
    attrs = dict(frame.attrs)
    zone = attrs["tz"]
    data = common.named(frame, resolved)
    data.insert(
        1,
        "label",
        pd.DatetimeIndex(data["period"]).strftime(_LABELS[period]).astype(str),
    )
    # Thin against the species' own fullest period, and thin without a
    # sample of it, also where it has none in the window
    fullest = data.groupby("key", observed=True)["n_samples"].transform("max")
    empty = data["n_samples"] == 0
    data["thin"] = (empty | (data["n_samples"] < fullest / 2)).to_numpy(dtype=bool)

    entries = []
    for s in resolved.species:
        rows = data[data["key"] == s.key]
        # A thin period's median is of the hours it holds, not of the
        # period; the fullest period is never thin
        medians = rows["median"].where(~rows["thin"]).to_numpy("float64")
        highest = (
            common.at(rows["period"].iloc[int(np.nanargmax(medians))], zone)
            if np.isfinite(medians).any()
            else None
        )
        entries.append(
            {
                **s.to_dict(),
                "periods": [
                    {
                        "start": common.at(row.period, zone),
                        "label": row.label,
                        "median": common.number(row.median),
                        "n": int(row.n),
                        "n_samples": int(row.n_samples),
                        "thin": bool(row.thin),
                    }
                    for row in rows.itertuples()
                ],
                "highest_median": highest,
                "thin_periods": [
                    common.at(t, zone) for t in rows.loc[rows["thin"], "period"]
                ],
                "presence": common.presence(rows["n"], rows["n_samples"]),
            }
        )

    label = common.value_label(store, attrs)
    panels = [
        Panel(
            "box",
            axes=Axes(x=Axis(f"{_AXES[period]} ({zone})"), y=Axis(label, y)),
            encodings={
                "x": "label",
                "q1": "p25",
                "median": "median",
                "q3": "p75",
                "lo": "p5",
                "hi": "p95",
                "group": "key",
                "name": "name",
                "hollow": "thin",
            },
            options={"hollow_label": THIN},
            title=s.name,
            where={"key": s.key},
        )
        for s in resolved.species
    ]
    columns = 1 if period == "day" else min(2, len(panels))
    return Figure(
        "period_boxes",
        "Distribution per period",
        subtitle=subtitle(common.context(attrs), extra=[BOXES]),
        panels=panels,
        summary=common.summary(
            store, attrs, resolved, entries, period=period, boxes=BOXES
        ),
        data=data,
        notes=common.notes(attrs, entries),
        columns=columns,
        context=common.context(attrs),
    )


KIND = registry.register(
    ChartKind(
        id="period_boxes",
        question="How did the distribution shift week to week, month to month?",
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
                "period",
                "text",
                "week",
                PERIODS,
                help="the calendar period of a box: weeks run Monday to Sunday",
            ),
            common.NORMALISE_PARAM,
            Param("y", "text", "linear", SCALES, help="the y scale"),
            *common.WINDOW_PARAMS,
        ),
        summary=(
            "species",
            "species[].periods",
            "species[].highest_median",
            "species[].thin_periods",
            "species[].presence",
            "requested",
            "period",
            "boxes",
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
        build=period_boxes,
    )
)
