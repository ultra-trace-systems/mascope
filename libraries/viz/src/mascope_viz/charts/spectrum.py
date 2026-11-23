"""``period_spectrum``: what was there in this period, and what differs from
that one?

Sticks of the period's average spectrum on the batch-peak grid, drawn from
:func:`mascope_viz.data.views.spectrum`: each stick at its ion's m/z (the
adduct included), as high as the mean of the values detected in the
period, the tallest labelled with their neutral formula
(``docs/dev/visual_digest.md``, section 3, composition snapshots). A stick's
mean is over its detections only, so a peak seen in few samples draws at
its detected mean: its presence, values detected over the samples that
could have held one, is in the frame and the hover, and a labelled peak
seen in under half of them is marked.

Against another period (an episode against its background, a night
against a day), two panels: the two spectra mirrored, the period up and
the other down, and their difference, signed, on the diverging ramp. A
peak absent from one period counts 0 there and is marked. The windows are
contiguous; all the nights of a campaign against all its days is a mask
the view does not take.
"""

from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from . import common, registry
from .spec import Context, Figure, Panel, subtitle


#: Under this presence a labelled stick is marked.
LOW_PRESENCE = 0.5

#: Two periods whose middles lie further apart than this compare raw values
#: across a sensitivity drift.
FAR_APART = pd.Timedelta(days=1)

_SIDES = {"period": "series.1", "other": "series.2"}


def period_spectrum(
    store: CampaignStore,
    *,
    start: Any = None,
    end: Any = None,
    other_start: Any = None,
    other_end: Any = None,
    top_n: int = 10,
    normalise: Any = "none",
    tz: str | None = None,
) -> Figure:
    """A period's average spectrum, and with another period the two
    mirrored and their difference.

    :param start: The period's start; None for open.
    :param end: The period's end; None for open.
    :param other_start: The other period's start: with it or *other_end*,
        the chart compares the two.
    :param other_end: The other period's end.
    :param top_n: How many sticks of each spectrum, and of the difference,
        are labelled and listed in the summary.
    :param normalise: ``none``, ``tic``, a series key, or a list of keys
        whose sum divides.
    :param tz: The zone a time without one is in, and times are shown in.
    """
    top_n = common.check_count("top_n", top_n)
    compare = other_start is not None or other_end is not None
    frame = views.spectrum(
        store,
        (start, end),
        (other_start, other_end) if compare else None,
        normalise=normalise,
        tz=tz,
    )
    attrs = frame.attrs
    zone = attrs["tz"]
    signal = common.value_label(store, attrs, "Mean")
    frame = frame.copy()
    frame["presence"] = _presence(frame["n"], frame["n_samples"])
    if compare:
        frame["other_presence"] = _presence(frame["other_n"], frame["other_n_samples"])
        frame["only_in"] = pd.Series(
            np.where(
                frame["other_n"] == 0,
                "period",
                np.where(frame["n"] == 0, "other", None),
            ),
            dtype=object,
        )
    period_name = common.window_name(attrs["period"], zone)
    other_name = common.window_name(attrs["other"], zone) if compare else None
    notes = [
        common.ASSIGNED_ONLY,
        "A stick is the mean of the values detected in its period: a peak "
        "seen in few samples draws at its detected mean. Labelled peaks seen "
        "in under half of the samples that could have held one are marked.",
    ]
    if compare:
        notes.append(
            "A peak absent from one period counts 0 there in the difference, "
            "and is marked as only in the other."
        )
        notes += _drift_notes(attrs, zone)
    notes += common.run_notes(attrs, zone) + common.normalise_notes(attrs)
    empty = [period_name] if not (frame["n"] > 0).any() else []
    if compare and not (frame["other_n"] > 0).any():
        empty.append(other_name)
    notes = common.empty_notes(store, empty) + notes
    context = Context.from_attrs(
        dict(attrs, stat="mean", start=attrs["period"][0], end=attrs["period"][1])
    )
    if compare:
        # The two windows together, the period's alone is half of it
        text = subtitle(
            Context.from_attrs(dict(attrs, stat="mean", start=None, end=None)),
            extra=[f"{period_name} against {other_name}", "assigned peaks only"],
        )
    else:
        text = subtitle(context, extra=["assigned peaks only"])
    x_axis = {"label": "m/z (ion, adduct included)"}
    if compare:
        data = _compared(frame, top_n, period_name, other_name)
        panels = [
            Panel(
                "sticks",
                axes={"x": x_axis, "y": {"label": f"{signal}, the other below"}},
                encodings={
                    "x": "mz",
                    "y": "y",
                    "group": "side",
                    "name": "side_name",
                    "label": "label",
                    "mark": "mark",
                    "hover": "hover",
                },
                options={
                    "top_n": len(data),
                    "colours": _SIDES,
                    "mark_label": "seen in under half of the samples",
                },
                title=f"{period_name} and {other_name}",
                where={"part": "mirror"},
            ),
            Panel(
                "sticks",
                axes={"x": x_axis, "y": {"label": f"{signal}, difference"}},
                encodings={
                    "x": "mz",
                    "y": "y",
                    "label": "label",
                    "mark": "mark",
                    "hover": "hover",
                },
                options={
                    "top_n": top_n,
                    "by_sign": True,
                    "mark_label": "only in one period",
                },
                title=f"{period_name} less {other_name}",
                where={"part": "difference"},
            ),
        ]
    else:
        data = _single(frame, top_n)
        panels = [
            Panel(
                "sticks",
                axes={"x": x_axis, "y": {"label": signal}},
                encodings={
                    "x": "mz",
                    "y": "y",
                    "label": "label",
                    "mark": "mark",
                    "hover": "hover",
                },
                options={
                    "top_n": len(data),
                    "mark_label": "seen in under half of the samples",
                },
            )
        ]
    return Figure(
        "period_spectrum",
        "Difference spectrum" if compare else "Period spectrum",
        text,
        panels=panels,
        summary=_summary(frame, attrs, top_n, compare),
        data=data,
        notes=notes,
        share_x=compare,
        context=context,
    )


def _presence(n: pd.Series, n_samples: pd.Series) -> np.ndarray:
    """Values detected over samples that could have held one; NaN without
    such a sample."""
    with np.errstate(divide="ignore", invalid="ignore"):
        share = n.to_numpy("float64") / n_samples.to_numpy("float64")
    share[n_samples.to_numpy() == 0] = np.nan
    return share


def _top(values: pd.Series, n: int) -> np.ndarray:
    """The index labels of the *n* largest finite *values*, the largest
    first, a tie in m/z order (the frame's)."""
    finite = values[np.isfinite(values.to_numpy("float64"))]
    return finite.sort_values(ascending=False, kind="stable").index[:n].to_numpy()


def _hover(row: Any, presence: float, n: int, n_samples: int) -> str:
    seen = (
        f"seen in {common.percent(presence)} of the samples ({n} of {n_samples})"
        if np.isfinite(presence)
        else "no sample could hold it"
    )
    return f"{row.formula} as {row.ion_formula}, m/z {row.mz:.4f}: {seen}"


def _single(frame: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """The frame of one spectrum: a stick per key, the *top_n* tallest
    labelled."""
    labelled = set(_top(frame["value"], top_n))
    rows = [
        _row(
            row,
            part="spectrum",
            y=row.value,
            label=row.formula if row.Index in labelled else None,
            mark=bool(row.Index in labelled and row.presence < LOW_PRESENCE),
            hover=_hover(row, row.presence, row.n, row.n_samples),
        )
        for row in frame.itertuples()
    ]
    return _frame(rows, _SINGLE)


def _compared(
    frame: pd.DataFrame, top_n: int, period_name: str, other_name: str
) -> pd.DataFrame:
    """The frame of two spectra: each key's stick up in the period and down
    in the other, where it has a value, the *top_n* tallest of each side
    labelled; and its difference, of which the panel labels the *top_n*
    largest either way."""
    up = set(_top(frame["value"].where(frame["n"] > 0), top_n))
    down = set(_top(frame["other_value"].where(frame["other_n"] > 0), top_n))
    rows = []
    for row in frame.itertuples():
        if row.n > 0:
            on = row.Index in up
            rows.append(
                _row(
                    row,
                    part="mirror",
                    side="period",
                    side_name=period_name,
                    y=row.value,
                    label=row.formula if on else None,
                    mark=bool(on and row.presence < LOW_PRESENCE),
                    hover=_hover(row, row.presence, row.n, row.n_samples),
                )
            )
        if row.other_n > 0:
            on = row.Index in down
            rows.append(
                _row(
                    row,
                    part="mirror",
                    side="other",
                    side_name=other_name,
                    y=-row.other_value,
                    label=row.formula if on else None,
                    mark=bool(on and row.other_presence < LOW_PRESENCE),
                    hover=_hover(
                        row, row.other_presence, row.other_n, row.other_n_samples
                    ),
                )
            )
    for row in frame.itertuples():
        if row.only_in is None:
            ratio = f"ratio {row.ratio:.3g}"
        else:
            ratio = "only in " + (
                "this period" if row.only_in == "period" else "the other"
            )
        rows.append(
            _row(
                row,
                part="difference",
                y=row.diff,
                label=row.formula,
                mark=row.only_in is not None,
                hover=(
                    f"{row.formula} as {row.ion_formula}, m/z {row.mz:.4f}: "
                    f"difference {row.diff:.4g}, {ratio}"
                ),
            )
        )
    return _frame(rows, list(_COLUMNS))


#: The key's own columns a row of the chart's frame carries.
_OWN = (
    "key",
    "name",
    "formula",
    "ion_formula",
    "mz",
    "value",
    "n",
    "n_samples",
    "presence",
    "other_value",
    "other_n",
    "other_n_samples",
    "other_presence",
    "diff",
    "ratio",
    "only_in",
)


def _row(row: Any, **fields: Any) -> dict[str, Any]:
    """A row of the chart's frame: the key's own columns, then *fields*."""
    own = {column: getattr(row, column) for column in _OWN if hasattr(row, column)}
    return {**own, **fields}


#: The chart frame's columns, in order, and their types.
_COLUMNS = {
    "part": "text",
    "side": "text",
    "side_name": "text",
    "key": "text",
    "name": "text",
    "formula": "text",
    "ion_formula": "text",
    "mz": "float64",
    "y": "float64",
    "label": "text",
    "mark": "bool",
    "hover": "text",
    "value": "float64",
    "n": "int64",
    "n_samples": "int64",
    "presence": "float64",
    "other_value": "float64",
    "other_n": "int64",
    "other_n_samples": "int64",
    "other_presence": "float64",
    "diff": "float64",
    "ratio": "float64",
    "only_in": "text",
}

#: The columns of one spectrum's frame.
_SINGLE = [
    c
    for c in _COLUMNS
    if c not in ("side", "side_name", "only_in", "diff", "ratio")
    and not c.startswith("other")
]


def _frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    """*rows* as a frame of *columns*, each of its type, a missing text
    None."""
    out = {}
    for column in columns:
        values = [r.get(column) for r in rows]
        kind = _COLUMNS[column]
        if kind == "text":
            out[column] = pd.Series([common.text(v) for v in values], dtype=object)
        else:
            out[column] = pd.Series(values, dtype=kind)
    return pd.DataFrame(out)


def _drift_notes(attrs: dict[str, Any], zone: str) -> list[str]:
    """A note when raw values of two periods far apart are compared: the
    instrument's sensitivity drifts between them, and the drift reads as a
    difference."""
    if attrs.get("normalise", "none") != "none":
        return []
    drawn = attrs.get("period_drawn"), attrs.get("other_drawn")
    if not all(drawn):
        return []
    middles = [
        pd.Timestamp(first) + (pd.Timestamp(last) - pd.Timestamp(first)) / 2
        for first, last in drawn
    ]
    apart = abs(middles[0] - middles[1])
    if apart <= FAR_APART:
        return []
    days = apart / pd.Timedelta(days=1)
    return [
        f"Raw values of two periods {days:.1f} days apart: a drift in the "
        "instrument's sensitivity between them reads as a difference. "
        "Normalise by the reagent ions to take it out."
    ]


def _summary(
    frame: pd.DataFrame, attrs: dict[str, Any], top_n: int, compare: bool
) -> dict[str, Any]:
    zone = attrs["tz"]

    def window(name: str) -> list[str | None] | None:
        bounds = attrs.get(name)
        if bounds is None:
            return None
        return [common.iso(bound, zone) for bound in bounds]

    def entry(row: Any, *columns: str) -> dict[str, Any]:
        out = {
            "species": common.species(row.key, row.name, row.formula),
            "ion_formula": common.text(row.ion_formula),
            "mz": float(row.mz),
        }
        for column in columns:
            value = getattr(row, column)
            out[column] = (
                common.text(value) if column == "only_in" else common.number(value)
            )
        return out

    seen = frame[frame["n"] > 0]
    top = seen.loc[_top(seen["value"], top_n)]
    period = attrs["period"]
    summary = {
        # The window is the period's, as the figure's context says
        **common.face(dict(attrs, start=period[0], end=period[1]), zone),
        **common.normaliser_fields(attrs),
        "stat": "mean",
        "period": window("period"),
        "other": window("other"),
        "top_n": int(top_n),
        "n_peaks": len(seen),
        "top": [entry(r, "value", "presence") for r in top.itertuples()],
    }
    if compare:
        other = frame[frame["other_n"] > 0]
        summary["other_n_peaks"] = len(other)
        summary["other_top"] = [
            entry(r, "other_value", "other_presence")
            for r in other.loc[_top(other["other_value"], top_n)].itertuples()
        ]
        columns = (
            "value",
            "presence",
            "other_value",
            "other_presence",
            "diff",
            "ratio",
            "only_in",
        )
        up = frame[frame["diff"] > 0]
        down = frame[frame["diff"] < 0]
        summary["increases"] = [
            entry(r, *columns) for r in up.loc[_top(up["diff"], top_n)].itertuples()
        ]
        summary["decreases"] = [
            entry(r, *columns)
            for r in down.loc[_top(-down["diff"], top_n)].itertuples()
        ]
    return summary


KIND = registry.register(
    registry.ChartKind(
        id="period_spectrum",
        question="What was there in this period, and what differs from that one?",
        tier=1,
        series="every formula key with an m/z (the assigned batch peaks)",
        params=(
            registry.Param("start", "time", None, help="the period's start"),
            registry.Param("end", "time", None, help="the period's end"),
            registry.Param(
                "other_start",
                "time",
                None,
                help="the other period's start: with it or other_end, a comparison",
            ),
            registry.Param("other_end", "time", None, help="the other period's end"),
            registry.Param(
                "top_n", "int", 10, help="the sticks labelled and listed per side"
            ),
            common.NORMALISE_PARAM,
            registry.Param("tz", "text", None, help="the zone times are in"),
        ),
        summary=(
            *common.FACE_FIELDS,
            *common.NORMALISER_FIELDS,
            "stat",
            "period",
            "other",
            "top_n",
            "n_peaks",
            "top",
            "other_n_peaks",
            "other_top",
            "increases",
            "decreases",
        ),
        build=period_spectrum,
    )
)
