"""
Maintenance script to audit the m/z calibration of one TOF instrument: refit
its files' stored peaks under named calibrant sets and models, as the node
does, and say how good each fit is per ion and per mass band.

The yardstick of the TOF mass calibration plan (``docs/dev/tof_calibration.md``,
milestone 0): every change to a calibrant set or to the node is measured with
it before and after, in ppm per mass band rather than one RMS.

For each file of the instrument in the date range (all of them, or a sample
per day), and for each ionization mode they were acquired in, it:

1. reads the file's kept peaks from ``peak_timeseries`` and its stored
   calibration record;
2. refits it with the node's own handler (``tof_calibration_audit``):
   the same matching, the same 15 ppm residual rule, the same exact subset
   search for five matches or fewer, the same fit - under the mode's
   calibration collection and every named set, in every model asked for;
3. checks that the refit under the mode's collection, with the tolerance and
   refine window the record was fitted with, lands on the stored fit to
   0.01 ppm, on every file the node fitted;
4. evaluates every fit, and the stored record, on every ion any set or
   cluster series matched in the file, and on the file's strong peaks against
   the CHO family of each of the mode's mechanisms.

It reports, per mode and per fit:

- each ion line's median and IQR error where the fit used it, and where it
  did not (held out);
- each calibrant's presence rate (matched) and how often the fit kept it;
- per mass band, the fitted and the held-out error, and the composition-free
  family offset: how far strong peaks sit from the nearest CcHhOo member
  under each mechanism (CHO.C3H10N+, CHO.NO3-, CHO.Br-, bare CHO for
  [M-H]- and [M+H]+...), which needs no identity to be right;
- the cluster series (HSO4-.(H2SO4)n, NO3-.(HNO3)n, Br-.(H2O)n) member by
  member.

**It writes nothing.** It says so to the runner with ``WRITES_NOTHING``, so no
``pg_dump`` is taken before it. It opens no write session, reads each file's
peak store, ``.props`` and scan times, and never computes a peak timeseries
the store lacks (the node would, and store it): such a candidate is ranked by
its stored sum, and the report counts them. So it can run inside the backend
container on a site, or on a copy of a site's database and filestore.

Configuration, all through the environment:

- ``TOF_AUDIT_INSTRUMENT``: the instrument, as files name it. Required.
- ``TOF_AUDIT_FROM`` / ``TOF_AUDIT_TO``: the acquisition dates, inclusive,
  as ``YYYY-MM-DD`` (UTC). Default: the seven days up to today.
- ``TOF_AUDIT_PER_DAY``: files per day, spread over the day; 0 (default)
  for every file.
- ``TOF_AUDIT_MODE``: only the files of the ionization mode of this name.
- ``TOF_AUDIT_SETS``: named calibrant sets, as JSON: a name to a list of
  compound formulas, which the mode's mechanisms make ions of exactly as a
  collection would, or to ``{"collection": "<id or name>"}``. The mode's
  own calibration collection is always audited, as ``collection``.
  ``{"proposed": ["C3H9N", "C6H18N2", "C13H25NO2"]}``
- ``TOF_AUDIT_MODELS``: Tofwerk modes to fit, comma separated; default
  ``0``. Mode 2 is accepted once the library fits it.
- ``TOF_AUDIT_BANDS``: mass band edges in Th; default
  ``40,100,150,200,300,500``.
- ``TOF_AUDIT_FAMILIES``: mechanisms whose CHO family is measured, comma
  separated (``[M+NO3]-,[M-H]-``); default the mode's.
- ``TOF_AUDIT_SERIES``: cluster series as JSON, a name to
  ``{"core": "HSO4", "unit": "H2SO4", "n": 4, "charge": "-"}``, or ``none``;
  default the three negative-ion series above.
- ``TOF_AUDIT_FAMILY_SNR`` (20), ``TOF_AUDIT_FAMILY_WINDOW_PPM`` (30),
  ``TOF_AUDIT_FAMILY_DBE_MAX`` (3): the family yardstick.
- ``TOF_AUDIT_JSON=1``: also print the whole result as one JSON document,
  per-file fits included, as the last line of stdout.

Usage:
    TOF_AUDIT_INSTRUMENT=<name> mascope dev db script run audit_tof_calibration
    TOF_AUDIT_INSTRUMENT=<name> TOF_AUDIT_FROM=2026-10-12 TOF_AUDIT_TO=2026-10-18 \\
        TOF_AUDIT_SETS='{"proposed": ["C13H25NO2"]}' \\
        mascope prod db script run audit_tof_calibration

Date: 2026-10-20
"""

import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import or_, select

import mascope_file.io as m_io
from mascope_backend.api.controllers.calibration.calibration_controller import (
    is_unfitted_record,
)
from mascope_backend.api.controllers.calibration.lib import (
    tof_calibration_audit as audit,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    TofCalibrationHandler,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    TofCalibrationParams,
)
from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetIon,
    async_session,
    configure_database_engine,
)
from mascope_backend.runtime import runtime
from mascope_tofwerk.calibration import tof_to_mass


#: Read by ``mascope dev|prod db script run``, which then takes no pre-script
#: backup (``mascope_cli.pg.script_writes_nothing``). It is a promise about
#: this module: nothing here opens a write session, and all it touches
#: outside the database is reading each file's peak store, ``.props`` and
#: scan times (``tof_calibration_audit``).
WRITES_NOTHING = True

#: Files refitted at once: each is a few reads and a handful of small fits.
_FILE_CONCURRENCY = 4

#: The fit the stored record is reported as.
STORED = "stored"
#: The calibrant set every mode is audited with: its calibration collection.
COLLECTION = "collection"

#: The refit under the mode's collection must land within this of the stored
#: fit, over the span of its calibrants, to count as the node's.
REPRODUCED_PPM = 0.01

#: Files a report names where the refit did not land on the stored fit.
_MISMATCHES_LISTED = 10


# --- Configuration ---


@dataclass(frozen=True)
class Config:
    instrument: str
    first_day: date
    last_day: date
    per_day: int
    mode: str | None
    sets: dict
    models: tuple[int, ...]
    band_edges: tuple[float, ...]
    families: tuple[str, ...] | None
    series: dict
    family_snr: float
    family_window_ppm: float
    family_dbe_max: int
    as_json: bool


def _env(name: str) -> str | None:
    raw = os.environ.get(name)
    return None if raw is None or raw.strip() == "" else raw.strip()


def _number_env(name: str, default, kind, minimum):
    """A bound from the environment, or raise.

    A bound is part of what the report says about itself, so a value that
    cannot be read stops the run rather than falling back to the default.
    """
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a number. Unset it for {default}.")
    if value < minimum:
        raise ValueError(f"{name}={raw!r} is below {minimum}.")
    return value


def _json_env(name: str, default):
    raw = _env(name)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} is not valid JSON: {error}") from None


def _day_env(name: str, default: date) -> date:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a date (YYYY-MM-DD).") from None


def _check_sets(sets) -> dict:
    if not isinstance(sets, dict):
        raise ValueError("TOF_AUDIT_SETS must map set names to sets.")
    for name, spec in sets.items():
        if name in (STORED, COLLECTION):
            raise ValueError(f"TOF_AUDIT_SETS: '{name}' is a reserved name.")
        is_formulas = isinstance(spec, list) and all(isinstance(f, str) for f in spec)
        is_collection = isinstance(spec, dict) and isinstance(
            spec.get("collection"), str
        )
        if not (is_formulas and spec) and not is_collection:
            raise ValueError(
                f"TOF_AUDIT_SETS: set '{name}' must be a list of formulas or "
                '{"collection": "<id or name>"}.'
            )
    return sets


def _check_series(series) -> dict:
    if series == "none":
        return {}
    if not isinstance(series, dict):
        raise ValueError("TOF_AUDIT_SERIES must map series names to series.")
    for name, spec in series.items():
        if not (
            isinstance(spec, dict)
            and isinstance(spec.get("core"), str)
            and isinstance(spec.get("unit"), str)
            and isinstance(spec.get("n"), int)
            and spec.get("charge", "-") in ("+", "-")
        ):
            raise ValueError(
                f"TOF_AUDIT_SERIES: series '{name}' must be "
                '{"core": ..., "unit": ..., "n": <int>, "charge": "+" or "-"}.'
            )
    return series


def read_config(today: date | None = None) -> Config:
    """The audit's configuration, from the environment.

    :raises ValueError: On anything that cannot be read, or reads nothing.
    """
    instrument = _env("TOF_AUDIT_INSTRUMENT")
    if instrument is None:
        raise ValueError("Set TOF_AUDIT_INSTRUMENT to the instrument to audit.")
    today = today or datetime.now(UTC).date()
    last_day = _day_env("TOF_AUDIT_TO", today)
    first_day = _day_env("TOF_AUDIT_FROM", last_day - timedelta(days=6))
    if first_day > last_day:
        raise ValueError("TOF_AUDIT_FROM is after TOF_AUDIT_TO.")

    models_raw = _env("TOF_AUDIT_MODELS") or "0"
    try:
        models = tuple(int(part) for part in models_raw.split(","))
    except ValueError:
        raise ValueError(f"TOF_AUDIT_MODELS={models_raw!r} is not a list of modes.")
    for model in models:
        if model not in audit.MODELS:
            raise ValueError(
                f"TOF_AUDIT_MODELS: mode {model} cannot be fitted yet; "
                f"fittable: {', '.join(str(m) for m in sorted(audit.MODELS))}."
            )

    bands_raw = _env("TOF_AUDIT_BANDS")
    if bands_raw is None:
        band_edges = audit.DEFAULT_BAND_EDGES
    else:
        try:
            band_edges = tuple(float(part) for part in bands_raw.split(","))
        except ValueError:
            raise ValueError(f"TOF_AUDIT_BANDS={bands_raw!r} is not a list of m/z.")
        if list(band_edges) != sorted(set(band_edges)):
            raise ValueError("TOF_AUDIT_BANDS must rise.")

    families_raw = _env("TOF_AUDIT_FAMILIES")
    return Config(
        instrument=instrument,
        first_day=first_day,
        last_day=last_day,
        per_day=_number_env("TOF_AUDIT_PER_DAY", 0, int, 0),
        mode=_env("TOF_AUDIT_MODE"),
        sets=_check_sets(_json_env("TOF_AUDIT_SETS", {})),
        models=models,
        band_edges=band_edges,
        families=(
            None
            if families_raw is None
            else tuple(part.strip() for part in families_raw.split(","))
        ),
        series=_check_series(
            _json_env("TOF_AUDIT_SERIES", audit.DEFAULT_CLUSTER_SERIES)
            if _env("TOF_AUDIT_SERIES") != "none"
            else "none"
        ),
        family_snr=_number_env(
            "TOF_AUDIT_FAMILY_SNR", audit.DEFAULT_FAMILY_SNR, float, 0
        ),
        family_window_ppm=_number_env(
            "TOF_AUDIT_FAMILY_WINDOW_PPM", audit.DEFAULT_FAMILY_WINDOW_PPM, float, 0
        ),
        family_dbe_max=_number_env(
            "TOF_AUDIT_FAMILY_DBE_MAX", audit.DEFAULT_FAMILY_DBE_MAX, int, 0
        ),
        as_json=_env("TOF_AUDIT_JSON") == "1",
    )


# --- The files ---


@dataclass(frozen=True)
class FileRow:
    sample_file_id: str
    filename: str
    acquired: datetime
    ionization_mode_id: str | None


def sample_per_day(rows: list[FileRow], per_day: int) -> list[FileRow]:
    """Every file, or ``per_day`` of each day's spread evenly over the day."""
    if per_day <= 0:
        return rows
    by_day = defaultdict(list)
    for row in rows:
        by_day[row.acquired.date()].append(row)
    sampled = []
    for day in sorted(by_day):
        files = sorted(by_day[day], key=lambda row: (row.acquired, row.filename))
        picks = np.unique(
            np.linspace(0, len(files) - 1, min(per_day, len(files))).round()
        )
        sampled.extend(files[int(i)] for i in picks)
    return sampled


async def select_files(config: Config) -> list[FileRow]:
    """The instrument's TOF files acquired in the range, oldest first, one row
    per file with the mode of its sample items."""
    start = datetime.combine(config.first_day, time.min, tzinfo=UTC)
    end = datetime.combine(config.last_day + timedelta(days=1), time.min, tzinfo=UTC)
    async with async_session() as session:
        result = await session.execute(
            select(
                SampleFile.sample_file_id,
                SampleFile.filename,
                SampleFile.datetime_utc,
                SampleItem.ionization_mode_id,
            )
            .join(SampleItem, SampleItem.sample_file_id == SampleFile.sample_file_id)
            .where(
                SampleFile.instrument == config.instrument,
                SampleFile.instrument_type == "tof",
                SampleFile.datetime_utc >= start,
                SampleFile.datetime_utc < end,
            )
            .order_by(SampleFile.datetime_utc, SampleFile.filename)
        )
        rows = {}
        for sample_file_id, filename, acquired, mode_id in result.all():
            known = rows.get(sample_file_id)
            if known is None or (known.ionization_mode_id is None and mode_id):
                rows[sample_file_id] = FileRow(
                    sample_file_id, filename, acquired, mode_id
                )
    return sample_per_day(list(rows.values()), config.per_day)


# --- A mode's yardsticks ---


@dataclass
class ModeAudit:
    """Everything a mode's files are refitted and measured with."""

    mode: IonizationMode
    params: CalibrationFitParams
    #: Calibrant set name to its isotopes (labelled); a set that has none for
    #: the mode is left out, with the reason in ``notes``.
    sets: dict[str, pd.DataFrame]
    families: list[audit.Family]
    series: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    @property
    def polarity(self) -> str:
        return self.mode.ionization_mode_polarity


async def _collection_isotopes(
    filename: str, params: CalibrationFitParams, collection_id: str
) -> pd.DataFrame | None:
    """A collection's isotopes for a mode, read as the node reads them."""
    handler = TofCalibrationHandler(
        filename, params.model_copy(update={"calibration_collection_id": collection_id})
    )
    isotopes = await handler._resolve_calibration_isotopes()
    if isotopes is None:
        return None
    async with async_session() as session:
        formulas = dict(
            (
                await session.execute(
                    select(TargetIon.target_ion_id, TargetIon.target_ion_formula).where(
                        TargetIon.target_ion_id.in_(
                            isotopes.target_ion_id.unique().tolist()
                        )
                    )
                )
            ).all()
        )
    return audit.label_isotopes(isotopes, formulas)


async def _find_collection(reference: str) -> str | None:
    """A collection's id, from its id or its unique name."""
    async with async_session() as session:
        found = (
            (
                await session.execute(
                    select(TargetCollection.target_collection_id).where(
                        or_(
                            TargetCollection.target_collection_id == reference,
                            TargetCollection.target_collection_name == reference,
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    return found[0] if len(found) == 1 else None


async def prepare_mode(
    mode: IonizationMode, config: Config, filename: str
) -> ModeAudit:
    """The sets, families and series a mode's files are measured with.

    :param filename: One of the mode's files, which tells the instrument type
        the isotopes are read at.
    """
    params = CalibrationFitParams(
        calibration_collection_id=mode.calibration_collection_id,
        ionization_mechanism_ids=mode.ionization_mechanism_ids,
        polarity=mode.ionization_mode_polarity,
        **TofCalibrationParams().model_dump(),
    )
    async with async_session() as session:
        mechanisms = (
            (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism_id.in_(
                            mode.ionization_mechanism_ids or []
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

    notes = []
    sets = {}
    if mode.calibration_collection_id:
        isotopes = await _collection_isotopes(
            filename, params, mode.calibration_collection_id
        )
        if isotopes is None:
            notes.append("The mode's calibration collection has no isotopes for it.")
        else:
            sets[COLLECTION] = isotopes
    else:
        notes.append("The mode has no calibration collection.")
    for name, spec in config.sets.items():
        if isinstance(spec, dict):
            collection_id = await _find_collection(spec["collection"])
            if collection_id is None:
                notes.append(
                    f"Set '{name}': no one collection is '{spec['collection']}'."
                )
                continue
            isotopes = await _collection_isotopes(filename, params, collection_id)
        else:
            try:
                isotopes = audit.isotopes_of(spec, mechanisms)
            except ValueError as error:
                notes.append(f"Set '{name}': {error}")
                continue
        if isotopes is None or isotopes.empty:
            notes.append(f"Set '{name}' has no isotopes for this mode.")
            continue
        sets[name] = isotopes

    if config.families is None:
        family_mechanisms = list(mechanisms)
    else:
        family_mechanisms = [
            IonizationMechanism(
                ionization_mechanism_id=f"family{i}",
                ionization_mechanism=text,
                ionization_mechanism_polarity=text[-1],
            )
            for i, text in enumerate(config.families)
        ]
    neutrals = audit.cho_neutral_masses(config.family_dbe_max)
    families = []
    for mechanism in family_mechanisms:
        if mechanism.ionization_mechanism_polarity != mode.ionization_mode_polarity:
            continue
        family = audit.family_of(mechanism, neutrals)
        if family is None:
            notes.append(
                f"{mechanism.ionization_mechanism} adds no fixed adduct to a "
                "CHO neutral, so it has no family."
            )
        else:
            families.append(family)

    return ModeAudit(
        mode=mode,
        params=params,
        sets=sets,
        families=families,
        series=audit.series_isotopes(config.series, mode.ionization_mode_polarity),
        notes=notes,
    )


# --- One file ---


@dataclass
class FileAudit:
    """What one file's refits and evaluations came to."""

    row: FileRow
    #: Fit name to the fit, the stored record included, or None where the
    #: refit failed; and why.
    fits: dict[str, dict | None]
    warnings: dict[str, str | None]
    #: Fit name to the ion labels the fit kept (absent for the record).
    retained: dict[str, frozenset]
    #: Set name to the ion labels it matched in the file.
    matched: dict[str, frozenset]
    #: Ion label to (exact m/z, TOF of the matched peak).
    readings: dict[str, tuple[float, float]]
    #: Kept peaks of the polarity at the family's S/N, by TOF.
    strong_tofs: np.ndarray
    estimated: int
    reproduction: dict | None


def fit_names(mode_audit: ModeAudit, models: tuple[int, ...]) -> list[str]:
    """The refits a file gets, by name: set and model."""
    return [f"{name}/mode{model}" for name in mode_audit.sets for model in models]


def _node_record(record: dict | None) -> bool:
    """Whether a stored record is a fit the node made and an audit can redo."""
    return (
        isinstance(record, dict)
        and isinstance(record.get("quality"), dict)
        and record.get("mode") == 0
        and not is_unfitted_record(record)
    )


async def audit_file(row: FileRow, mode_audit: ModeAudit, config: Config) -> FileAudit:
    """Refit one file under every set and model, and read what each says."""
    stored = await asyncio.to_thread(audit.read_stored_peaks, row.filename)
    record = (await asyncio.to_thread(m_io.read_props, row.filename)).get(
        "mz_calibration"
    )
    _, resolution = await read_instrument_functions(
        row.filename, polarity=mode_audit.polarity
    )

    fits: dict[str, dict | None] = {}
    warnings: dict[str, str | None] = {}
    retained: dict[str, frozenset] = {}
    matched: dict[str, frozenset] = {}
    readings: dict[str, tuple[float, float]] = {}

    if isinstance(record, dict) and "mode" in record and "par" in record:
        fits[STORED] = {"mode": record["mode"], "par": record["par"]}
        warnings[STORED] = None

    def take(frame: pd.DataFrame) -> None:
        for label, mz, tof in zip(frame.ion_label, frame.mz, frame.sample_peak_tof):
            readings.setdefault(label, (float(mz), float(tof)))

    for set_name, isotopes in mode_audit.sets.items():
        for model in config.models:
            name = f"{set_name}/mode{model}"
            result = await audit.refit(
                stored, mode_audit.params, isotopes, resolution, model=model
            )
            fits[name] = result.fit
            warnings[name] = result.error or result.warning
            retained[name] = result.retained
            matched[set_name] = frozenset(result.readings.ion_label)
            take(result.readings)

    if not mode_audit.series.empty:
        take(
            await audit.match_only(
                stored, mode_audit.params, mode_audit.series, resolution
            )
        )

    reproduction = None
    if COLLECTION in mode_audit.sets and _node_record(record):
        quality = record["quality"]
        params = mode_audit.params.model_copy(
            update={
                "mz_error_tolerance": quality.get(
                    "mz_error_tolerance", mode_audit.params.mz_error_tolerance
                ),
                "refine_window": int(
                    quality.get("refine_window", mode_audit.params.refine_window)
                ),
            }
        )
        isotopes = mode_audit.sets[COLLECTION]
        result = await audit.refit(stored, params, isotopes, resolution)
        reproduction = reproduce(result, record, isotopes, params)

    peaks = stored.peaks
    strong = (peaks.polarity.values == mode_audit.polarity) & (
        peaks.signal_to_noise.values >= config.family_snr
    )
    return FileAudit(
        row=row,
        fits=fits,
        warnings=warnings,
        retained=retained,
        matched=matched,
        readings=readings,
        strong_tofs=np.asarray(peaks.tof.values[strong], dtype=np.float64),
        estimated=len(stored.estimated),
        reproduction=reproduction,
    )


def reproduce(
    result: audit.Refit,
    record: dict,
    isotopes: pd.DataFrame,
    params: CalibrationFitParams,
) -> dict:
    """How far a refit under the record's own settings lands from the record.

    Over the TOF span of the calibrants the refit kept. Where it did not land,
    the likeliest reason is said: a set that now offers fewer lines than the
    record kept was edited after the file was fitted.
    """
    n_record = record["quality"].get("n_points")
    offered = int((isotopes.relative_abundance >= params.isotope_abundance_min).sum())
    outcome = {
        "n_record": n_record,
        "n_refit": len(result.retained),
        "difference_ppm": None,
        "reproduced": False,
        "reason": None,
    }
    if result.fit is None:
        outcome["reason"] = f"no refit: {result.error or result.warning}"
        return outcome
    kept = result.readings[result.readings.ion_label.isin(result.retained)]
    difference = audit.axis_difference_ppm(
        result.fit,
        record,
        (float(kept.sample_peak_tof.min()), float(kept.sample_peak_tof.max())),
    )
    outcome["difference_ppm"] = difference
    outcome["reproduced"] = difference <= REPRODUCED_PPM
    if not outcome["reproduced"]:
        if n_record is not None and offered < n_record:
            outcome["reason"] = (
                f"the collection offers {offered} line(s) and the record kept "
                f"{n_record}: it was edited after the file was fitted"
            )
        elif n_record is not None and n_record != len(result.retained):
            outcome["reason"] = (
                f"the refit kept {len(result.retained)} point(s), the record {n_record}"
            )
        else:
            outcome["reason"] = "the same number of points, other peaks"
    return outcome


# --- Summaries ---


@dataclass
class ModeSummary:
    """What the files of one mode came to, fit by fit."""

    mode_name: str
    polarity: str
    fit_names: list[str]
    band_labels: list[str]
    notes: list[str]
    files: int = 0
    unreadable: Counter = field(default_factory=Counter)
    estimated_files: int = 0
    fitted: Counter = field(default_factory=Counter)
    failed: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    points: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    spans: dict[str, list] = field(default_factory=lambda: defaultdict(list))
    #: (fit, ion label) -> {"used": [...], "held_out": [...]}
    ion_errors: dict = field(
        default_factory=lambda: defaultdict(lambda: {"used": [], "held_out": []})
    )
    #: (set, ion label) -> files it matched in; (fit, label) -> files kept in
    matched: Counter = field(default_factory=Counter)
    kept: Counter = field(default_factory=Counter)
    set_files: Counter = field(default_factory=Counter)
    #: ion label -> exact m/z, and the series it belongs to, if any
    ion_mz: dict = field(default_factory=dict)
    ion_series: dict = field(default_factory=dict)
    ion_sets: dict = field(default_factory=lambda: defaultdict(set))
    #: (fit, band) -> {"used": [...], "held_out": [...]}
    band_errors: dict = field(
        default_factory=lambda: defaultdict(lambda: {"used": [], "held_out": []})
    )
    #: (fit, mechanism, band) -> offsets
    family: dict = field(default_factory=lambda: defaultdict(list))
    reproduction: list = field(default_factory=list)


def summarise_file(
    summary: ModeSummary, file: FileAudit, mode_audit: ModeAudit, config: Config
) -> None:
    """Add one file's evaluations to its mode's summary."""
    summary.files += 1
    summary.estimated_files += bool(file.estimated)
    for set_name, labels in file.matched.items():
        summary.set_files[set_name] += 1
        for label in labels:
            summary.matched[(set_name, label)] += 1
    for label, (mz, _) in file.readings.items():
        summary.ion_mz[label] = mz
    if not mode_audit.series.empty:
        for label, name, n in zip(
            mode_audit.series.ion_label,
            mode_audit.series.series,
            mode_audit.series.series_n,
        ):
            summary.ion_series[label] = (name, int(n))
    for set_name, isotopes in mode_audit.sets.items():
        for label in isotopes.ion_label:
            summary.ion_sets[label].add(set_name)

    labels = list(file.readings)
    exact = np.array([file.readings[label][0] for label in labels], dtype=np.float64)
    tofs = np.array([file.readings[label][1] for label in labels], dtype=np.float64)
    ion_bands = audit.band_of(exact, config.band_edges)

    for name in summary.fit_names:
        fit = file.fits.get(name)
        if fit is None:
            # A file with no record has no stored fit to fail
            if name in file.fits:
                summary.failed[name][file.warnings.get(name) or "no fit"] += 1
            continue
        summary.fitted[name] += 1
        used_labels = file.retained.get(name)
        if used_labels is not None:
            summary.points[name].append(len(used_labels))
            kept_mz = [file.readings[label][0] for label in used_labels]
            if kept_mz:
                summary.spans[name].append((min(kept_mz), max(kept_mz)))
            for label in used_labels:
                summary.kept[(name, label)] += 1
        if labels:
            errors = audit.ppm_error(tofs, exact, fit)
            for label, error, band in zip(labels, errors, ion_bands):
                bucket = (
                    "used"
                    if used_labels is not None and label in used_labels
                    else "held_out"
                )
                summary.ion_errors[(name, label)][bucket].append(float(error))
                summary.band_errors[(name, int(band))][bucket].append(float(error))
        if file.strong_tofs.size:
            strong_mz = np.asarray(
                tof_to_mass(file.strong_tofs, fit["mode"], fit["par"]), dtype=np.float64
            )
            for family in mode_audit.families:
                mz, offsets = family.offsets_ppm(strong_mz, config.family_window_ppm)
                for band, offset in zip(audit.band_of(mz, config.band_edges), offsets):
                    summary.family[(name, family.mechanism, int(band))].append(
                        float(offset)
                    )
    if file.reproduction is not None:
        summary.reproduction.append(
            {"sample_file_id": file.row.sample_file_id, **file.reproduction}
        )


def summary_dict(summary: ModeSummary) -> dict:
    """A mode's summary as plain data, for the report and the JSON."""
    fits = {}
    for name in summary.fit_names:
        spans = summary.spans.get(name) or []
        fits[name] = {
            "fitted": summary.fitted[name],
            "failed": dict(summary.failed[name]),
            "points": audit.spread(summary.points.get(name) or []),
            "span": (
                [
                    float(np.median([s[0] for s in spans])),
                    float(np.median([s[1] for s in spans])),
                ]
                if spans
                else None
            ),
            "ions": [],
            "bands": [],
        }
        for label in sorted(summary.ion_mz, key=lambda label: summary.ion_mz[label]):
            errors = summary.ion_errors.get((name, label))
            if errors is None:
                continue
            set_name = name.split("/")[0]
            in_set = set_name in summary.ion_sets.get(label, set())
            fits[name]["ions"].append(
                {
                    "ion": label,
                    "mz": summary.ion_mz[label],
                    "series": summary.ion_series.get(label),
                    "calibrant": in_set,
                    "presence": (
                        summary.matched[(set_name, label)] / summary.set_files[set_name]
                        if in_set and summary.set_files[set_name]
                        else None
                    ),
                    "kept": (
                        summary.kept[(name, label)] / summary.fitted[name]
                        if in_set and summary.fitted[name]
                        else None
                    ),
                    "used": audit.spread(errors["used"]),
                    "held_out": audit.spread(errors["held_out"]),
                }
            )
        for index, band in enumerate(summary.band_labels):
            errors = summary.band_errors.get(
                (name, index), {"used": [], "held_out": []}
            )
            families = {
                mechanism: audit.spread(offsets)
                for (fit, mechanism, band_index), offsets in summary.family.items()
                if fit == name and band_index == index
            }
            if (
                not errors["used"]
                and not errors["held_out"]
                and not any(families.values())
            ):
                continue
            fits[name]["bands"].append(
                {
                    "band": band,
                    "used": audit.spread(errors["used"]),
                    "held_out": audit.spread(errors["held_out"]),
                    "families": families,
                }
            )
    checked = summary.reproduction
    reproduced = [entry for entry in checked if entry["reproduced"]]
    differences = [
        entry["difference_ppm"]
        for entry in checked
        if entry["difference_ppm"] is not None
    ]
    return {
        "mode": summary.mode_name,
        "polarity": summary.polarity,
        "files": summary.files,
        "unreadable": dict(summary.unreadable),
        "estimated_files": summary.estimated_files,
        "notes": summary.notes,
        "reproduction": {
            "checked": len(checked),
            "reproduced": len(reproduced),
            "worst_ppm": max(differences) if differences else None,
            "mismatches": [entry for entry in checked if not entry["reproduced"]],
        },
        "fits": fits,
    }


# --- The report ---


def _ppm(stats: dict | None) -> str:
    if stats is None:
        return "-"
    return f"{stats['median']:+.1f} (IQR {stats['iqr']:.1f}, n {stats['n']})"


def _share(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def log_summary(summary: dict, config: Config) -> None:
    """Say what one mode's files came to."""
    log = runtime.logger.info
    log(
        f"- Mode '{summary['mode']}' ({summary['polarity']}): {summary['files']} "
        f"file(s) audited."
    )
    if summary["unreadable"]:
        log(
            "    Could not be read: "
            + ", ".join(f"{n} ({kind})" for kind, n in summary["unreadable"].items())
        )
    if summary["estimated_files"]:
        log(
            f"    {summary['estimated_files']} file(s) had candidate peaks with no "
            "stored timeseries; they were ranked by their stored sum, where the "
            "node would have computed the timeseries."
        )
    for note in summary["notes"]:
        log(f"    {note}")

    reproduction = summary["reproduction"]
    if reproduction["checked"]:
        worst = reproduction["worst_ppm"]
        log(
            f"    Node fits reproduced to {REPRODUCED_PPM} ppm: "
            f"{reproduction['reproduced']} of {reproduction['checked']}"
            + (f" (largest difference {worst:.4f} ppm)" if worst is not None else "")
        )
        for entry in reproduction["mismatches"][:_MISMATCHES_LISTED]:
            difference = entry["difference_ppm"]
            log(
                f"      {entry['sample_file_id']}: "
                + (f"{difference:.3f} ppm, " if difference is not None else "")
                + f"{entry['reason']}"
            )
        if len(reproduction["mismatches"]) > _MISMATCHES_LISTED:
            log(
                f"      ...and {len(reproduction['mismatches']) - _MISMATCHES_LISTED} more."
            )
    elif COLLECTION in summary["fits"]:
        log("    No file carries a node fit to reproduce.")

    for name, fit in summary["fits"].items():
        failed = ", ".join(f"{n} {reason}" for reason, n in fit["failed"].items())
        span = fit["span"]
        log(
            f"    Fit {name}: {fit['fitted']} fitted"
            + (f"; failed: {failed}" if failed else "")
            + (
                f"; points {fit['points']['median']:g} (median)"
                if fit["points"]
                else ""
            )
            + (
                f"; calibrants span {span[0]:.0f}-{span[1]:.0f} Th (median)"
                if span
                else ""
            )
        )
        for ion in fit["ions"]:
            series = ion["series"]
            what = (
                f"series {series[0]} n={series[1]}"
                if series
                else ("calibrant" if ion["calibrant"] else "other set")
            )
            log(
                f"      {ion['ion']:<32} {what:<26} present {_share(ion['presence']):>4}"
                f" kept {_share(ion['kept']):>4} | used {_ppm(ion['used'])}"
                f" | held out {_ppm(ion['held_out'])}"
            )
        for band in fit["bands"]:
            held_out = band["held_out"]
            log(
                f"      {band['band']:>8} Th: used {_ppm(band['used'])}; held out "
                f"{_ppm(held_out)}"
                + (f", RMS {held_out['rms']:.1f}" if held_out else "")
            )
            for mechanism, offsets in band["families"].items():
                log(f"               family CHO under {mechanism}: {_ppm(offsets)}")
    log(
        "    Errors in ppm of the exact m/z, as median (IQR, count) over the files; "
        "'used' where the fit kept the ion, 'held out' where it did not. The "
        "stored record keeps no list of what it was fitted on, so all of its "
        "errors count as held out."
    )


async def run_audit(config: Config) -> dict:
    """Audit the instrument's files, log the report, and return it."""
    rows = await select_files(config)
    log = runtime.logger.info
    log(
        f"TOF calibration audit of {config.instrument}: {len(rows)} file(s) acquired "
        f"{config.first_day} to {config.last_day} (UTC)"
        + (f", {config.per_day} per day" if config.per_day else "")
        + f"; models {', '.join(str(m) for m in config.models)}."
    )
    by_mode = defaultdict(list)
    for row in rows:
        by_mode[row.ionization_mode_id].append(row)

    report = {
        "instrument": config.instrument,
        "from": config.first_day.isoformat(),
        "to": config.last_day.isoformat(),
        "per_day": config.per_day,
        "models": list(config.models),
        "modes": [],
        "files": [],
    }
    report["without_mode"] = len(by_mode.pop(None, []))
    if report["without_mode"]:
        log(f"  {report['without_mode']} file(s) have no ionization mode; left out.")

    gate = asyncio.Semaphore(_FILE_CONCURRENCY)
    for mode_id, mode_rows in by_mode.items():
        async with async_session() as session:
            mode = await session.get(IonizationMode, mode_id)
        if mode is None:
            continue
        if config.mode is not None and mode.ionization_mode_name != config.mode:
            continue
        mode_audit = await prepare_mode(mode, config, mode_rows[0].filename)
        names = [STORED, *fit_names(mode_audit, config.models)]
        summary = ModeSummary(
            mode_name=mode.ionization_mode_name,
            polarity=mode.ionization_mode_polarity,
            fit_names=names,
            band_labels=audit.band_labels(config.band_edges),
            notes=mode_audit.notes,
        )

        async def one(row: FileRow) -> FileAudit | None:
            async with gate:
                try:
                    return await audit_file(row, mode_audit, config)
                except Exception as error:  # noqa: BLE001 - one file's failure
                    summary.unreadable[type(error).__name__] += 1
                    runtime.logger.opt(exception=True).debug(
                        f"Could not audit {row.filename}"
                    )
                    return None

        for file in await asyncio.gather(*(one(row) for row in mode_rows)):
            if file is None:
                continue
            summarise_file(summary, file, mode_audit, config)
            report["files"].append(
                {
                    "sample_file_id": file.row.sample_file_id,
                    "acquired": file.row.acquired.isoformat(),
                    "mode": mode.ionization_mode_name,
                    "fits": {
                        name: {
                            "fit": fit,
                            "warning": file.warnings.get(name),
                            "points": len(file.retained.get(name, ())) or None,
                        }
                        for name, fit in file.fits.items()
                    },
                    "estimated_peaks": file.estimated,
                    "reproduction": file.reproduction,
                }
            )
        mode_report = summary_dict(summary)
        log_summary(mode_report, config)
        report["modes"].append(mode_report)
    return report


async def run() -> None:
    """Read the configuration, initialise the database and run the audit."""
    config = read_config()
    await configure_database_engine()
    report = await run_audit(config)
    if config.as_json:
        # After the report: the log is written from a queue, and the
        # document is to be the last line. On a line of its own, since the
        # log's last record leaves its colour reset at the start of the next.
        await runtime.logger.complete()
        sys.stdout.write("\n" + json.dumps(report, default=str) + "\n")
        sys.stdout.flush()


def main() -> None:
    """Entry point for ``mascope dev|prod db script run``."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
