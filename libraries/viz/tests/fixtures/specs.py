"""One Figure spec per primitive, for the renderer tests.

Each draws a small frame shaped as the views give theirs (a categorical
``key``, times in an instrument's zone, a NaN bin where no sample fell),
with what the primitive can say switched on: a band, a log axis, event
and gap spans, a run-change rule, top-N labels, groups, error bars. The
frames are built from fixed numbers, so a spec is the same on every run
and every platform. :func:`everything` puts all eight in one figure.
"""

import numpy as np
import pandas as pd

from mascope_viz.charts import Figure, Panel, subtitle


#: The zone the fixtures' times are in: one with a clock change, and a
#: half-hour offset elsewhere would not matter to the renderers.
ZONE = "Europe/Helsinki"

#: Two species, as a view keys them.
KEYS = ["ion:1", "formula:C3H4O4|[M+I]-"]

#: Their display names.
NAMES = {"ion:1": "Malonic acid", "formula:C3H4O4|[M+I]-": "C3H4O4 $I$ <b>"}


def _wave(n: int, phase: float, scale: float) -> np.ndarray:
    hours = np.arange(n)
    return scale * (2.0 + np.sin(2 * np.pi * (hours / 24.0) + phase))


def series() -> Figure:
    """Two species over two days at an hourly cadence: a median line with
    its p10-p90 band, a gap of two bins, a log y, an episode and a gap
    span, and a run change."""
    t = pd.date_range("2026-07-01", periods=48, freq="1h", tz=ZONE)
    frames = []
    for i, key in enumerate(KEYS):
        value = _wave(48, phase=i, scale=10.0 ** (i + 2))
        value[20:22] = np.nan
        value[30] = np.nan
        value[32] = np.nan
        frames.append(
            pd.DataFrame(
                {
                    "t": t,
                    "key": key,
                    "name": NAMES[key],
                    "value": value,
                    "lo": value * 0.7,
                    "hi": value * 1.3,
                    "n": np.where(np.isnan(value), 0, 12),
                }
            )
        )
    data = pd.concat(frames, ignore_index=True)
    data["key"] = pd.Categorical(data["key"], categories=KEYS)
    panel = Panel(
        "series",
        axes={
            "x": {"label": f"Time ({ZONE})"},
            "y": {"label": "Intensity (counts)", "scale": "log"},
        },
        encodings={"x": "t", "y": "value", "group": "name", "lo": "lo", "hi": "hi"},
        options={
            "spans": [
                {
                    "start": pd.Timestamp("2026-07-01T06:00", tz=ZONE),
                    "end": pd.Timestamp("2026-07-01T10:00", tz=ZONE),
                    "label": "Episode 1",
                    "kind": "event",
                },
                {
                    "start": pd.Timestamp("2026-07-01T20:00", tz=ZONE),
                    "end": pd.Timestamp("2026-07-01T22:00", tz=ZONE),
                    "label": "no samples",
                    "kind": "gap",
                },
            ],
            "rules": [
                {
                    "at": pd.Timestamp("2026-07-02T12:00", tz=ZONE),
                    "label": "run changes",
                }
            ],
        },
    )
    return Figure(
        "species_series",
        "Species over time",
        subtitle(cadence="1h", stat="median", normalise="none", tz=ZONE),
        panels=[panel],
        summary={
            "median": {
                NAMES[k]: float(np.nanmedian(f["value"])) for k, f in zip(KEYS, frames)
            }
        },
        data=data,
        notes=["Gaps are bins without a sample."],
    )


def strip() -> Figure:
    """Coverage: two rows by hour over two days, shaded by sample count,
    an hour acquired but filed into no batch marked, and a gap."""
    t = pd.date_range("2026-07-01", periods=48, freq="1h", tz=ZONE)
    rows = []
    for row, scale in (("negative / mode A", 60), ("positive / mode B", 30)):
        samples = (scale * (1 + np.cos(np.arange(48) / 5.0)) / 2).round()
        samples[10:14] = 0
        rows.append(
            pd.DataFrame(
                {
                    "t": t,
                    "stream": row,
                    "samples": samples.astype("int64"),
                    "unfiled": np.arange(48) == 30,
                }
            )
        )
    data = pd.concat(rows, ignore_index=True)
    panel = Panel(
        "strip",
        axes={
            "x": {"label": f"Time ({ZONE})"},
            "colour": {"label": "Samples per hour"},
        },
        encodings={"x": "t", "y": "stream", "value": "samples", "mark": "unfiled"},
        options={
            "width": "1h",
            "mark_label": "acquired, in no batch",
            "spans": [
                {
                    "start": pd.Timestamp("2026-07-01T10:00", tz=ZONE),
                    "end": pd.Timestamp("2026-07-01T14:00", tz=ZONE),
                    "label": "gap",
                    "kind": "gap",
                }
            ],
        },
    )
    return Figure(
        "coverage",
        "Coverage",
        subtitle(cadence="1h", tz=ZONE),
        panels=[panel],
        summary={"coverage_fraction": 0.92},
        data=data,
    )


def heatmap() -> Figure:
    """A spectrogram, time by m/z with a log colour, beside a weekly cycle,
    weekday by hour on a diverging palette."""
    t = pd.date_range("2026-07-01", periods=24, freq="1h", tz=ZONE)
    mz = np.array([100.0, 101.0, 102.5, 104.0, 110.0, 120.0])
    times, masses = np.meshgrid(np.arange(24), mz)
    intensity = 10.0 ** (1 + 3 * np.abs(np.sin(times / 7.0 + masses / 30.0)))
    intensity[2, 5] = np.nan
    spectrogram = pd.DataFrame(
        {
            "t": np.tile(t, len(mz)),
            "mz": masses.ravel(),
            "intensity": intensity.ravel(),
            "part": "spectrogram",
        }
    )
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours, weekday = np.meshgrid(np.arange(24), np.arange(7))
    change = np.sin(hours / 4.0) * np.cos(weekday)
    cycle = pd.DataFrame(
        {
            "hour": hours.ravel().astype("int64"),
            "weekday": [days[d] for d in weekday.ravel()],
            "change": change.ravel(),
            "part": "weekly",
        }
    )
    data = pd.concat([spectrogram, cycle], ignore_index=True)
    return Figure(
        "spectrogram",
        "Spectrogram and weekly cycle",
        subtitle(cadence="1h", stat="median", tz=ZONE),
        panels=[
            Panel(
                "heatmap",
                axes={
                    "x": {"label": f"Time ({ZONE})"},
                    "y": {"label": "m/z"},
                    "colour": {"label": "Intensity", "scale": "log"},
                },
                encodings={"x": "t", "y": "mz", "value": "intensity"},
                options={"mode": "raster"},
                title="Time by m/z",
                where={"part": "spectrogram"},
            ),
            Panel(
                "heatmap",
                axes={
                    "x": {"label": "Hour of day"},
                    "colour": {"label": "log2 fold change"},
                },
                encodings={"x": "hour", "y": "weekday", "value": "change"},
                options={"palette": "diverging", "centre": 0.0},
                title="Weekly cycle",
                where={"part": "weekly"},
            ),
        ],
        data=data,
    )


def scatter() -> Figure:
    """A Van Krevelen coloured by carbon number with the three brightest
    labelled, and one coloured by class."""
    n_c = np.array([3, 4, 5, 6, 8, 10, 12, 15, 4, 7], dtype="int64")
    o_c = np.array([1.3, 1.0, 0.8, 0.5, 0.4, 0.3, 0.25, 0.2, 0.75, 0.6])
    h_c = np.array([1.3, 1.5, 1.6, 1.4, 1.75, 1.6, 1.5, 1.9, 1.0, 1.3])
    median = np.array([5e3, 2e4, 1e3, 8e4, 3e2, 4e4, np.nan, 7e3, 1.5e4, 9e2])
    data = pd.DataFrame(
        {
            "formula": [f"C{c}HxOy" for c in n_c],
            "o_c": o_c,
            "h_c": h_c,
            "median": median,
            "n_c": n_c,
            "class": [
                "CHO",
                "CHO",
                "CHON",
                "CHO",
                "CHOS",
                "CHON",
                "CHO",
                "CHO",
                "CHON",
                "CHOS",
            ],
        }
    )
    axes = {"x": {"label": "O/C", "range": (0.0, 1.5)}, "y": {"label": "H/C"}}
    return Figure(
        "van_krevelen",
        "Van Krevelen",
        "the assigned formulas; size by median intensity",
        panels=[
            Panel(
                "scatter",
                axes={**axes, "colour": {"label": "Carbon number"}},
                encodings={
                    "x": "o_c",
                    "y": "h_c",
                    "size": "median",
                    "colour": "n_c",
                    "label": "formula",
                },
                options={"top_n": 3},
                title="By carbon number",
            ),
            Panel(
                "scatter",
                axes=axes,
                encodings={"x": "o_c", "y": "h_c", "colour": "class"},
                options={"colours": {"CHOS": "warning"}},
                title="By class",
            ),
        ],
        data=data,
        columns=2,
    )


def sticks() -> Figure:
    """A period's spectrum with the four tallest labelled, and its
    difference to another period, up and down in two colours."""
    mz = np.array([62.0, 97.0, 126.9, 145.0, 175.0, 190.9, 210.0, 254.8])
    mean = np.array([3e4, 1.2e4, 8e4, 4e3, 6e3, 2.5e4, 1e3, 9e3])
    difference = np.array([5e3, -2e3, 1.5e4, -6e2, 1e3, -8e3, 2e2, 3e3])
    names = [f"m{int(m)}" for m in mz]
    data = pd.DataFrame(
        {
            "mz": np.concatenate([mz, mz]),
            "intensity": np.concatenate([mean, difference]),
            "formula": names + names,
            "part": ["mean"] * 8 + ["difference"] * 8,
            "direction": ["mean"] * 8 + ["up" if d > 0 else "down" for d in difference],
        }
    )
    return Figure(
        "period_spectrum",
        "Period spectrum",
        "night against day",
        panels=[
            Panel(
                "sticks",
                axes={"x": {"label": "m/z"}, "y": {"label": "Mean intensity"}},
                encodings={"x": "mz", "y": "intensity", "label": "formula"},
                options={"top_n": 4},
                where={"part": "mean"},
                title="Night",
            ),
            Panel(
                "sticks",
                axes={"x": {"label": "m/z"}, "y": {"label": "Night - day"}},
                encodings={
                    "x": "mz",
                    "y": "intensity",
                    "group": "direction",
                    "label": "formula",
                },
                options={"colours": {"up": "success", "down": "error"}, "top_n": 3},
                where={"part": "difference"},
                title="Difference",
            ),
        ],
        data=data,
        share_x=True,
    )


def bars() -> Figure:
    """Tier counts per day, two groups side by side with error bars, and the
    same stacked."""
    days = ["Jul 1", "Jul 2", "Jul 3"]
    frames = []
    for group, base in (("assigned", 120.0), ("candidate", 45.0)):
        frames.append(
            pd.DataFrame(
                {
                    "day": days,
                    "tier": group,
                    "count": [base, base * 1.1, base * 0.9],
                    "lo": [base * 0.9, base, base * 0.8],
                    "hi": [base * 1.1, base * 1.2, base],
                }
            )
        )
    data = pd.concat(frames, ignore_index=True)
    return Figure(
        "tier_counts",
        "Peaks per tier",
        panels=[
            Panel(
                "bars",
                axes={"y": {"label": "Peaks"}},
                encodings={
                    "x": "day",
                    "y": "count",
                    "group": "tier",
                    "lo": "lo",
                    "hi": "hi",
                },
                title="Side by side",
            ),
            Panel(
                "bars",
                axes={"y": {"label": "Peaks"}},
                encodings={"x": "day", "y": "count", "group": "tier"},
                options={"stack": True},
                title="Stacked",
            ),
        ],
        data=data,
        columns=2,
    )


def box() -> Figure:
    """Weekly distributions of two species, as the periods view gives them:
    quartiles, the 5th and 95th percentiles as whiskers, and the mean."""
    weeks = ["W27", "W28", "W29", "W30"]
    frames = []
    for i, key in enumerate(KEYS):
        median = np.array([10.0, 12.0, 9.0, 15.0]) * (i + 1)
        frames.append(
            pd.DataFrame(
                {
                    "week": weeks,
                    "name": NAMES[key],
                    "p5": median * 0.4,
                    "p25": median * 0.8,
                    "median": median,
                    "p75": median * 1.25,
                    "p95": median * 1.9,
                    "mean": median * 1.05,
                }
            )
        )
    data = pd.concat(frames, ignore_index=True)
    data.loc[3, "p25"] = np.nan
    return Figure(
        "period_boxes",
        "Weekly distributions",
        subtitle(cadence="week", normalise="none", tz=ZONE),
        panels=[
            Panel(
                "box",
                axes={"y": {"label": "Intensity"}},
                encodings={
                    "x": "week",
                    "q1": "p25",
                    "median": "median",
                    "q3": "p75",
                    "lo": "p5",
                    "hi": "p95",
                    "group": "name",
                    "mean": "mean",
                },
            )
        ],
        data=data,
    )


def table() -> Figure:
    """The top species: text, numbers in a format, an integer, a time, a
    boolean and a missing value, capped at four rows of six."""
    data = pd.DataFrame(
        {
            "name": [
                "Malonic acid",
                "C3H4O4 $I$ <b>",
                "Nitrate",
                "Iodide",
                "Oxalic acid",
                "x",
            ],
            "median": [12345.678, 0.00123, 5.5, np.nan, 1e7, 2.0],
            "ratio": [1.5, 0.25, 2.0, 1.0, 0.5, 3.0],
            "n": np.array([120, 80, 60, 40, 20, 10], dtype="int64"),
            "first": pd.date_range("2026-07-01T08:00", periods=6, freq="1D", tz=ZONE),
            "new": [False, True, False, False, True, False],
        }
    )
    return Figure(
        "top_species",
        "Top species",
        "by median intensity",
        panels=[
            Panel(
                "table",
                encodings={"columns": ["name", "median", "ratio", "n", "first", "new"]},
                options={
                    "headers": {
                        "median": "Median",
                        "ratio": "Last / first",
                        "first": "First seen",
                    },
                    "formats": {"median": ".3g", "ratio": ".0%"},
                    "max_rows": 4,
                },
            )
        ],
        data=data,
    )


#: The fixture figures, by primitive.
FIGURES = {
    "series": series,
    "strip": strip,
    "heatmap": heatmap,
    "scatter": scatter,
    "sticks": sticks,
    "bars": bars,
    "box": box,
    "table": table,
}


def everything() -> Figure:
    """All eight primitives in one figure of two columns, each panel
    keeping its own rows by a ``part`` column."""
    figures = {name: make() for name, make in FIGURES.items()}
    frames = []
    panels = []
    for name, figure in figures.items():
        first = figure.panels[0]
        frame = figure.frame(first).add_prefix(f"{name}.")
        frame.insert(0, "figure", name)
        frames.append(frame)
        encodings = {
            k: ([f"{name}.{c}" for c in v] if isinstance(v, list) else f"{name}.{v}")
            for k, v in first.encodings.items()
        }
        options = dict(first.options)
        panels.append(
            Panel(
                first.primitive,
                axes=first.axes,
                encodings=encodings,
                options=options,
                title=name,
                where={"figure": name},
            )
        )
    data = pd.concat(frames, ignore_index=True)
    return Figure(
        "everything",
        "Every primitive",
        "one spec, eight panels",
        panels=panels,
        data=data,
        columns=2,
    )
