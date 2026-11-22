"""``coverage``: when was the instrument acquiring, and is every file in a
batch? (``docs/dev/visual_digest.md``, section 3.)

A strip, one row per polarity and ionization mode, coloured by the samples
per hour; the files no batch holds have a row of their own and a mark where
they were acquired, and the view's gaps are shaded.
"""

from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from . import registry
from .common import (
    POLARITY_NAMES,
    WINDOW_PARAMS,
    duration_text,
    face,
    moment,
    time_label,
)
from .registry import ChartKind, Param
from .spec import Context, Figure, Panel, subtitle


#: What a cell's mark means.
UNFILED = "acquired, filed into no batch"


def row_labels(bins: pd.DataFrame) -> list[str]:
    """Each bin's row, as a reader names it: the polarity and the mode's
    name (its ID beside it when two modes share the name), and ``in no
    batch`` for the files no sample of the store comes from."""
    pairs = bins[["polarity", "mode", "mode_name"]].astype(object).drop_duplicates()
    shared = pairs.dropna(subset=["mode"]).groupby("mode_name", dropna=True)["mode"]
    twice = {name for name, ids in shared if ids.nunique() > 1}
    labels = {}
    for polarity, mode, name in pairs.itertuples(index=False):
        sign = None if polarity is None or pd.isna(polarity) else str(polarity)
        parts = [POLARITY_NAMES.get(sign, sign)] if sign else []
        if mode is None or pd.isna(mode):
            parts.append("in no batch")
        elif name is None or pd.isna(name):
            parts.append(f"mode {mode}")
        else:
            parts.append(f"{name} ({mode})" if name in twice else str(name))
        labels[(polarity, mode)] = " / ".join(parts)
    return [labels[(p, m)] for p, m in zip(bins["polarity"], bins["mode"])]


def numbers(found: views.Coverage) -> dict[str, Any]:
    """What a cover cites of a coverage: the span (first to last file or
    sample), the hours in it, those the gaps miss and those acquired, and
    the share acquired."""
    start, end = found.start, found.end
    if start is None or end is None:
        return {
            "hours_span": 0.0,
            "hours_missing": 0.0,
            "hours_acquired": 0.0,
            "coverage_fraction": None,
        }
    span = (end - start) / pd.Timedelta(hours=1)
    missing = float(found.gaps["duration"].sum() / pd.Timedelta(hours=1))
    acquired = span - missing
    return {
        "hours_span": span,
        "hours_missing": missing,
        "hours_acquired": acquired,
        "coverage_fraction": acquired / span if span > 0 else None,
    }


def coverage(
    store: CampaignStore,
    *,
    cadence: str = "1h",
    min_gap: str = "1h",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
) -> Figure:
    """When the instrument acquired, and what of it a batch holds.

    :param cadence: A cell's width, a pandas offset.
    :param min_gap: The shortest stretch without a sample that is shaded
        as a gap.
    :param tz: The zone the cells are in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    """
    found = views.coverage(
        store, cadence=cadence, min_gap=min_gap, tz=tz, start=start, end=end
    )
    bins = found.bins
    attrs = bins.attrs
    zone = attrs["tz"]
    data = pd.DataFrame(
        {
            "t": bins["t"],
            "row": pd.Categorical(
                row_labels(bins), categories=list(dict.fromkeys(row_labels(bins)))
            ),
            "polarity": bins["polarity"].astype(object),
            "mode": bins["mode"].astype(object),
            "mode_name": bins["mode_name"].astype(object),
            "n_samples": bins["n_samples"].astype("int64"),
            "n_files": bins["n_files"].astype("int64"),
            "n_unfiled": bins["n_unfiled"].astype("int64"),
            "unfiled": (bins["n_unfiled"] > 0).to_numpy(dtype=bool),
        }
    )
    gaps = found.gaps
    shortest = duration_text(min_gap)
    width = duration_text(pd.Timedelta(pd.tseries.frequencies.to_offset(cadence)))
    panel = Panel(
        "strip",
        axes={
            "x": {"label": time_label(zone)},
            "colour": {"label": f"Samples per {width}"},
        },
        encodings={"x": "t", "y": "row", "value": "n_samples", "mark": "unfiled"},
        options={
            "width": cadence,
            "mark_label": UNFILED,
            "spans": [
                {
                    "start": gap.start,
                    "end": gap.end,
                    "label": "no samples",
                    "kind": "gap",
                }
                for gap in gaps.itertuples()
            ],
        },
    )
    context = Context(
        cadence=attrs.get("cadence"),
        tz=zone,
        start=attrs.get("start"),
        end=attrs.get("end"),
    )
    rows = (
        data.groupby("row", observed=True, sort=False)
        .agg(
            polarity=("polarity", "first"),
            mode=("mode", "first"),
            mode_name=("mode_name", "first"),
            n_files=("n_files", "sum"),
            n_unfiled=("n_unfiled", "sum"),
            n_samples=("n_samples", "sum"),
        )
        .reset_index()
    )
    summary = {
        **face(attrs, zone, start=found.start, end=found.end),
        "span": {"start": moment(found.start, zone), "end": moment(found.end, zone)},
        **numbers(found),
        "min_gap": shortest,
        "gaps": [
            {
                "start": gap.start,
                "end": gap.end,
                "hours": gap.duration / pd.Timedelta(hours=1),
                "n_files": int(gap.n_files),
                "n_unfiled": int(gap.n_unfiled),
            }
            for gap in gaps.itertuples()
        ],
        "n_files": found.n_files,
        "n_unfiled": found.n_unfiled,
        "n_samples": found.n_samples,
        "rows": [
            {
                "row": str(row.row),
                "polarity": row.polarity,
                "mode": row.mode,
                "mode_name": row.mode_name,
                "n_files": int(row.n_files),
                "n_unfiled": int(row.n_unfiled),
                "n_samples": int(row.n_samples),
            }
            for row in rows.itertuples()
        ],
    }
    notes = []
    if found.n_unfiled:
        notes.append(
            f"{found.n_unfiled} of {found.n_files} files were acquired but are "
            "in no batch of the scope: their samples are in no other chart."
        )
    return Figure(
        "coverage",
        "Coverage",
        subtitle(context, extra=[f"gaps of at least {shortest} shaded"]),
        panels=[panel],
        summary=_plain_rows(summary),
        data=data,
        notes=notes,
        context=context,
    )


def _plain_rows(summary: dict[str, Any]) -> dict[str, Any]:
    """*summary* with the missing values of its rows as None."""
    for row in summary["rows"]:
        for name in ("polarity", "mode", "mode_name"):
            value = row[name]
            if value is not None and not isinstance(value, str) and np.isnan(value):
                row[name] = None
    return summary


KIND = registry.register(
    ChartKind(
        id="coverage",
        question="When was the instrument acquiring, and is every file in a batch?",
        tier=1,
        series="files and samples, per polarity and ionization mode",
        params=(
            Param("cadence", "duration", "1h", help="a cell's width"),
            Param(
                "min_gap",
                "duration",
                "1h",
                help="the shortest stretch without a sample shaded as a gap",
            ),
            *WINDOW_PARAMS,
        ),
        summary=(
            "span",
            "hours_span",
            "hours_missing",
            "hours_acquired",
            "coverage_fraction",
            "min_gap",
            "gaps",
            "n_files",
            "n_unfiled",
            "n_samples",
            "rows",
            "normalise",
            "normalise_name",
            "cadence",
            "tz",
            "start",
            "end",
            "mixed_runs",
            "run_changes",
        ),
        build=coverage,
    )
)
