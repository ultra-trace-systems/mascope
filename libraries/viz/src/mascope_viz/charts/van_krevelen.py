"""``van_krevelen``: what chemistry is this?

A scatter of the assigned formulas' H/C by O/C (van Krevelen 1950), drawn
from :func:`mascope_viz.data.views.composition` over the window
(``docs/dev/visual_digest.md``, section 3, composition snapshots):

- **The neutral formula's ratios**, as the store's ``o_c`` and ``h_c``
  are: the reagent adduct is not counted. A formula without carbon has no
  place on it and is left out, and counted.
- **Assigned formulas by default.** A candidate is a plausible formula
  with real alternatives open, not an identification; asked for, the
  candidates are drawn hollow and counted apart.
- **Marker area in proportion to the median signal** over the samples
  with a value, larger markers behind, with a size legend. Signal is not
  abundance: the sensitivity differs from compound to compound.
- **Colour** by carbon number, by compound class (the store's ``class``:
  CHO, CHON, CHOS, CHONS, the rest as ``other``), or by amplitude, the cv
  of the hourly medians (:func:`mascope_viz.data.views.amplitude`), which
  under raw values includes the instrument's drift.
- **The summary's means**, weighted by the median signal ``w``:
  ``oc_weighted`` is the sum of ``w * O/C`` over the sum of ``w`` (and
  ``hc_weighted`` likewise), the mean of the points' ratios; ``oc_bulk``
  is the sum of ``w * n_O`` over the sum of ``w * n_C`` (and ``hc_bulk``
  likewise), the ratio of the summed atoms. Both are in use, and they
  differ.
"""

from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from . import common, registry
from .spec import Context, Figure, Panel, subtitle


#: What the points are coloured by.
COLOURS = ("n_c", "class", "amplitude")

#: The compound classes drawn apart, in order; the rest are ``other``.
CLASSES = ("CHO", "CHON", "CHOS", "CHONS")

#: Each class's colour token, so that a class keeps its colour in every
#: Van Krevelen.
CLASS_COLOURS = {
    "CHO": "series.1",
    "CHON": "series.2",
    "CHOS": "series.3",
    "CHONS": "series.4",
    "other": "muted",
}

#: The cadence the amplitude's medians are binned at.
AMPLITUDE_CADENCE = "1h"


def van_krevelen(
    store: CampaignStore,
    *,
    colour: str = "n_c",
    tiers: Any = ("assigned",),
    normalise: Any = "none",
    tz: str | None = None,
    start: Any = None,
    end: Any = None,
    top_n: int = 10,
) -> Figure:
    """The assigned formulas' H/C by O/C over a window.

    :param colour: ``n_c`` (carbon number), ``class`` or ``amplitude``.
    :param tiers: The tiers drawn (:data:`mascope_viz.data.views.TIERS`);
        ``assigned`` alone by default. Any other is drawn hollow.
    :param normalise: ``none``, ``tic``, a series key, or a list of keys
        whose sum divides.
    :param tz: The zone a time without one is in.
    :param start: The window's start, if any.
    :param end: The window's end, if any.
    :param top_n: How many of the largest points are labelled and listed.
    """
    if colour not in COLOURS:
        raise ValueError(f"colour={colour!r} is none of {', '.join(COLOURS)}")
    drawn_tiers = _check_tiers(tiers)
    top_n = common.check_count("top_n", top_n)
    frame = views.composition(
        store, "all", normalise=normalise, tz=tz, start=start, end=end
    )
    attrs = frame.attrs
    formulas = frame[frame["key"].astype(str).str.startswith("formula:")]
    with_carbon = (
        (formulas["n_c"].fillna(0).astype("float64") > 0)
        & formulas["o_c"].notna()
        & formulas["h_c"].notna()
    )
    placed = formulas[with_carbon]
    tier = placed["tier"].astype(object).where(placed["tier"].notna(), None)
    shown = placed[tier.isin(drawn_tiers).to_numpy()].reset_index(drop=True)
    data = _points(shown)
    notes = [
        "O/C and H/C are those of the neutral formula: the reagent adduct is "
        "not counted.",
        "Marker area is in proportion to the median signal over the samples "
        "with a value. Signal is not abundance: the sensitivity differs from "
        "compound to compound.",
    ]
    without = int((~with_carbon).sum())
    if without:
        notes.append(
            f"{without} formula{'s' * (without > 1)} without carbon "
            f"{'have' if without > 1 else 'has'} no place on it and "
            f"{'are' if without > 1 else 'is'} left out."
        )
    left = placed[~tier.isin(drawn_tiers).to_numpy()]
    if len(left):
        counts = left["tier"].astype(object).fillna("no tier").value_counts()
        listed = ", ".join(f"{n} of tier {name}" for name, n in counts.items())
        notes.append(
            f"Not drawn: {listed}. A candidate is not an identification; "
            "asked for, its tier is drawn hollow."
        )
    shared = _shared(data)
    if shared:
        notes.append(
            f"{len(shared)} point{'s' * (len(shared) > 1)} "
            f"{'hold' if len(shared) > 1 else 'holds'} more than one formula "
            "of the same ratios (such as C5H8O4 and C10H16O8): the summary "
            "lists them."
        )
    colour_axis: dict[str, Any] = {}
    encodings = {
        "x": "o_c",
        "y": "h_c",
        "size": "median",
        "label": "formula",
        "hollow": "hollow",
    }
    options: dict[str, Any] = {
        "top_n": top_n,
        "hollow_label": "candidate, not an identification",
    }
    if colour == "n_c":
        encodings["colour"] = "n_c"
        colour_axis = {"label": "Carbon number"}
    elif colour == "class":
        encodings["colour"] = "group"
        options["colours"] = CLASS_COLOURS
    else:
        cv = views.amplitude(
            store,
            list(data["key"]),
            cadence=AMPLITUDE_CADENCE,
            normalise=normalise,
            tz=tz,
            start=start,
            end=end,
        ).set_index("key")["cv"]
        data["cv"] = data["key"].map(cv).astype("float64")
        encodings["colour"] = "cv"
        colour_axis = {"label": f"Amplitude, cv of the {AMPLITUDE_CADENCE} medians"}
        if attrs.get("normalise", "none") == "none":
            notes.append(
                "The amplitude of raw values includes the instrument's drift: "
                "normalise by the reagent ions to take it out."
            )
    notes += common.formula_run_notes(attrs) + common.normaliser_notes(attrs)
    # One point per formula over the whole window
    attrs = dict(attrs, cadence="window")
    context = Context.from_attrs(dict(attrs, stat="median"))
    tier_text = " and ".join(drawn_tiers)
    return Figure(
        "van_krevelen",
        "Van Krevelen of the formulas",
        subtitle(
            context, extra=[f"tier {tier_text}", f"coloured by {_COLOUR_NAMES[colour]}"]
        ),
        panels=[
            Panel(
                "scatter",
                axes={
                    "x": {"label": "O/C (neutral formula)"},
                    "y": {"label": "H/C (neutral formula)"},
                    "colour": colour_axis,
                    "size": {"label": common.signal_label(store, attrs, "Median")},
                },
                encodings=encodings,
                options=options,
            )
        ],
        summary=_summary(
            formulas, placed, data, shared, attrs, colour, drawn_tiers, top_n
        ),
        data=data,
        notes=notes,
        context=context,
    )


_COLOUR_NAMES = {
    "n_c": "carbon number",
    "class": "compound class",
    "amplitude": "amplitude",
}


def _check_tiers(tiers: Any) -> list[str]:
    if isinstance(tiers, str):
        tiers = [tiers]
    chosen = list(dict.fromkeys(str(t) for t in tiers))
    if not chosen:
        raise ValueError("tiers is empty: name at least one, such as 'assigned'")
    unknown = [t for t in chosen if t not in views.TIERS]
    if unknown:
        raise ValueError(
            f"tiers={unknown!r}: a tier is one of {', '.join(views.TIERS)}"
        )
    return chosen


def _points(shown: pd.DataFrame) -> pd.DataFrame:
    """The chart's frame: a point per formula, with what places, sizes and
    colours it, and its presence."""
    group = shown["class"].astype(object).where(shown["class"].isin(CLASSES), "other")
    with np.errstate(divide="ignore", invalid="ignore"):
        presence = shown["n"].to_numpy("float64") / shown["n_samples"].to_numpy(
            "float64"
        )
    return pd.DataFrame(
        {
            "key": shown["key"].astype(str).to_numpy(),
            "name": shown["name"].astype(object).to_numpy(),
            "formula": shown["formula"].astype(object).to_numpy(),
            "ion_formula": shown["ion_formula"].astype(object).to_numpy(),
            "class": shown["class"].astype(object).to_numpy(),
            "group": pd.Categorical(group, categories=[*CLASSES, "other"]),
            "tier": shown["tier"].astype(object).to_numpy(),
            "hollow": (shown["tier"].astype(object) != "assigned").to_numpy(bool),
            "o_c": shown["o_c"].to_numpy("float64"),
            "h_c": shown["h_c"].to_numpy("float64"),
            "n_c": shown["n_c"].to_numpy("float64"),
            "n_h": shown["n_h"].to_numpy("float64"),
            "n_o": shown["n_o"].to_numpy("float64"),
            "n_n": shown["n_n"].to_numpy("float64"),
            "n_s": shown["n_s"].to_numpy("float64"),
            "median": shown["median"].to_numpy("float64"),
            "mean": shown["mean"].to_numpy("float64"),
            "n": shown["n"].to_numpy("int64"),
            "n_samples": shown["n_samples"].to_numpy("int64"),
            "presence": presence,
        }
    )


def _shared(data: pd.DataFrame) -> list[pd.DataFrame]:
    """The points more than one formula shares: those with one O/C and one
    H/C, to the ninth decimal."""
    if data.empty:
        return []
    place = pd.DataFrame(
        {"o_c": data["o_c"].round(9), "h_c": data["h_c"].round(9)}, index=data.index
    )
    groups = data.groupby([place["o_c"], place["h_c"]], sort=True)
    return [rows for _, rows in groups if len(rows) > 1]


def _summary(
    formulas: pd.DataFrame,
    placed: pd.DataFrame,
    data: pd.DataFrame,
    shared: list[pd.DataFrame],
    attrs: dict[str, Any],
    colour: str,
    tiers: list[str],
    top_n: int,
) -> dict[str, Any]:
    w = data["median"].to_numpy("float64")
    kept = np.isfinite(w) & (w > 0)

    def weighted(numerator: np.ndarray, denominator: np.ndarray) -> float | None:
        top = float(np.sum(w[kept] * numerator[kept]))
        bottom = float(np.sum(w[kept] * denominator[kept]))
        return top / bottom if bottom > 0 else None

    ones = np.ones(len(data))
    by_tier = (
        placed["tier"].astype(object).fillna("no tier").value_counts().sort_index()
    )
    by_class = data["group"].value_counts().reindex([*CLASSES, "other"], fill_value=0)
    largest = data.sort_values(
        ["median", "key"], ascending=[False, True], kind="stable"
    ).head(top_n)
    return {
        **common.context_summary(attrs),
        "stat": "median",
        "colour": colour,
        "tiers": list(tiers),
        "n_formulas": len(formulas),
        "n_without_carbon": len(formulas) - len(placed),
        "n_points": len(data),
        "n_hollow": int(data["hollow"].sum()),
        "by_tier": {str(k): int(v) for k, v in by_tier.items()},
        "by_class": {str(k): int(v) for k, v in by_class.items()},
        "oc_weighted": weighted(data["o_c"].to_numpy(), ones),
        "hc_weighted": weighted(data["h_c"].to_numpy(), ones),
        "oc_bulk": weighted(data["n_o"].to_numpy(), data["n_c"].to_numpy()),
        "hc_bulk": weighted(data["n_h"].to_numpy(), data["n_c"].to_numpy()),
        "n_shared_points": len(shared),
        "shared_points": [
            {
                "o_c": float(rows["o_c"].iloc[0]),
                "h_c": float(rows["h_c"].iloc[0]),
                "species": [
                    common.species(r.key, r.name, r.formula) for r in rows.itertuples()
                ],
            }
            for rows in shared
        ],
        "top": [
            {
                "species": common.species(r.key, r.name, r.formula),
                "median": common.number(r.median),
                "presence": common.number(r.presence),
                "o_c": float(r.o_c),
                "h_c": float(r.h_c),
                "tier": common.text(r.tier),
            }
            for r in largest.itertuples()
        ],
    }


KIND = registry.register(
    registry.ChartKind(
        id="van_krevelen",
        question="What chemistry is this?",
        tier=1,
        series="every formula key (the assigned formulas), over the window",
        params=(
            registry.Param(
                "colour",
                "text",
                "n_c",
                COLOURS,
                help="colour by carbon number, compound class or amplitude",
            ),
            registry.Param(
                "tiers",
                "texts",
                ["assigned"],
                help=f"the tiers drawn, of {', '.join(views.TIERS)}; others hollow",
            ),
            registry.Param(
                "normalise",
                "normalise",
                "none",
                help="none, tic, a series key, or keys whose sum divides",
            ),
            registry.Param("tz", "text", None, help="the zone times are in"),
            registry.Param("start", "time", None, help="the window's start"),
            registry.Param("end", "time", None, help="the window's end"),
            registry.Param(
                "top_n", "int", 10, help="the largest points labelled and listed"
            ),
        ),
        summary=(
            *common.CONTEXT_SUMMARY,
            "stat",
            "colour",
            "tiers",
            "n_formulas",
            "n_without_carbon",
            "n_points",
            "n_hollow",
            "by_tier",
            "by_class",
            "oc_weighted",
            "hc_weighted",
            "oc_bulk",
            "hc_bulk",
            "n_shared_points",
            "shared_points",
            "top",
        ),
        build=van_krevelen,
    )
)
