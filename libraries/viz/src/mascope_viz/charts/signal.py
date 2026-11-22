"""``instrument_signal``: was the ion source stable?
(``docs/dev/visual_digest.md``, section 3.)

The TIC and the reagent ions the recipe names, hourly medians with a
p10-p90 band on a log axis. With two or more reagent ions, their sum is a
further trace and a third panel draws each one's share of the sum: a share
that cycles is the reagent's, and so is a species' cycle that follows it.

The summary gives each trace's median, its drift (the last full day's
median over the first's) and its largest step (:func:`largest_step`).
"""

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from . import registry
from .common import (
    BELOW_DETECTION,
    NORMALISE_PARAM,
    TIC_LABEL,
    WINDOW_PARAMS,
    duration_text,
    face,
    normalise_notes,
    normaliser_name,
    polarities,
    pooled_note,
    run_notes,
    run_rules,
    species_refs,
    time_label,
    value_label,
)
from .registry import ChartKind, Param
from .spec import Context, Figure, Panel


#: How close to its largest a step's factor stays over the stretch whose
#: middle is the step's time (:func:`largest_step`).
_PLATEAU = 0.95


def _reagent_keys(reagents: Any) -> list[str]:
    if reagents is None:
        return []
    keys = [reagents] if isinstance(reagents, str) else list(reagents)
    if not all(isinstance(key, str) for key in keys):
        raise ValueError(f"reagents={reagents!r} is not a list of series keys")
    if views.TIC in keys:
        raise ValueError("reagents names the reagent's ions; the TIC is drawn anyway")
    if len(set(keys)) != len(keys):
        raise ValueError(f"reagents={reagents!r} names a key twice")
    return keys


def largest_step(
    times: pd.Series, values: np.ndarray, window: pd.Timedelta
) -> dict[str, Any] | None:
    """The largest step of a binned trace.

    At each bin's start, the ratio of the median of the bins' values in the
    *window* after it to the median of the bins one *window* earlier, the
    bins of the window before it (each paired with the bin nearest to one
    window before it, within half a bin). Only the pairs of bins that both hold a
    value count, and only where more than half the window's bins pair up:
    so each side holds the same hours of the day, a window of a whole day
    cancels a diurnal cycle even beside a gap, and a stretch without
    samples, such as a day acquired but filed nowhere, compares nothing
    across it. The largest step is the one furthest from 1, up or down. A
    median ratio holds nearly a window either side of a step, so the step's
    time is the one, at most a window either side and with the ratio's log
    within 5 % of its largest, where the ratio of the same pairs' means is
    furthest from 1: a mean moves with every bin a step reaches, and is
    furthest at the step itself.

    :param times: The bins' starts, oldest first, one row per bin.
    :param values: Their values; NaN for a bin without one.
    :return: ``at``, ``factor`` and ``window`` (its text); None when no time
        has enough pairs.
    """
    ns = pd.DatetimeIndex(times).as_unit("ns").asi8
    values = np.asarray(values, dtype="float64")
    if len(ns) < 2:
        return None
    width = window.value
    step = float(np.median(np.diff(ns)))
    need = width / step / 2
    earlier = _one_window_earlier(ns, values, width, step)
    paired = np.isfinite(values) & np.isfinite(earlier)
    hi = np.searchsorted(ns, ns + width, side="left")
    log_ratio = np.full(len(ns), np.nan)
    log_means = np.full(len(ns), np.nan)
    for i in range(len(ns)):
        both = paired[i : hi[i]]
        if both.sum() <= need:
            continue
        after = values[i : hi[i]][both]
        before = earlier[i : hi[i]][both]
        if np.median(before) > 0 and np.median(after) > 0:
            log_ratio[i] = np.log(np.median(after) / np.median(before))
            log_means[i] = np.log(after.mean() / before.mean())
    size = np.abs(log_ratio)
    if not np.isfinite(size).any():
        return None
    best = int(np.nanargmax(size))
    held = size >= _PLATEAU * size[best]
    first = last = best
    while first > 0 and held[first - 1] and ns[best] - ns[first - 1] <= width:
        first -= 1
    while last < len(ns) - 1 and held[last + 1] and ns[last + 1] - ns[best] <= width:
        last += 1
    at = first + int(np.nanargmax(np.abs(log_means[first : last + 1])))
    return {
        "at": pd.Timestamp(times.iloc[at]),
        "factor": float(np.exp(log_ratio[at])),
        "window": duration_text(window),
    }


def _one_window_earlier(
    ns: np.ndarray, values: np.ndarray, width: int, step: float
) -> np.ndarray:
    """Each bin's partner one *width* earlier (*ns* sorted, as
    :func:`largest_step` takes them): the value of the bin nearest
    to that time, if it lies within half a bin (*step*) of it and before the
    bin itself, else NaN. Nearest, not exact, so bins that do not divide the
    window (7-minute bins, irregular raw samples, local days across a clock
    change) still pair."""
    target = ns - width
    right = np.clip(np.searchsorted(ns, target), 0, len(ns) - 1)
    left = np.clip(right - 1, 0, len(ns) - 1)
    nearest = np.where(
        np.abs(ns[right] - target) < np.abs(ns[left] - target), right, left
    )
    near = (np.abs(ns[nearest] - target) <= step / 2) & (ns[nearest] < ns)
    return np.where(near, values[nearest], np.nan)


def drift(times: pd.Series, values: np.ndarray) -> float | None:
    """The last full day's median of a binned trace over its first full
    day's: a day whose bins all lie in the trace's span (in the times'
    zone, so a local day), with more than half of them holding a value.
    None without two such days."""
    t = pd.DatetimeIndex(times).as_unit("ns")
    if len(t) < 2:
        return None
    values = np.asarray(values, dtype="float64")
    step = pd.Timedelta(float(np.median(np.diff(t.asi8))), unit="ns")
    end = t[-1] + step
    days = t.normalize()
    medians = []
    for day in days.unique():
        # The next midnight on the wall clock: a day across a clock change
        # is 23 or 25 hours long
        next_day = day + pd.DateOffset(days=1)
        if day < t[0] or next_day > end:
            continue
        mine = values[(t >= day) & (t < next_day)]
        expected = (next_day - day) / step
        held = mine[np.isfinite(mine)]
        if len(held) > expected / 2:
            medians.append(float(np.median(held)))
    if len(medians) < 2 or medians[0] <= 0:
        return None
    return medians[-1] / medians[0]


def _trace(frame: pd.DataFrame, window: pd.Timedelta) -> dict[str, Any]:
    values = frame["value"].to_numpy("float64")
    n, could = int(frame["n"].sum()), int(frame["n_samples"].sum())
    held = values[np.isfinite(values)]
    return {
        "median": float(np.median(held)) if len(held) else None,
        "drift": drift(frame["t"], values),
        "step": largest_step(frame["t"], values, window),
        "n": n,
        "n_samples": could,
        "presence": n / could if could else None,
    }


def instrument_signal(
    store: CampaignStore,
    *,
    reagents: Sequence[str] | str | None = None,
    normalise: Any = "none",
    cadence: str = "1h",
    step_window: str = "24h",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Figure:
    """The TIC and the reagent ions over time.

    :param reagents: The reagent's ions, as series keys; none by default,
        and then the TIC alone is drawn. A reagent is never guessed from a
        name.
    :param normalise: What the values are divided by (the views'
        ``normalise``).
    :param cadence: The bins (an hour by default).
    :param step_window: The stretch each side of a time a step compares.
    :param tz: The zone the bins are in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    """
    reagents = _reagent_keys(reagents)
    window = pd.Timedelta(step_window)
    if window <= pd.Timedelta(0):
        raise ValueError(f"step_window={step_window!r} is not a positive duration")
    keys: list[Any] = [views.TIC, *reagents]
    if len(reagents) > 1:
        keys.append(tuple(reagents))
    frame = views.series(
        store, keys, cadence=cadence, normalise=normalise, tz=tz, start=start, end=end
    )
    attrs = dict(frame.attrs)
    zone = attrs["tz"]
    refs = species_refs(store, keys)
    frame = frame.assign(key=frame["key"].astype(str))
    frame["panel"] = np.where(frame["key"] == views.TIC, "tic", "reagents")
    parts = [frame]
    shares = None
    if len(reagents) > 1:
        # Each ion over the sum of them all, per sample, then binned
        shares = views.series(
            store,
            reagents,
            cadence=cadence,
            normalise=list(reagents),
            tz=tz,
            start=start,
            end=end,
        )
        shares = shares.assign(key=shares["key"].astype(str), panel="shares")
        parts.append(shares)
    data = pd.concat(parts, ignore_index=True)
    data["name"] = data["key"].map(lambda key: refs[key]["name"])
    data = data[["t", "key", "name", "panel", "value", "lo", "hi", "n", "n_samples"]]

    rules = run_rules(attrs)
    encodings = {
        "x": "t",
        "y": "value",
        "group": "key",
        "name": "name",
        "lo": "lo",
        "hi": "hi",
    }
    x_axis = {"label": time_label(zone)}
    divisor = normaliser_name(attrs)
    tic_label = TIC_LABEL if divisor is None else f"{TIC_LABEL} / {divisor}"
    panels = [
        Panel(
            "series",
            axes={"x": x_axis, "y": {"label": tic_label, "scale": "log"}},
            encodings=encodings,
            options={"rules": rules},
            title="TIC",
            where={"panel": "tic"},
        )
    ]
    if reagents:
        panels.append(
            Panel(
                "series",
                axes={
                    "x": x_axis,
                    "y": {"label": value_label(store, attrs), "scale": "log"},
                },
                encodings=encodings,
                options={"rules": rules},
                title="Reagent ions" + (" and their sum" if shares is not None else ""),
                where={"panel": "reagents"},
            )
        )
    if shares is not None:
        panels.append(
            Panel(
                "series",
                axes={
                    "x": x_axis,
                    "y": {"label": "Share of the reagent sum", "range": (0, 1)},
                },
                encodings=encodings,
                options={"rules": rules},
                title="Each reagent ion's share of the sum",
                where={"panel": "shares"},
            )
        )

    traces = []
    for key in [k if isinstance(k, str) else "+".join(k) for k in keys]:
        mine = frame[frame["key"] == key]
        traces.append({**refs[key], **_trace(mine, window)})
    share_rows = []
    if shares is not None:
        for key in reagents:
            values = shares.loc[shares["key"] == key, "value"].to_numpy("float64")
            held = values[np.isfinite(values)]
            share_rows.append(
                {
                    **refs[key],
                    "median_share": float(np.median(held)) if len(held) else None,
                }
            )
    times = pd.DatetimeIndex(frame["t"].dropna().unique()).sort_values()
    # The span the bins cover: the last one's end, a raw sample's time
    width = times[1:] - times[:-1]
    last = None
    if len(times):
        last = times[-1]
        if attrs.get("cadence") != "raw" and len(width):
            last = last + width.median()
    summary = {
        **face(attrs, zone, start=times.min() if len(times) else None, end=last),
        "stat": attrs.get("stat"),
        "band": attrs.get("band"),
        "step_window": duration_text(window),
        "polarities": polarities(attrs),
        "reagents": [refs[key] for key in reagents],
        "traces": traces,
        "shares": share_rows,
    }
    notes = []
    if not reagents:
        notes.append(
            "No reagent ion was named, so the TIC is drawn alone: name the "
            "reagent's ions to draw them."
        )
    if shares is not None:
        notes.append(
            "The sum adds the reagent ions with a value in each sample: where "
            "one is below detection, the sum is the others' and its share is "
            "missing."
        )
    notes += pooled_note(
        attrs,
        "the TIC pools them: a bin's median and its p10-p90 band mix the two "
        "polarities' TICs, so the band can be the gap between them",
    )
    notes += [*normalise_notes(attrs), *run_notes(attrs, zone), BELOW_DETECTION]
    return Figure(
        "instrument_signal",
        "Instrument signal",
        panels=panels,
        summary=summary,
        data=data,
        notes=notes,
        share_x=True,
        context=Context.from_attrs(attrs),
    )


KIND = registry.register(
    ChartKind(
        id="instrument_signal",
        question="Was the ion source stable?",
        tier=1,
        series=(
            "tic, and each reagent ion named; with two or more, their sum and "
            "each one's share of it"
        ),
        params=(
            Param(
                "reagents",
                "keys",
                None,
                help="the reagent's ions, as series keys; without them the TIC "
                "is drawn alone",
            ),
            NORMALISE_PARAM,
            Param("cadence", "cadence", "1h", help="the bins: auto, raw or an offset"),
            Param(
                "step_window",
                "duration",
                "24h",
                help="the stretch each side of a time a step compares",
            ),
            *WINDOW_PARAMS,
        ),
        summary=(
            "reagents",
            "traces",
            "shares",
            "stat",
            "band",
            "step_window",
            "polarities",
            "normalise",
            "normalise_name",
            "cadence",
            "tz",
            "start",
            "end",
            "mixed_runs",
            "run_changes",
        ),
        build=instrument_signal,
    )
)
