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

The composition charts (spectrogram, period spectrum, Van Krevelen) say
the same with their own helpers below: the signal's label
(:func:`signal_label`), the summary's context (:func:`context_summary`),
the run-change note and rules (:func:`formula_run_notes`,
:func:`formula_run_rules`), the note on a partial normaliser
(:func:`normaliser_notes`), and why a chart has nothing to draw: whether
the store holds no assigned peak (no batch ledger, or none its runs left)
or the window holds none (:func:`empty_notes`), so an empty frame is not
taken for a rendering fault.
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


def polarity_note(entries: Sequence[Mapping[str, Any]]) -> str | None:
    """The line a figure prints when its species are read in both
    polarities, naming each polarity's; None for one polarity."""
    by_polarity: dict[str, list[str]] = {}
    for entry in entries:
        if entry.get("polarity") in POLARITY_NAMES:
            by_polarity.setdefault(entry["polarity"], []).append(entry["name"])
    if len(by_polarity) < 2:
        return None
    read = "; ".join(
        f"{', '.join(by_polarity[sign])} {POLARITY_NAMES[sign]}" for sign in ("+", "-")
    )
    return (
        f"The species drawn are read in both polarities ({read}): a positive "
        "and a negative ion are two chemistries' readings, not one scale. "
        "Scope the store to one polarity, or name the species of one, to "
        "compare them."
    )


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
        polarity_note(entries),
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


# --- the composition charts -----------------------------------------------------


#: The note of a chart drawn from the store's batch peaks.
ASSIGNED_ONLY = (
    "Assigned peaks only: the store holds the batch peaks with a consensus "
    "formula, seen in two samples at least, without their isotopologues; "
    "unassigned signal is not in it."
)

#: A run change's label on a time axis.
RUN_CHANGE_LABEL = "engine run changes"

#: The fields :func:`context_summary` gives every summary.
CONTEXT_SUMMARY = (
    "normalise",
    "normalise_name",
    "normalise_members",
    "normalise_complete",
    "cadence",
    "tz",
    "start",
    "end",
    "mixed_runs",
    "run_changes",
)


def signal_variable(store: CampaignStore) -> str:
    """The variable a store's intensities are, as its samples' instrument
    type says: ``peak area`` on a TOF, ``peak height`` on an Orbitrap, and
    ``signal`` when the samples say neither or both."""
    types = store.table("samples")["instrument_type"].dropna().astype(str)
    kinds = {_instrument_kind(name) for name in types.unique()}
    if kinds == {"tof"}:
        return "peak area"
    if kinds == {"orbi"}:
        return "peak height"
    return "signal"


def _instrument_kind(name: str) -> str | None:
    """``tof`` or ``orbi`` for an instrument type the server or the store
    spells (``tof``, ``TOF``, ``orbi``, ``Orbitrap``)."""
    lowered = name.strip().lower()
    if lowered.startswith("tof"):
        return "tof"
    if lowered.startswith("orbi"):
        return "orbi"
    return None


def signal_label(store: CampaignStore, attrs: Mapping[str, Any], what: str = "") -> str:
    """What an axis of the values of a view frame says: the variable
    (:func:`signal_variable`) raw, ``signal / <normaliser>`` normalised.

    :param what: The statistic in front of it (``Mean``, ``Median``);
        none by default.
    """
    if attrs.get("normalise", "none") == "none":
        text = signal_variable(store)
    else:
        text = f"signal / {attrs.get('normalise_name') or attrs.get('normalise')}"
    if what:
        return f"{what} {text}"
    return text[:1].upper() + text[1:]


def iso(moment: Any, zone: str | None) -> str | None:
    """*moment* as ISO 8601 with its offset in *zone*; None for none."""
    if moment is None:
        return None
    stamp = pd.Timestamp(moment)
    if pd.isna(stamp):
        return None
    if stamp.tz is None:
        stamp = stamp.tz_localize("UTC")
    if zone:
        stamp = stamp.tz_convert(zone)
    return stamp.isoformat()


def wall(moment: Any, zone: str | None) -> str:
    """*moment* as a reader sees it in a note: ``2026-06-16 03:00`` in
    *zone*."""
    stamp = pd.Timestamp(moment)
    if stamp.tz is None:
        stamp = stamp.tz_localize("UTC")
    if zone:
        stamp = stamp.tz_convert(zone)
    return stamp.strftime("%Y-%m-%d %H:%M")


def zoned_run_changes(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The view's run changes, each ``at`` in the display zone."""
    zone = attrs.get("tz")
    return [
        dict(change, at=iso(change.get("at"), zone))
        for change in attrs.get("run_changes") or []
    ]


def context_summary(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """The fields every chart's summary carries, from its view frame's
    attrs: how the values are normalised, the cadence, the zone, the
    window, and whether the engine run changes in it and where."""
    zone = attrs.get("tz")
    normalise = attrs.get("normalise", "none")
    return {
        "normalise": list(normalise) if isinstance(normalise, list) else normalise,
        "normalise_name": attrs.get("normalise_name"),
        "normalise_members": list(attrs.get("normalise_members") or []),
        "normalise_complete": attrs.get("normalise_complete"),
        "cadence": attrs.get("cadence"),
        "tz": zone,
        "start": iso(attrs.get("start"), zone),
        "end": iso(attrs.get("end"), zone),
        "mixed_runs": bool(attrs.get("mixed_runs")),
        "run_changes": zoned_run_changes(attrs),
    }


def run_moments(attrs: Mapping[str, Any]) -> list[Any]:
    """The moments the view's engine run changes at, in time order, each
    once and as the view gives it: the view gives a change per followed
    polarity, so a ``+-`` batch whose reading changes gives two at one
    moment."""
    moments: dict[pd.Timestamp, Any] = {}
    for change in attrs.get("run_changes") or []:
        stamp = pd.Timestamp(change["at"])
        if stamp.tz is None:
            stamp = stamp.tz_localize("UTC")
        moments.setdefault(stamp, change["at"])
    return [moments[stamp] for stamp in sorted(moments)]


def formula_run_notes(attrs: Mapping[str, Any]) -> list[str]:
    """A note when the view's formulas come from more than one engine run:
    how often and where the run changes, a moment counted once, and that
    the two readings are not joined."""
    moments = run_moments(attrs)
    if not attrs.get("mixed_runs") or not moments:
        return []
    zone = attrs.get("tz")
    times = ", ".join(wall(moment, zone) for moment in moments)
    many = len(moments) > 1
    return [
        f"The engine run that read the formulas changes {len(moments)} "
        f"time{'s' * many}, at {times}: a formula one run reads and the other "
        "does not shows on one side of a change only, and the two readings "
        "are not joined."
    ]


def formula_run_rules(attrs: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A rule at each run change, for a panel over time; one per moment."""
    if not attrs.get("mixed_runs"):
        return []
    return [{"at": at, "label": RUN_CHANGE_LABEL} for at in run_moments(attrs)]


def normaliser_notes(attrs: Mapping[str, Any]) -> list[str]:
    """A note when the denominator was not whole in every sample: for a
    sum, the share of the samples in which it summed only some of its
    members; for one key, the share in which it had no value, which leaves
    those samples without a value."""
    complete = attrs.get("normalise_complete")
    members = attrs.get("normalise_members") or []
    if complete is None or not members or complete >= 1:
        return []
    share = percent(1 - complete)
    name = attrs.get("normalise_name") or " + ".join(members)
    if len(members) > 1:
        return [
            f"In {share} of the samples the denominator was a partial sum: "
            f"some of {name} had no value there, and the others divided."
        ]
    return [
        f"In {share} of the samples {name} had no value, so those samples "
        "have none either."
    ]


def window_name(window: Any, zone: str | None) -> str:
    """A window, ``[start, end]`` with either None for open, as a note, a
    legend or a title calls it."""
    start, end = window or (None, None)
    if start and end:
        return f"{wall(start, zone)} to {wall(end, zone)}"
    if start:
        return f"from {wall(start, zone)}"
    if end:
        return f"until {wall(end, zone)}"
    return "every sample"


#: The note of a store that holds no assigned peak, none of whose batches
#: has a ledger.
NO_LEDGER = (
    "Nothing to draw: the store holds no assigned peak. None of its batches "
    "has a batch ledger, so no assignment pass has run on them."
)

#: The note of a store that holds no assigned peak although its batches
#: have a ledger.
NO_ASSIGNED_PEAK = (
    "Nothing to draw: the store holds no assigned peak. Its batches' engine "
    "runs left no batch peak with a consensus formula seen in two samples."
)


def empty_notes(store: CampaignStore, empty: list[str]) -> list[str]:
    """Why a chart of the assigned peaks draws nothing, or nothing for a
    window: the store holds none at all, and why, or a window holds none.

    :param empty: The windows, by name (:func:`window_name`), in which no
        assigned peak has a value; none when the chart draws them all.
    :return: One note when the store holds no assigned peak; else one per
        window of *empty*.
    """
    if not empty:
        return []
    keys = store.table("species")["series_key"].astype(str)
    if not keys.str.startswith("formula:").any():
        readings = store.status().batches["reading"]
        return [NO_ASSIGNED_PEAK if readings.notna().any() else NO_LEDGER]
    return [
        f"No assigned peak has a value in {name}: the store holds some outside it."
        for name in empty
    ]


def species(key: Any, name: Any, formula: Any) -> dict[str, Any]:
    """A species as a summary names it."""
    return {"key": text(key), "name": text(name), "formula": text(formula)}


def text(value: Any) -> str | None:
    """A text for a summary: None for a missing one."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def check_count(name: str, value: Any) -> int:
    """*value* as a count a chart takes, such as ``top_n``: a whole number,
    not negative; a ValueError naming *name* otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name}={value!r} is no count")
    if value < 0:
        raise ValueError(f"{name}={value!r} is negative")
    return int(value)


def percent(share: float) -> str:
    """A share as a note, a label or a hover says it: ``12 %``, ``0.4 %``,
    ``under 0.1 %``."""
    percent = 100 * share
    if percent < 0.1:
        return "under 0.1 %"
    if percent < 10:
        return f"{percent:.1f} %"
    return f"{percent:.0f} %"
