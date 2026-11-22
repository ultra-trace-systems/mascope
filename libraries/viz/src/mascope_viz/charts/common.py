"""What the chart kinds share: the face every chart carries (the intensity
variable on its axis, the run changes drawn, the notes a reader needs) and
the summary fields every chart's summary has.

A chart is a view call plus spec building (``docs/dev/visual_digest.md``,
section 2.4): the views aggregate, and a chart only arranges what they give
and says what it is. Pandas and NumPy only, so a server can build the
charts without the renderers.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from .registry import Param


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
    """A note when the denominator was a partial sum in some samples."""
    complete = attrs.get("normalise_complete")
    if complete is None or complete >= 1:
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
