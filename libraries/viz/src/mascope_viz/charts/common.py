"""What the chart kinds share: the face every chart carries (the intensity
variable on its axis, the run changes drawn, the notes a reader needs) and
the summary fields every chart's summary has.

A chart is a view call plus spec building (``docs/dev/visual_digest.md``,
section 2.4): the views aggregate, and a chart only arranges what they give
and says what it is. Pandas and NumPy only, so a server can build the
charts without the renderers.

The species charts (``species_series``, ``diurnal``, ``period_boxes``)
follow the same conventions (MAS-228's review, on every chart of the
note's phase 2), with helpers of their own below:

- **On the face**: the cadence, the statistic and the band, the
  normalisation by name, the window and the zone, all from the view's
  ``attrs`` (:func:`context`), never retyped.
- **Below detection is missing, not zero**, so each statistic is over the
  detected values only. Each summary carries presence, the values detected
  over the samples that could have held one (:func:`presence`).
- **Summaries** carry :data:`FACE_FIELDS` (:func:`face`), and a species
  as ``{key, name, formula}`` (:func:`summary`).
- **A parameter it cannot draw raises InvalidChart**, the views' and the
  spec's refusals included (:func:`refused`).
"""

import math
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from ..errors import InvalidChart, VizError
from .registry import Param
from .spec import Context
from .species import Resolved


#: A polarity as a reader names it.
POLARITY_NAMES = {"+": "positive", "-": "negative"}

#: What the TIC is since MAS-633: the mean intensity per scan, not the sum.
TIC_LABEL = "TIC, mean per scan"

#: The note every chart of species' values carries: below detection is no
#: value, so a statistic is over the detected values alone.
BELOW_DETECTION = (
    "Below detection is missing, not zero: every statistic is over the "
    "detected values only, and reads high for a species near its detection "
    "limit. The summary gives each one's presence: its values detected over "
    "the samples that could have held one."
)

#: The fields every chart's summary has.
FACE_FIELDS = (
    "normalise",
    "normalise_name",
    "cadence",
    "tz",
    "start",
    "end",
    "mixed_runs",
    "run_changes",
)

#: The parameters of a window in a zone, which every chart over time takes.
WINDOW_PARAMS = (
    Param(
        "tz",
        "text",
        None,
        help="the zone times are shown in: an IANA name, or instrument; "
        "the store's display zone when not given",
    ),
    Param("start", "time", None, help="keep the samples from this time on"),
    Param("end", "time", None, help="keep the samples before this time"),
)

#: The parameter every chart of species' values takes (decision 7).
NORMALISE_PARAM = Param(
    "normalise",
    "normalise",
    "none",
    help="none, tic, a series key, or a list of keys whose per-sample sum "
    "divides (a reagent spread over clusters)",
)


def intensity_variable(store: CampaignStore) -> str | None:
    """What a raw value of the store is: ``peak area`` on a TOF, ``peak
    height`` on an Orbitrap (the server's ``intensity_variable``), both
    when the store holds both, None when its samples name neither type."""
    types = store.table("samples")["instrument_type"].dropna().astype(str)
    found = set()
    for kind in types.str.lower().unique():
        if "tof" in kind:
            found.add("peak area")
        elif "orbi" in kind:
            found.add("peak height")
    if not found:
        return None
    return " or ".join(sorted(found))


def value_label(store: CampaignStore, attrs: Mapping[str, Any]) -> str:
    """A y axis' label for a view's values: the intensity variable when
    they are raw, ``signal / <normaliser>`` when they are divided. A
    signal, never a concentration: no calibration factor is applied."""
    name = normaliser_name(attrs)
    if name is None:
        variable = intensity_variable(store)
        return variable[0].upper() + variable[1:] if variable else "Signal"
    return f"signal / {name}"


def normaliser_name(attrs: Mapping[str, Any]) -> str | None:
    """What a view's values are divided by, by name; None when raw."""
    normalise = attrs.get("normalise", "none")
    if normalise in (None, "none"):
        return None
    name = attrs.get("normalise_name")
    if name:
        return str(name)
    if normalise == views.TIC:
        return "TIC"
    return normalise if isinstance(normalise, str) else " + ".join(normalise)


def time_label(zone: str) -> str:
    return f"Time ({zone})"


def species_refs(
    store: CampaignStore, keys: Sequence[Any]
) -> dict[str, dict[str, Any]]:
    """Each key as a summary names a species: ``{key, name, formula}``,
    by its key as a view spells it (a sum's members joined by ``+``).

    The TIC is named ``TIC``; a sum by its members' names joined by
    `` + ``, with no formula; a key the store has no name for by its
    formula, and without one by its key.

    :raises ValueError: A key the store does not hold.
    """
    members = [m for k in keys for m in _members(k) if m != views.TIC]
    species = store.table("species", keys=members) if members else None
    rows = {} if species is None else species.set_index("series_key")
    unknown = sorted(set(members) - set(getattr(rows, "index", ())))
    if unknown:
        raise ValueError(f"the store holds no series {', '.join(unknown)}")

    def one(key: str) -> dict[str, Any]:
        if key == views.TIC:
            return {"key": key, "name": "TIC", "formula": None}
        row = rows.loc[key]
        formula = _text(row["formula"])
        # A target ion without a compound name by its formula
        return {
            "key": key,
            "name": _text(row["name"]) or formula or key,
            "formula": formula,
        }

    out = {}
    for key in keys:
        parts = _members(key)
        if len(parts) == 1:
            out[parts[0]] = one(parts[0])
            continue
        named = [one(part)["name"] for part in parts]
        joined = "+".join(parts)
        out[joined] = {"key": joined, "name": " + ".join(named), "formula": None}
    return out


def _members(key: Any) -> tuple[str, ...]:
    return (key,) if isinstance(key, str) else tuple(key)


def _text(value: Any) -> str | None:
    """*value* as text; None when missing or blank."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value)
    return text if text.strip() else None


def moment(value: Any, zone: str) -> pd.Timestamp | None:
    """A time (a timestamp, or ISO 8601 text) in *zone*; None for none."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    at = pd.Timestamp(value)
    if at.tz is None:
        at = at.tz_localize("UTC")
    return at.tz_convert(zone)


def run_changes(attrs: Mapping[str, Any], zone: str) -> list[dict[str, Any]]:
    """A view's run changes with their times in *zone*."""
    return [
        dict(change, at=moment(change.get("at"), zone))
        for change in attrs.get("run_changes") or []
    ]


def run_rules(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A rule on a time axis at each run change a view flags: two readings
    are never joined silently."""
    rules = []
    seen = set()
    for change in attrs.get("run_changes") or []:
        if change["at"] in seen:
            continue
        seen.add(change["at"])
        rules.append({"at": change["at"], "label": f"engine run: {change['after']}"})
    return rules


def run_notes(attrs: Mapping[str, Any], zone: str) -> list[str]:
    """A note for each run change a view flags."""
    notes = []
    for change in run_changes(attrs, zone):
        polarity = POLARITY_NAMES.get(change.get("polarity"), change.get("polarity"))
        notes.append(
            f"The engine run that reads the formulas changes at "
            f"{change['at']:%Y-%m-%d %H:%M} ({polarity} batches: "
            f"{change['before']} to {change['after']}): a formula before and "
            "after it is two readings, not one series."
        )
    return notes


def polarities(attrs: Mapping[str, Any]) -> list[str]:
    """The polarities of a view's window's samples, ``+`` before ``-``."""
    return list(attrs.get("polarities") or [])


def pooled_note(attrs: Mapping[str, Any], what: str) -> list[str]:
    """A note when the window's samples are of both polarities, so *what*
    pools two chemistries; none for one polarity."""
    if len(polarities(attrs)) < 2:
        return []
    return [
        f"The window's samples are of both polarities, and {what}. Scope the "
        "store to one polarity to read either alone."
    ]


def normalise_notes(attrs: Mapping[str, Any]) -> list[str]:
    """A note when the denominator, a sum, was a partial sum in some
    samples. One key is no sum: a sample without its value has no
    denominator, and no value."""
    complete = attrs.get("normalise_complete")
    members = attrs.get("normalise_members") or []
    if len(members) < 2 or complete is None or complete >= 1:
        return []
    return [
        f"The denominator was a partial sum in {1 - complete:.1%} of the "
        "samples: one of its ions had no value there, and the others divided."
    ]


def face(
    attrs: Mapping[str, Any],
    zone: str,
    *,
    start: Any = None,
    end: Any = None,
) -> dict[str, Any]:
    """The summary fields every chart has (:data:`FACE_FIELDS`), from a
    view's attrs: the window's ends in *zone*, else *start* and *end* (the
    data's span)."""
    normalise = attrs.get("normalise")
    return {
        "normalise": list(normalise)
        if isinstance(normalise, (list, tuple))
        else normalise,
        "normalise_name": attrs.get("normalise_name"),
        "cadence": attrs.get("cadence"),
        "tz": zone,
        "start": moment(attrs.get("start"), zone) or moment(start, zone),
        "end": moment(attrs.get("end"), zone) or moment(end, zone),
        "mixed_runs": bool(attrs.get("mixed_runs")),
        "run_changes": run_changes(attrs, zone),
    }


def duration_text(value: Any) -> str:
    """A duration as a reader writes it: ``1h``, ``30min``, ``2 days``."""
    step = pd.Timedelta(value)
    seconds = int(step.total_seconds())
    if seconds and seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds and seconds % 60 == 0:
        return f"{seconds // 60}min"
    return f"{step.total_seconds():g}s"


# --- the species charts ---------------------------------------------------------


#: Under this share of detections, a summary names a species as read high.
SELDOM = 0.9

#: The most panels a species chart's small multiples hold, a species each.
MAX_PANELS = 24


@contextmanager
def refused() -> Iterator[None]:
    """A parameter the views or the spec refuse, raised as
    :class:`~mascope_viz.errors.InvalidChart` with their message: they
    raise a ValueError that says what to change, and a chart raises
    InvalidChart for every parameter it cannot draw."""
    try:
        yield
    except VizError:
        raise
    except ValueError as e:
        raise InvalidChart(str(e)) from e


def window_samples(store: CampaignStore, attrs: Mapping[str, Any]) -> pd.DataFrame:
    """The store's samples inside the window a view frame's *attrs* name."""
    samples = store.table("samples")
    when = samples["t_mid_utc"]
    kept = when.notna()
    if attrs.get("start"):
        kept &= when >= pd.Timestamp(attrs["start"])
    if attrs.get("end"):
        kept &= when < pd.Timestamp(attrs["end"])
    return samples[kept]


def at(value: Any, zone: str) -> str | None:
    """A time as a summary gives it: :func:`moment`, as ISO 8601 with its
    offset in *zone*; None for none."""
    when = moment(value, zone)
    return None if when is None else when.isoformat()


def depends_on_run(key: str, attrs: Mapping[str, Any]) -> bool:
    """Whether a run change moves *key*'s values: a formula's, or any key's
    divided by a formula's."""
    members = attrs.get("normalise_members") or []
    return key.startswith("formula:") or any(m.startswith("formula:") for m in members)


def ratio(top: Any, bottom: Any) -> float | None:
    """*top* over *bottom*; None unless both are finite and *bottom* is
    positive."""
    if top is None or bottom is None:
        return None
    top, bottom = float(top), float(bottom)
    if not (math.isfinite(top) and math.isfinite(bottom)) or bottom <= 0:
        return None
    return top / bottom


def presence(n: Any, n_samples: Any) -> float | None:
    """The values detected over the samples that could have held one; None
    without such a sample."""
    n, n_samples = float(np.sum(n)), float(np.sum(n_samples))
    return n / n_samples if n_samples else None


def seldom_note(entries: Sequence[Mapping[str, Any]]) -> str | None:
    """The line a figure prints for species detected in under
    :data:`SELDOM` of the samples that could hold them."""
    names = [
        e["name"]
        for e in entries
        if e.get("presence") is not None and e["presence"] < SELDOM
    ]
    if not names:
        return None
    return (
        f"Below detection is missing, not zero: {', '.join(names)} "
        f"{'was' if len(names) == 1 else 'were'} detected in under "
        f"{SELDOM:.0%} of the samples that could hold a value, and the "
        "statistics, over the detected values only, read high."
    )


def context(attrs: Mapping[str, Any]) -> Context:
    """The figure's context, from the view frame's *attrs*."""
    return Context.from_attrs(attrs)


def span(store: CampaignStore, attrs: Mapping[str, Any]) -> tuple[Any, Any]:
    """The window's start and end; where a bound is open, the first or the
    last sample's time in it. :func:`face` gives them in the zone."""
    start, end = attrs.get("start"), attrs.get("end")
    if start is None or end is None:
        # A chart draws only a window that holds a value, so a sample
        times = window_samples(store, attrs)["t_mid_utc"]
        start = start if start is not None else times.min()
        end = end if end is not None else times.max()
    return start, end


def number(value: Any) -> float | None:
    """A float, None for a value that is missing or not finite."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def summary(
    store: CampaignStore,
    attrs: Mapping[str, Any],
    resolved: Resolved,
    entries: list[dict[str, Any]],
    **extra: Any,
) -> dict[str, Any]:
    """The fields every species chart's summary carries, then *extra*.

    :param entries: Per species, in the resolved order: its numbers, after
        its ``key``, ``name`` and ``formula``.
    """
    zone = attrs["tz"]
    start, end = span(store, attrs)
    return {
        "species": entries,
        "requested": resolved.given,
        **face(attrs, zone, start=start, end=end),
        "normalise_members": list(attrs.get("normalise_members") or []),
        "normalise_complete": number(attrs.get("normalise_complete")),
        "same_ion": [same.to_dict() for same in resolved.same_ion],
        **extra,
    }


def same_ion_note(resolved: Resolved) -> str | None:
    """The line a figure prints when ``top:N`` left out species that read
    an ion it draws; None when it left none out."""
    if not resolved.same_ion:
        return None
    parts = []
    for same in resolved.same_ion:
        others = [s["name"] for s in same.left_out]
        parts.append(
            f"{resolved.name(same.key)} reads {same.ion_formula}, as "
            f"{', '.join(others)} {'does' if len(others) == 1 else 'do'}"
        )
    return (
        "top:N draws one series per ion, the species ranked first of those "
        "that read it: " + "; ".join(parts) + ". The others are left out: "
        "their values are that ion's too."
    )


def notes(
    attrs: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    resolved: Resolved | None = None,
) -> list[str]:
    """The notes every species chart prints, those that apply."""
    lines = [
        *run_notes(attrs, attrs["tz"]),
        *normalise_notes(attrs),
        same_ion_note(resolved) if resolved is not None else None,
        seldom_note(entries),
    ]
    return [line for line in lines if line]


def named(frame: pd.DataFrame, resolved: Resolved) -> pd.DataFrame:
    """*frame* with each row's species' display name, ``name``, beside its
    ``key``."""
    frame = frame.copy()
    names = {s.key: s.name for s in resolved.species}
    frame.insert(
        frame.columns.get_loc("key") + 1,
        "name",
        [names[str(k)] for k in frame["key"]],
    )
    return frame
