"""``scope_summary``: what is this digest about?
(``docs/dev/visual_digest.md``, section 3.)

A table for a digest's cover: the datasets, batches, samples and files, the
span and its zone, the instrument and what its values are, the chemistry,
the reagent ions named, the collections, the engine runs and where they
change, the normalisation and its members, the cadence and the native
sample spacing, the coverage, the rule that below detection is missing, the
package versions, the server's provenance and when it was made. A run change
is a row of its own, marked.
"""

from collections.abc import Sequence
from importlib import metadata
from typing import Any

import numpy as np
import pandas as pd

from ..data import views
from ..data.store import CampaignStore
from . import registry
from .common import (
    NORMALISE_PARAM,
    POLARITY_NAMES,
    duration_text,
    face,
    intensity_variable,
    moment,
    run_changes,
    species_refs,
)
from .coverage import numbers
from .registry import ChartKind, Param
from .spec import Context, Figure, Panel


#: What a marked row means.
RUN_CHANGE = "the engine run changes: a formula is two readings across it"

#: The rule every digest states.
BELOW_DETECTION = "missing, not zero: statistics are over the detected values only"


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _normaliser(normalise: Any) -> tuple[Any, list[str]]:
    """*normalise* as the views take it, and its series keys."""
    if isinstance(normalise, str):
        return normalise, [] if normalise in ("none", views.TIC) else [normalise]
    keys = list(normalise)
    if not keys or not all(isinstance(key, str) for key in keys):
        raise ValueError(f"normalise={normalise!r} is not a list of series keys")
    if len(keys) == 1:
        return keys[0], keys
    return keys, keys


def _names(values: Sequence[Any]) -> str:
    texts = [str(v) for v in values if v is not None and not pd.isna(v)]
    return ", ".join(texts) if texts else "none"


def scope_summary(
    store: CampaignStore,
    *,
    reagents: Sequence[str] | str | None = None,
    normalise: Any = "none",
    cadence: str = "auto",
    generated_at: Any = None,
    tz: str | None = None,
) -> Figure:
    """What a store holds, as a cover's table.

    :param reagents: The reagent's ions a digest names, as series keys.
    :param normalise: What the digest's charts divide by (the views'
        ``normalise``): its members are listed by name.
    :param cadence: The digest's cadence: ``auto`` (the native spacing,
        rounded up to whole minutes), ``raw`` or an offset.
    :param generated_at: When the digest was made; now when not given. A
        digest that must be the same bytes every run pins it.
    :param tz: The zone times are shown in.
    """
    zone = views.display_zone(store, tz)
    normalise, members = _normaliser(normalise)
    reagent_keys = (
        []
        if reagents is None
        else [reagents]
        if isinstance(reagents, str)
        else list(reagents)
    )
    refs = species_refs(store, [*reagent_keys, *members])
    status = store.status()
    meta = store.meta
    samples = store.table("samples")
    found = views.coverage(store, tz=zone)
    covered = numbers(found)
    changes = run_changes(
        {"run_changes": [change.to_dict() for change in status.run_changes]}, zone
    )

    datasets = meta.get("datasets") or [
        {"id": stamp.get("dataset_id"), "name": None}
        for stamp in meta.get("batches", {}).values()
        if stamp.get("dataset_id")
    ]
    datasets = list({d["id"]: d for d in datasets}.values())
    collections = meta.get("collections") or (
        [{"id": meta["collection"], "name": None}] if meta.get("collection") else []
    )
    instruments = sorted(samples["instrument"].dropna().astype(str).unique())
    types = sorted(samples["instrument_type"].dropna().astype(str).unique())
    polarities = sorted(samples["polarity"].dropna().astype(str).unique())
    modes = sorted(samples["ionization_mode_name"].dropna().astype(str).unique())
    times = np.unique(pd.DatetimeIndex(samples["t_mid_utc"].dropna()).asi8)
    spacing = (
        pd.Timedelta(float(np.median(np.diff(times))), unit="ns").round("1s")
        if len(times) >= 2
        else None
    )
    if cadence == "auto":
        cadence_text = (
            f"auto: {duration_text(views.auto_cadence(samples['t_mid_utc']))}"
            if len(times)
            else "auto"
        )
    else:
        cadence_text = str(cadence)
    if normalise == "none":
        normalise_name = None
    elif normalise == views.TIC:
        normalise_name = "TIC"
    else:
        normalise_name = " + ".join(refs[key]["name"] for key in members)
    runs = status.runs
    made = moment(generated_at, zone) or pd.Timestamp.now(tz="UTC").tz_convert(zone)
    provenance = meta.get("provenance") or {}
    produced = provenance.get("produced_with") or {}
    versions = {
        "mascope_viz": _version("mascope_viz"),
        "mascope_sdk": _version("mascope_sdk"),
    }

    def when(at: Any) -> str:
        at = moment(at, zone)
        return "unknown" if at is None else f"{at:%Y-%m-%d %H:%M}"

    rows: list[tuple[str, str, bool]] = [
        ("Datasets", _names([d["name"] or d["id"] for d in datasets]), False),
        ("Batches", str(len(status.batches)), False),
        ("Samples", str(len(samples)), False),
        ("Files", f"{status.n_files}, {status.n_unfiled} of them in no batch", False),
        ("Span", f"{when(found.start)} to {when(found.end)}", False),
        ("Time zone", zone, False),
        (
            "Instrument",
            _names(instruments) + (f" ({_names(types)})" if types else ""),
            False,
        ),
        ("Intensity", intensity_variable(store) or "unknown", False),
        (
            "Chemistry",
            "; ".join(
                part
                for part in (
                    _names([POLARITY_NAMES.get(p, p) for p in polarities]),
                    f"modes {_names(modes)}" if modes else "",
                )
                if part
            ),
            False,
        ),
        (
            "Reagent ions",
            _names([refs[key]["name"] for key in reagent_keys])
            if reagent_keys
            else "none named",
            False,
        ),
        ("Collections", _names([c["name"] or c["id"] for c in collections]), False),
        (
            "Engine runs",
            _names(
                f"{run.reading} ({run.n_batches} batches)" for run in runs.itertuples()
            )
            + ("; they change, see below" if changes else ""),
            bool(changes),
        ),
    ]
    for change in changes:
        polarity = POLARITY_NAMES.get(change["polarity"], change["polarity"])
        rows.append(
            (
                "Run change",
                f"{when(change['at'])}, {polarity}: {change['before']} to "
                f"{change['after']}",
                True,
            )
        )
    rows += [
        ("Normalisation", normalise_name or "none: raw values", False),
        (
            "Normaliser members",
            _names([refs[key]["name"] for key in members]) if members else "none",
            False,
        ),
        ("Cadence", cadence_text, False),
        (
            "Sample spacing",
            "unknown" if spacing is None else f"{duration_text(spacing)} (median)",
            False,
        ),
        (
            "Coverage",
            "none"
            if covered["coverage_fraction"] is None
            else f"{covered['coverage_fraction']:.1%} of the span acquired",
            False,
        ),
        ("Below detection", BELOW_DETECTION, False),
        (
            "Versions",
            ", ".join(
                f"{name} {version}" for name, version in versions.items() if version
            )
            or "unknown",
            False,
        ),
        (
            "Server",
            ", ".join(
                f"{label} {produced[name]}"
                for label, name in (
                    ("Mascope", "mascope_version"),
                    ("engine", "peak_assignment_engine_version"),
                )
                if produced.get(name)
            )
            or "unknown",
            False,
        ),
        ("Store synced", when(status.synced_utc), False),
        ("Generated", when(made), False),
    ]
    data = pd.DataFrame(
        {
            "item": [row[0] for row in rows],
            "value": [row[1] for row in rows],
            "run_change": np.array([row[2] for row in rows], dtype=bool),
        }
    )
    summary = {
        **face(
            {
                "normalise": normalise,
                "normalise_name": normalise_name,
                "cadence": cadence_text,
                "mixed_runs": status.mixed_runs,
            },
            zone,
            start=found.start,
            end=found.end,
        ),
        "run_changes": changes,
        "datasets": datasets,
        "n_batches": len(status.batches),
        "n_samples": len(samples),
        "n_files": status.n_files,
        "n_unfiled": status.n_unfiled,
        "span": {"start": moment(found.start, zone), "end": moment(found.end, zone)},
        "instruments": instruments,
        "instrument_types": types,
        "intensity_variable": intensity_variable(store),
        "polarities": polarities,
        "modes": modes,
        "reagents": [refs[key] for key in reagent_keys],
        "collections": collections,
        "runs": [
            {
                "reading": run.get("reading"),
                "engine": run.get("engine"),
                "engine_version": run.get("engine_version"),
                "n_batches": int(run.get("n_batches") or 0),
                "start": moment(run.get("first_datetime_utc"), zone),
                "end": moment(run.get("last_datetime_utc"), zone),
            }
            for run in runs.to_dict("records")
        ],
        "normalise_members": [refs[key] for key in members],
        "sample_spacing_seconds": None if spacing is None else spacing.total_seconds(),
        "coverage_fraction": covered["coverage_fraction"],
        "below_detection": "missing",
        "versions": versions,
        "provenance": provenance or None,
        "synced_at": moment(status.synced_utc, zone),
        "generated_at": made,
    }
    context = Context(
        cadence=cadence_text,
        tz=zone,
        normalise=normalise,
        normalise_name=normalise_name,
        normalise_members=members,
        mixed_runs=status.mixed_runs,
        run_changes=[change.to_dict() for change in status.run_changes],
    )
    notes = []
    if changes:
        notes.append(
            "The engine run that reads the formulas changes in this scope: "
            "every chart marks the change and joins no formula across it."
        )
    panel = Panel(
        "table",
        encodings={"columns": ["item", "value"], "mark": "run_change"},
        options={
            "headers": {"item": "", "value": ""},
            "mark_label": RUN_CHANGE,
        },
    )
    return Figure(
        "scope_summary",
        "Scope",
        f"{len(samples)} samples, {when(found.start)} to {when(found.end)}; "
        f"times in {zone}",
        panels=[panel],
        summary=summary,
        data=data,
        notes=notes,
        context=context,
    )


KIND = registry.register(
    ChartKind(
        id="scope_summary",
        question="What is this digest about?",
        tier=1,
        series="the store: its listings, batches, samples, files and runs",
        params=(
            Param(
                "reagents",
                "keys",
                None,
                help="the reagent's ions the digest names, as series keys",
            ),
            NORMALISE_PARAM,
            Param("cadence", "cadence", "auto", help="the digest's cadence"),
            Param(
                "generated_at",
                "time",
                None,
                help="when the digest was made; now when not given",
            ),
            Param(
                "tz",
                "text",
                None,
                help="the zone times are shown in; the store's display zone "
                "when not given",
            ),
        ),
        summary=(
            "datasets",
            "n_batches",
            "n_samples",
            "n_files",
            "n_unfiled",
            "span",
            "instruments",
            "instrument_types",
            "intensity_variable",
            "polarities",
            "modes",
            "reagents",
            "collections",
            "runs",
            "normalise_members",
            "sample_spacing_seconds",
            "coverage_fraction",
            "below_detection",
            "versions",
            "provenance",
            "synced_at",
            "generated_at",
            "normalise",
            "normalise_name",
            "cadence",
            "tz",
            "start",
            "end",
            "mixed_runs",
            "run_changes",
        ),
        build=scope_summary,
    )
)
