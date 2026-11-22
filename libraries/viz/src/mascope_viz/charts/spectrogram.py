"""``spectrogram``: what does the whole campaign look like in one picture?

A heatmap of time by m/z on a fixed grid, log colour, drawn from
:func:`mascope_viz.data.views.spectrogram`: a cell is the mean over its
time bin's samples of the sum of the assigned peaks in its m/z row, so a
bin with more samples is not brighter for it (``docs/dev/visual_digest.md``,
section 3, composition snapshots). A bin without samples is drawn in the
gap style; a cell with samples and nothing detected stays blank.

The store holds assigned peaks only, so the chart says so in its title and
subtitle. It draws marks; the ``raster`` mode is the ``[big]`` extra's.
"""

from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from . import common, registry
from .spec import Context, Figure, Panel, subtitle


#: The percentiles of the cells with a value the colour scale runs between.
COLOUR_LIMITS = (1, 99)


def spectrogram(
    store: CampaignStore,
    *,
    mz_width: float = 1.0,
    cadence: str = "1h",
    normalise: Any = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
    top_n: int = 10,
    max_rows: int = views.SPECTROGRAM_MAX_ROWS,
) -> Figure:
    """The assigned signal over time on a fixed m/z grid.

    :param mz_width: The m/z rows' width in Th, centred on its multiples;
        widened, and said so, when the rows would pass *max_rows*.
    :param cadence: The time bins, a pandas offset.
    :param normalise: ``none``, ``tic``, a series key, or a list of keys
        whose sum divides.
    :param tz: The zone the bins are in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    :param top_n: How many rows and columns the summary lists.
    :param max_rows: The m/z rows the grid may hold.
    """
    top_n = common.check_count("top_n", top_n)
    grid = views.spectrogram(
        store,
        mz_width=mz_width,
        cadence=cadence,
        normalise=normalise,
        tz=tz,
        start=start,
        end=end,
        max_rows=max_rows,
    )
    attrs = grid.cells.attrs
    zone = attrs["tz"]
    data = _cells(grid)
    limits = _limits(grid.cells["value"].to_numpy("float64"))
    signal = common.signal_label(store, attrs)
    notes = [
        common.ASSIGNED_ONLY,
        "A cell is the mean, over its bin's samples with a value, of the sum "
        "of the assigned peaks in its m/z row in each sample.",
    ]
    if limits is not None:
        notes.append(
            "The colour runs on a log scale from the 1st to the 99th percentile "
            f"of the cells with a value ({_short(limits[0])} to "
            f"{_short(limits[1])}); a cell beyond takes its end's colour."
        )
    if grid.widened:
        notes.append(
            f"The m/z rows are {grid.mz_width:g} Th wide, not the "
            f"{attrs['mz_width_asked']:g} Th asked for, to keep the grid under "
            f"{attrs['max_rows']} rows."
        )
    notes += common.formula_run_notes(attrs) + common.normaliser_notes(attrs)
    context = Context.from_attrs(attrs)
    figure = Figure(
        "spectrogram",
        "Spectrogram of the assigned peaks",
        subtitle(
            context, extra=["assigned peaks only", f"rows of {grid.mz_width:g} Th"]
        ),
        panels=[
            Panel(
                "heatmap",
                axes={
                    "x": {"label": f"Time ({zone})"},
                    "y": {"label": "m/z (ion, adduct included)"},
                    "colour": {
                        "label": f"{signal}, bin mean",
                        "scale": "log",
                        "range": limits,
                    },
                },
                encodings={"x": "t", "y": "mz", "value": "value", "gap": "gap"},
                options={"rules": common.formula_run_rules(attrs)},
            )
        ],
        summary=_summary(grid, limits, top_n),
        data=data,
        notes=notes,
        context=context,
    )
    return figure


def _cells(grid: views.Spectrogram) -> pd.DataFrame:
    """The heatmap's frame: each cell with a value at its bin's middle, a
    gap cell in every row of a bin without samples, and a placeholder
    without a value for a row or a bin neither gives, so that every row and
    every bin of the grid has its place and no cell stretches over one."""
    cells, bins, rows = grid.cells, grid.bins, grid.rows
    zone = cells.attrs["tz"]
    columns = ["t", "start", "mz", "value", "n", "n_samples", "presence", "gap"]
    if bins.empty or rows.empty:
        frame = pd.DataFrame(
            {
                "t": pd.DatetimeIndex([], tz=zone).as_unit("ns"),
                "start": pd.DatetimeIndex([], tz=zone).as_unit("ns"),
                "mz": pd.Series([], dtype="float64"),
                "value": pd.Series([], dtype="float64"),
                "n": pd.Series([], dtype="int64"),
                "n_samples": pd.Series([], dtype="int64"),
                "presence": pd.Series([], dtype="float64"),
                "gap": pd.Series([], dtype=bool),
            }
        )
        return frame[columns]
    middle = bins["t"] + (bins["t_end"] - bins["t"]) / 2
    middle_of = pd.Series(middle.to_numpy(), index=bins["t"].to_numpy())
    with np.errstate(divide="ignore", invalid="ignore"):
        presence = cells["n"] / cells["n_samples"].where(cells["n_samples"] > 0)
    parts = [
        pd.DataFrame(
            {
                "t": cells["t"].map(middle_of).to_numpy(),
                "start": cells["t"].to_numpy(),
                "mz": cells["mz"].to_numpy(),
                "value": cells["value"].to_numpy(),
                "n": cells["n"].to_numpy(),
                "n_samples": cells["n_samples"].to_numpy(),
                "presence": presence.to_numpy("float64"),
                "gap": False,
            }
        )
    ]
    empty = bins["n_samples"].to_numpy() == 0
    if empty.any():
        n_rows = len(rows)
        parts.append(
            pd.DataFrame(
                {
                    "t": np.repeat(middle[empty].to_numpy(), n_rows),
                    "start": np.repeat(bins.loc[empty, "t"].to_numpy(), n_rows),
                    "mz": np.tile(rows["mz"].to_numpy(), int(empty.sum())),
                    "value": np.nan,
                    "n": 0,
                    "n_samples": 0,
                    "presence": np.nan,
                    "gap": True,
                }
            )
        )
    placed = pd.concat(parts, ignore_index=True)
    rows_left = rows.loc[~rows["mz"].isin(placed["mz"]), "mz"].to_numpy()
    bins_left = ~bins["t"].isin(placed["start"]).to_numpy()
    fillers = [(middle.iloc[0], bins["t"].iloc[0], mz) for mz in rows_left] + [
        (middle.iloc[i], bins["t"].iloc[i], rows["mz"].iloc[0])
        for i in np.flatnonzero(bins_left)
    ]
    if fillers:
        t, start, mz = zip(*fillers)
        parts.append(
            pd.DataFrame(
                {
                    "t": list(t),
                    "start": list(start),
                    "mz": list(mz),
                    "value": np.nan,
                    "n": 0,
                    "n_samples": 0,
                    "presence": np.nan,
                    "gap": False,
                }
            )
        )
    frame = pd.concat(parts, ignore_index=True)
    frame["t"] = pd.DatetimeIndex(frame["t"]).tz_convert(zone)
    frame["start"] = pd.DatetimeIndex(frame["start"]).tz_convert(zone)
    frame["n"] = frame["n"].astype("int64")
    frame["n_samples"] = frame["n_samples"].astype("int64")
    frame["gap"] = frame["gap"].astype(bool)
    return frame.sort_values(["t", "mz"], kind="stable").reset_index(drop=True)[columns]


def _limits(values: np.ndarray) -> tuple[float, float] | None:
    """The colour scale's ends: the 1st and 99th percentiles of the
    positive values, which a log scale draws; None when they do not span a
    range."""
    positive = values[np.isfinite(values) & (values > 0)]
    if not positive.size:
        return None
    low, high = (float(v) for v in np.percentile(positive, COLOUR_LIMITS))
    if not low < high:
        return None
    return low, high


def _short(value: float) -> str:
    return f"{value:.3g}"


def _summary(
    grid: views.Spectrogram, limits: tuple[float, float] | None, top_n: int
) -> dict[str, Any]:
    cells, bins, rows = grid.cells, grid.bins, grid.rows
    attrs = cells.attrs
    zone = attrs["tz"]
    by_total = rows[rows["total"].notna()].sort_values(
        ["total", "mz"], ascending=[False, True], kind="stable"
    )
    top_rows = [
        {
            "mz": float(row.mz),
            "total": common.number(row.total),
            "n_keys": int(row.n_keys),
            "presence": common.number(row.n / row.n_samples) if row.n_samples else None,
            "species": common.species(row.key, row.name, row.formula),
            "share": common.number(row.share),
        }
        for row in by_total.head(top_n).itertuples()
    ]
    columns = bins[bins["total"].notna()].sort_values(
        ["total", "t"], ascending=[False, True], kind="stable"
    )
    top_columns = [
        {
            "t": common.iso(row.t, zone),
            "end": common.iso(row.t_end, zone),
            "total": common.number(row.total),
            "n_samples": int(row.n_samples),
        }
        for row in columns.head(top_n).itertuples()
    ]
    return {
        **common.context_summary(attrs),
        "stat": "mean",
        "mz_width": grid.mz_width,
        "mz_width_asked": attrs["mz_width_asked"],
        "widened": grid.widened,
        "max_rows": attrs["max_rows"],
        "n_rows": len(rows),
        "n_bins": len(bins),
        "n_gap_bins": int((bins["n_samples"] == 0).sum()),
        "n_cells": len(cells),
        "colour_low": limits[0] if limits else None,
        "colour_high": limits[1] if limits else None,
        "top_rows": top_rows,
        "top_columns": top_columns,
    }


KIND = registry.register(
    registry.ChartKind(
        id="spectrogram",
        question="What does the whole campaign look like in one picture?",
        tier=1,
        series=(
            "every formula key with an m/z (the assigned batch peaks), summed "
            "per sample into fixed m/z rows"
        ),
        params=(
            registry.Param("mz_width", "number", 1.0, help="the m/z rows' width in Th"),
            registry.Param("cadence", "duration", "1h", help="the time bins"),
            registry.Param(
                "normalise",
                "normalise",
                "none",
                help="none, tic, a series key, or keys whose sum divides",
            ),
            registry.Param("tz", "text", None, help="the zone times are shown in"),
            registry.Param("start", "time", None, help="the window's start"),
            registry.Param("end", "time", None, help="the window's end"),
            registry.Param(
                "top_n", "int", 10, help="the rows and columns the summary lists"
            ),
            registry.Param(
                "max_rows",
                "int",
                views.SPECTROGRAM_MAX_ROWS,
                help="the m/z rows the grid may hold before they widen",
            ),
        ),
        summary=(
            *common.CONTEXT_SUMMARY,
            "stat",
            "mz_width",
            "mz_width_asked",
            "widened",
            "max_rows",
            "n_rows",
            "n_bins",
            "n_gap_bins",
            "n_cells",
            "colour_low",
            "colour_high",
            "top_rows",
            "top_columns",
        ),
        build=spectrogram,
    )
)
