"""``species_series``: how these species behaved over the period.

A ``series`` panel per species, the panels one above another on a shared
time axis, each with a y axis of its own since species span orders of
magnitude; or the species overlaid in one panel. The values are the
:func:`~mascope_viz.data.views.series` view's: binned at a cadence (``auto``,
``10min``, ``1h``, ``1d``, or every sample at ``raw``), each bin's median
with its p10-p90 band where a bin holds more than one value, a gap where a
bin holds no sample or no detection.

What it draws, besides the lines:

- **Event spans**, as given (``start``, ``end``, ``label``). The chart
  computes no episodes: that is tier 3's.
- **A rule at each run change** on the panels whose values the run moves
  (a formula's key, or any key divided by a formula), with a note.
- **A point budget**: when bins times series exceed it (``budget``, 50,000
  marks by default), the series are binned at the next of 10 min, 1 h and
  1 d, and the subtitle says so. Points are never dropped silently.

The summary says the cadence drawn, the one coarsened from, the marks and
the ``source`` of the bins (the store's ``rollups`` or its ``rows``), and
per species: ``median`` and ``max`` of the plotted bin values
at the chart's cadence, ``max_time`` the start of the bin of the maximum,
``fraction_present`` the bins with a value over the bins with samples (so
an acquisition gap does not count against a species), and ``presence``,
the values over the samples that could have held one.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from ..errors import InvalidChart
from . import common, registry
from .registry import ChartKind, Param
from .spec import Axes, Axis, Figure, Panel, _span, subtitle
from .species import Resolved, resolve


#: The cadences the chart takes; ``1d`` is the view's ``1D``.
CADENCES = ("auto", "10min", "1h", "1d", "raw")

#: The cadences the point budget coarsens to, in order.
COARSER = ("10min", "1h", "1D")

#: How the species are laid out.
LAYOUTS = ("panels", "overlay")

#: The y scales the chart takes.
SCALES = ("log", "linear")

#: The most species one panel overlays: the categorical palette's
#: distinct colours.
MAX_OVERLAID = 8

#: The most panels the small multiples hold.
MAX_PANELS = common.MAX_PANELS

#: The marks a figure holds, bins times series, before it coarsens.
BUDGET = 50_000

#: The percentiles of a bin's band.
BAND = (10, 90)


def _nominal(cadence: str) -> pd.Timedelta:
    """A cadence's length, as the view names it (``1min``, ``1h``, ``1D``);
    ``raw`` none."""
    if cadence == "raw":
        return pd.Timedelta(0)
    return pd.Timedelta(cadence)


def _coarser(cadence: str) -> str | None:
    """The first of :data:`COARSER` longer than *cadence*; None past a day."""
    length = _nominal(cadence)
    return next((c for c in COARSER if pd.Timedelta(c) > length), None)


def _events(events: Any) -> list[dict[str, Any]]:
    """The event spans, as a series panel's ``spans`` take them."""
    if events is None:
        return []
    if isinstance(events, (str, Mapping)) or not isinstance(events, Sequence):
        raise InvalidChart(
            "events is no list of spans: give each as {start, end, label}"
        )
    spans = []
    for event in events:
        if not isinstance(event, Mapping) or set(event) - {"start", "end", "label"}:
            raise InvalidChart(
                f"events holds {event!r}: a span is {{start, end, label}}"
            )
        if event.get("start") is None or event.get("end") is None:
            raise InvalidChart(f"the event {event!r} needs a start and an end")
        spans.append(
            {
                "start": event["start"],
                "end": event["end"],
                "label": str(event.get("label") or ""),
                "kind": "event",
            }
        )
    return spans


def _check(layout: str, cadence: str, y: str, budget: Any) -> None:
    if layout not in LAYOUTS:
        raise InvalidChart(f"layout={layout!r} is none of {', '.join(LAYOUTS)}")
    if cadence not in CADENCES:
        raise InvalidChart(f"cadence={cadence!r} is none of {', '.join(CADENCES)}")
    if y not in SCALES:
        raise InvalidChart(f"y={y!r} is none of {', '.join(SCALES)}")
    if isinstance(budget, bool) or not isinstance(budget, (int, np.integer)):
        raise InvalidChart(f"budget={budget!r} is no whole number of marks")
    if budget < 1:
        raise InvalidChart(f"budget={budget!r} is not at least one mark")


def _binned(
    store: CampaignStore,
    keys: list[str],
    cadence: str,
    budget: int,
    **read: Any,
) -> tuple[pd.DataFrame, str | None]:
    """The series view at *cadence*, coarsened until it fits *budget*;
    and the cadence it was coarsened from, None when it was not."""
    asked = "1D" if cadence == "1d" else cadence
    frame = views.series(store, keys, cadence=asked, band=BAND, **read)
    coarsened = None
    while len(frame) > budget:
        current = frame.attrs["cadence"]
        step = _coarser(current)
        if step is None:
            raise InvalidChart(
                f"{len(keys)} series at 1 d are {len(frame):,} marks, over the "
                f"budget of {budget:,}: narrow the window, name fewer species "
                "or raise the budget"
            )
        coarsened = coarsened or current
        frame = views.series(store, keys, cadence=step, band=BAND, **read)
    return frame, coarsened


def _entry(rows: pd.DataFrame, zone: str) -> dict[str, Any]:
    """A species' numbers, from its rows of the frame."""
    value = rows["value"].to_numpy("float64")
    drawn = np.isfinite(value)
    sampled = rows["n_samples"].to_numpy() > 0
    peak = int(np.nanargmax(value)) if drawn.any() else None
    return {
        "median": common.number(np.median(value[drawn])) if drawn.any() else None,
        "max": common.number(value[peak]) if peak is not None else None,
        "max_time": common.at(rows["t"].iloc[peak], zone) if peak is not None else None,
        "n_bins": int(sampled.sum()),
        # A bin with a value has the sample that gave it
        "n_present": int(drawn.sum()),
        "fraction_present": (
            float(drawn.sum() / sampled.sum()) if sampled.any() else None
        ),
        "presence": common.presence(rows["n"], rows["n_samples"]),
    }


def species_series(
    store: CampaignStore,
    species: Any = "top:5",
    *,
    layout: str = "panels",
    cadence: str = "auto",
    normalise: Any = "none",
    y: str = "log",
    events: Any = (),
    budget: int = BUDGET,
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Figure:
    """How these species behaved over the period (see the module).

    :param species: Keys, names, formulas or ``top:N``
        (:mod:`~mascope_viz.charts.species`).
    :param layout: ``panels``, one per species (at most
        :data:`MAX_PANELS`), or ``overlay``, all in one (at most
        :data:`MAX_OVERLAID`).
    :param cadence: One of :data:`CADENCES`.
    :param normalise: ``none``, ``tic``, a series key, or a list of keys
        whose per-sample sum divides; passed to the view as given.
    :param y: ``log`` or ``linear``.
    :param events: Spans to shade, each ``{start, end, label}``; a time
        without a zone is in *tz*.
    :param budget: The marks the figure may hold, bins times series.
    :param tz: The zone the bins and the axis are in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    :raises InvalidChart: A parameter it cannot draw, more species than
        the layout holds, or nothing to draw in the window.
    """
    _check(layout, cadence, y, budget)
    spans = _events(events)
    read = {"normalise": normalise, "tz": tz, "start": start, "end": end}
    with common.refused():
        resolved = resolve(store, species, **read)
    n = len(resolved.species)
    most = MAX_PANELS if layout == "panels" else MAX_OVERLAID
    if n > most:
        raise InvalidChart(
            f"{n} species are more than {layout} hold ({most}): name fewer, or "
            + ("draw them as panels" if layout == "overlay" else "draw two charts")
        )
    with common.refused():
        frame, coarsened = _binned(store, resolved.keys, cadence, int(budget), **read)
        # The spans' times, read in the axis' zone, as the panel will
        for i, event in enumerate(spans):
            _span(event, ("time", frame.attrs["tz"]), f"events[{i}]")
    attrs = dict(frame.attrs)
    if not frame["n"].sum():
        raise InvalidChart(
            "No values to draw: no sample in the window holds a value of "
            + ", ".join(s.name for s in resolved.species)
        )
    # A band of bins of one value each, as every sample's are, is the line
    if not (frame["n"] > 1).any():
        attrs["band"] = None
    return _figure(store, frame, attrs, resolved, layout, y, spans, coarsened, budget)


def _figure(
    store: CampaignStore,
    frame: pd.DataFrame,
    attrs: dict[str, Any],
    resolved: Resolved,
    layout: str,
    y: str,
    spans: list[dict[str, Any]],
    coarsened: str | None,
    budget: int,
) -> Figure:
    zone = attrs["tz"]
    data = common.named(frame, resolved)
    entries = []
    for s in resolved.species:
        rows = data[data["key"] == s.key]
        entries.append({**s.to_dict(), **_entry(rows, zone)})

    label = common.value_label(store, attrs)
    rules = common.run_rules(attrs)
    encodings = {"x": "t", "y": "value", "group": "key", "name": "name"}
    if attrs["band"] is not None:
        encodings.update(lo="lo", hi="hi")

    def panel(keys: list[str], title: str) -> Panel:
        moved = any(common.depends_on_run(k, attrs) for k in keys)
        return Panel(
            "series",
            axes=Axes(x=Axis(common.time_label(zone)), y=Axis(label, y)),
            encodings=encodings,
            options={"spans": spans, "rules": rules if moved else []},
            title=title,
            where={"key": keys},
        )

    if layout == "overlay":
        panels = [panel(resolved.keys, "")]
    else:
        panels = [panel([s.key], s.name) for s in resolved.species]
    extra = []
    if coarsened:
        extra.append(
            f"coarsened from {coarsened} to {attrs['cadence']} to keep within "
            f"{budget:,} marks"
        )
    return Figure(
        "species_series",
        "Species over time",
        subtitle=subtitle(common.context(attrs), extra=extra),
        panels=panels,
        summary=common.summary(
            store,
            attrs,
            resolved,
            entries,
            stat=attrs.get("stat"),
            band=list(attrs["band"]) if attrs["band"] else None,
            auto=bool(attrs.get("auto")),
            source=attrs.get("source"),
            coarsened_from=coarsened,
            budget=budget,
            marks=len(frame),
            layout=layout,
        ),
        data=data,
        notes=common.notes(attrs, entries, resolved),
        columns=1 if len(panels) <= 4 else 2,
        share_x=True,
        context=common.context(attrs),
    )


KIND = registry.register(
    ChartKind(
        id="species_series",
        question="How did these species behave over the period?",
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
                "layout",
                "text",
                "panels",
                LAYOUTS,
                help=f"a panel per species (at most {MAX_PANELS}), or overlaid "
                f"(at most {MAX_OVERLAID})",
            ),
            Param(
                "cadence",
                "text",
                "auto",
                CADENCES,
                help="the bins: auto (the samples' spacing), or every sample at raw",
            ),
            common.NORMALISE_PARAM,
            Param("y", "text", "log", SCALES, help="the y scale"),
            Param(
                "events",
                "spans",
                [],
                help="spans to shade, each {start, end, label}",
            ),
            Param(
                "budget",
                "int",
                BUDGET,
                help="the marks a figure holds before it coarsens to 10min, 1h, 1d",
            ),
            *common.WINDOW_PARAMS,
        ),
        summary=(
            "species",
            "species[].polarity",
            "species[].median",
            "species[].max",
            "species[].max_time",
            "species[].fraction_present",
            "species[].n_bins",
            "species[].n_present",
            "species[].presence",
            "requested",
            "cadence",
            "stat",
            "band",
            "auto",
            "source",
            "coarsened_from",
            "marks",
            "budget",
            "layout",
            "normalise",
            "normalise_name",
            "normalise_members",
            "normalise_complete",
            "same_ion",
            "tz",
            "start",
            "end",
            "mixed_runs",
            "run_changes",
        ),
        build=species_series,
    )
)
